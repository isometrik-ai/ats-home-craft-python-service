"""Move events business logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.move_events_repository import (
    MoveEventsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import (
    ContactType,
    ContactUnitStatus,
    MoveEventType,
)
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    MoveEventDocumentResponse,
    MoveEventResponse,
    UpdateMoveEventRequest,
)
from apps.user_service.app.schemas.tenant_requests import TenantRequestDocumentInput
from apps.user_service.app.services.inventory_service import resolve_is_sold
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    recipient_language_from_contact,
    unit_label_from_row,
)
from apps.user_service.app.services.tenant_requests_service import TenantRequestsService
from apps.user_service.app.services.unit_occupancy_turnover_service import (
    UnitOccupancyTurnoverService,
)
from apps.user_service.app.services.units_service import format_contact_display_name
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_any,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class MoveEventsService:
    """Community-admin move-in / move-out operations."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        move_events_repository: MoveEventsRepository | None = None,
        contact_units_repository: ContactUnitsRepository | None = None,
        contact_roles_repository: ContactRolesRepository | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.move_events_repo = move_events_repository or MoveEventsRepository(db_connection)
        self.contact_units_repo = contact_units_repository or ContactUnitsRepository(db_connection)
        self.contact_roles_repo = contact_roles_repository or ContactRolesRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self._push_dispatcher: PushNotificationDispatcher | None = None

    def _push(self) -> PushNotificationDispatcher:
        if self._push_dispatcher is None:
            self._push_dispatcher = PushNotificationDispatcher(db_connection=self.db_connection)
        return self._push_dispatcher

    async def _notify_move_recorded(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        move_event_id: str,
        move_type: str,
        moving_contact_id: str,
        unit_label: str,
    ) -> None:
        """Notify the moving contact and the unit Owner; dedupe by linked user_id."""
        contact_ids: list[str] = [moving_contact_id]
        occupants_by_unit = await self.units_repo.get_unit_role_occupants_batch(
            organization_id=organization_id,
            unit_ids=[unit_id],
        )
        owner = (occupants_by_unit.get(unit_id) or {}).get("owner")
        if owner and owner.get("contact_id"):
            contact_ids.append(str(owner["contact_id"]))

        unique_contact_ids = list(
            dict.fromkeys(contact_id for contact_id in contact_ids if contact_id)
        )
        if not unique_contact_ids:
            return

        params = {
            "move_type": move_type.replace("_", " "),
            "unit_label": unit_label,
        }
        data = {
            "move_event_id": move_event_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "screen": "move_event_detail",
        }
        options = {
            "click_action": "OPEN_MOVE",
            "idempotency_key": f"move:{move_event_id}:recorded",
        }
        contacts_repo = self._push().contacts_repo
        user_ids_seen: set[str] = set()
        for contact_id in unique_contact_ids:
            contact = await contacts_repo.get_contact_for_update(
                contact_id=contact_id,
                organization_id=organization_id,
            )
            if not contact:
                continue
            recipient_user_id = str(contact.get("user_id") or "").strip()
            if not recipient_user_id or recipient_user_id in user_ids_seen:
                continue
            user_ids_seen.add(recipient_user_id)
            await self._push().send_to_user(
                organization_id=organization_id,
                recipient_user_id=recipient_user_id,
                message_key="notifications.push.move.recorded",
                notification_type="NOTIFICATION_TYPE_MOVE",
                feed_type="move",
                language=recipient_language_from_contact(contact.get("additional_data")),
                params=params,
                data=data,
                entity={"kind": "move_event", "id": move_event_id},
                options=options,
                check_push_preference=True,
            )

    @staticmethod
    def _format_date(value: Any) -> str:
        """Format a date value for API responses."""
        if value is None:
            return ""
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _format_decimal(value: Any) -> str | None:
        """Format numeric fee for API responses."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return format(value, "f")
        return str(value)

    @staticmethod
    def _documents_to_json(
        documents: list[TenantRequestDocumentInput] | None,
    ) -> list[dict[str, str | None]]:
        """Serialize typed document inputs for jsonb storage."""
        if not documents:
            return []
        return [
            {
                "document_type": item.document_type.value,
                "file_path": item.file_path,
                "file_name": item.file_name,
            }
            for item in documents
        ]

    @staticmethod
    def _documents_from_row(value: Any) -> list[MoveEventDocumentResponse]:
        """Parse documents jsonb into API response models."""
        raw_items = parse_json_any(value, default=[]) or []
        documents: list[MoveEventDocumentResponse] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            document_type = str(item.get("document_type") or "").strip()
            file_path = str(item.get("file_path") or "").strip()
            if not document_type or not file_path:
                continue
            documents.append(
                MoveEventDocumentResponse(
                    document_type=document_type,
                    file_path=file_path,
                    file_name=item.get("file_name"),
                    status=item.get("status"),
                )
            )
        return documents

    def _serialize_row(self, row: dict[str, Any]) -> MoveEventResponse:
        """Map a DB row to the API response model."""
        return MoveEventResponse(
            id=row["id"],
            organization_id=row["organization_id"],
            project_id=row["project_id"],
            unit_id=row["unit_id"],
            contact_id=row["contact_id"],
            contact_unit_id=row.get("contact_unit_id"),
            move_type=row["move_type"],
            event_date=self._format_date(row.get("event_date")),
            fee_amount=self._format_decimal(row.get("fee_amount")),
            fee_currency=row.get("fee_currency") or "INR",
            notes=row.get("notes"),
            documents=self._documents_from_row(row.get("documents")),
            recorded_by_user_id=row.get("recorded_by_user_id"),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
            unit_code=row.get("unit_code"),
            unit_label=row.get("unit_label"),
            unit_tower_name=row.get("unit_tower_name"),
            unit_type=row.get("unit_type"),
            contact_name=format_contact_display_name(
                prefix=row.get("contact_prefix"),
                first_name=row.get("contact_first_name"),
                last_name=row.get("contact_last_name"),
            ),
            contact_role=row.get("contact_role"),
        )

    async def _sync_occupancy_for_move(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        contact_id: str,
        unit_id: str,
        move_type: str,
        event_date: date,
    ) -> None:
        """Apply contact_units occupancy sync for a move-in."""
        if move_type != MoveEventType.MOVE_IN.value:
            return
        await self.contact_units_repo.sync_move_in(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
            event_date=event_date,
        )

    async def _release_occupancy_for_move_out(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
    ) -> None:
        """Clear household artifacts when a contact moves out of a unit."""
        turnover_service = UnitOccupancyTurnoverService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        active_tenant_id = await self.contact_roles_repo.get_active_tenant_contact_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if active_tenant_id and str(active_tenant_id) == str(contact_id):
            await turnover_service.release_outgoing_tenant_household(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
                reason="Admin move-out; clearing outgoing household.",
            )
            return
        await turnover_service.release_single_occupant(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_id=contact_id,
            reason="Admin move-out; clearing occupant.",
        )

    async def _resolve_contact_unit(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        move_type: str,
    ) -> str:
        """Return contact_unit_id, creating a link on move-in when missing."""
        link = await self.contact_units_repo.get_by_unit_and_contact(
            organization_id=organization_id,
            unit_id=unit_id,
            contact_id=contact_id,
        )
        if link:
            return link["id"]

        if move_type == MoveEventType.MOVE_OUT.value:
            raise ValidationException(
                message_key="move_events.errors.not_currently_occupying",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        created = await self.contact_units_repo.insert_allotment(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_id=contact_id,
            is_primary=False,
            relationship="self",
            status=ContactUnitStatus.PENDING.value,
        )
        return created["id"]

    async def _assert_move_in_allowed(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
    ) -> None:
        """Reject move-in when the unit is unsold or already has an active tenant."""
        unit_row = await self.units_repo.get_unit_detail_base(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not unit_row:
            raise NotFoundException(
                message_key="move_events.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        owner = await self.units_repo.get_unit_owner_contact(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        owner_contact_id = str(owner["contact_id"]) if owner and owner.get("contact_id") else None
        if not resolve_is_sold(
            status=str(unit_row.get("status") or ""),
            owner_contact_id=owner_contact_id,
        ):
            raise ValidationException(
                message_key="move_events.errors.unit_not_sold",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        tenant_id = await self.contact_roles_repo.get_active_tenant_contact_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if tenant_id:
            raise ValidationException(
                message_key="move_events.errors.unit_occupied_by_other_tenant",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _ensure_tenant_role_for_move_in(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Assign a Tenant role on move-in when the contact is not already Owner/Tenant."""
        roles = await self.contact_roles_repo.list_active_roles_for_contact(
            organization_id=organization_id,
            contact_id=contact_id,
        )
        for role in roles:
            if str(role.get("unit_id") or "") != unit_id:
                continue
            role_type = str(role.get("role_type") or "")
            if role_type in {ContactType.OWNER.value, ContactType.TENANT.value}:
                return

        await self.contact_roles_repo.end_active_roles_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
            role_types=[ContactType.TENANT.value],
        )
        await self.contact_roles_repo.insert_tenant_role(
            organization_id=organization_id,
            contact_id=contact_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_unit_id=contact_unit_id,
        )

    async def _record_move_out(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        event_date: date,
        notes: str | None = None,
        fee_amount: Decimal | None = None,
        fee_currency: str = "INR",
        send_notification: bool = True,
    ) -> dict[str, Any]:
        """Insert a move-out ledger row and run turnover + tenant-request sync."""
        contact_unit_id = await self._resolve_contact_unit(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_id=contact_id,
            move_type=MoveEventType.MOVE_OUT.value,
        )

        inserted = await self.move_events_repo.insert(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "unit_id": unit_id,
                "contact_id": contact_id,
                "contact_unit_id": contact_unit_id,
                "move_type": MoveEventType.MOVE_OUT.value,
                "event_date": event_date,
                "fee_amount": fee_amount,
                "fee_currency": fee_currency,
                "notes": notes,
                "documents": [],
                "recorded_by_user_id": self.user_context.user_id,
            }
        )

        await self._release_occupancy_for_move_out(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_id=str(contact_id),
        )
        tenant_requests_service = TenantRequestsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await tenant_requests_service.sync_after_admin_move_out(
            unit_id=unit_id,
            tenant_contact_id=str(contact_id),
            move_event_id=str(inserted["id"]),
        )

        if send_notification:
            row = await self.move_events_repo.get_by_id(
                organization_id=organization_id,
                move_event_id=inserted["id"],
            )
            if row:
                await self._notify_move_recorded(
                    organization_id=organization_id,
                    project_id=project_id,
                    unit_id=unit_id,
                    move_event_id=str(row.get("id") or inserted["id"]),
                    move_type=MoveEventType.MOVE_OUT.value,
                    moving_contact_id=str(contact_id),
                    unit_label=unit_label_from_row(
                        {
                            "unit_id": unit_id,
                            "unit_label": row.get("unit_label"),
                            "unit_code": row.get("unit_code"),
                        }
                    ),
                )
        return inserted

    async def record_tenant_move_out_for_owner_change(
        self,
        *,
        unit_id: str,
        project_id: str,
        notes: str,
    ) -> str | None:
        """Record admin move-out for the active tenant when owner occupancy changes."""
        organization_id = self.user_context.organization_id
        assert organization_id

        tenant_contact_id = await self.contact_roles_repo.get_active_tenant_contact_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        if not tenant_contact_id:
            return None

        tenant_contact_id = str(tenant_contact_id)
        has_active = await self.contact_units_repo.contact_has_active_unit(
            organization_id=organization_id,
            contact_id=tenant_contact_id,
            unit_id=unit_id,
        )
        if not has_active:
            return None

        inserted = await self._record_move_out(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            contact_id=tenant_contact_id,
            event_date=datetime.now(timezone.utc).date(),
            notes=notes,
            send_notification=False,
        )
        return str(inserted["id"])

    async def create_move_event(self, body: CreateMoveEventRequest) -> MoveEventResponse:
        """Record a move-in or move-out and sync occupancy."""
        organization_id = self.user_context.organization_id

        unit = await self.contact_units_repo.get_unit_project(
            organization_id=organization_id,
            unit_id=body.unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="move_events.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not await self.move_events_repo.contact_exists(
            organization_id=organization_id,
            contact_id=body.contact_id,
        ):
            raise NotFoundException(
                message_key="move_events.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        move_type = body.move_type.value
        project_id = str(unit["project_id"])
        if move_type == MoveEventType.MOVE_IN.value:
            await self._assert_move_in_allowed(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=body.unit_id,
            )
            turnover_service = UnitOccupancyTurnoverService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            await turnover_service.release_outgoing_tenant_household(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=body.unit_id,
                reason="Admin move-in; clearing stale household.",
            )
        if move_type == MoveEventType.MOVE_OUT.value:
            has_active = await self.contact_units_repo.contact_has_active_unit(
                organization_id=organization_id,
                contact_id=body.contact_id,
                unit_id=body.unit_id,
            )
            if not has_active:
                raise ValidationException(
                    message_key="move_events.errors.not_currently_occupying",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

        contact_unit_id = await self._resolve_contact_unit(
            organization_id=organization_id,
            project_id=unit["project_id"],
            unit_id=body.unit_id,
            contact_id=body.contact_id,
            move_type=move_type,
        )

        if move_type == MoveEventType.MOVE_OUT.value:
            inserted = await self._record_move_out(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=body.unit_id,
                contact_id=body.contact_id,
                event_date=body.event_date,
                notes=body.notes,
                fee_amount=body.fee_amount,
                fee_currency=body.fee_currency,
                send_notification=False,
            )
        else:
            inserted = await self.move_events_repo.insert(
                {
                    "organization_id": organization_id,
                    "project_id": unit["project_id"],
                    "unit_id": body.unit_id,
                    "contact_id": body.contact_id,
                    "contact_unit_id": contact_unit_id,
                    "move_type": move_type,
                    "event_date": body.event_date,
                    "fee_amount": body.fee_amount,
                    "fee_currency": body.fee_currency,
                    "notes": body.notes,
                    "documents": self._documents_to_json(body.documents),
                    "recorded_by_user_id": self.user_context.user_id,
                }
            )

        await self._sync_occupancy_for_move(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
            contact_id=body.contact_id,
            unit_id=body.unit_id,
            move_type=move_type,
            event_date=body.event_date,
        )
        if move_type == MoveEventType.MOVE_IN.value:
            await self._ensure_tenant_role_for_move_in(
                organization_id=organization_id,
                project_id=str(unit["project_id"]),
                unit_id=body.unit_id,
                contact_id=body.contact_id,
                contact_unit_id=contact_unit_id,
            )
            tenant_requests_service = TenantRequestsService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            await tenant_requests_service.sync_after_admin_move_in(
                project_id=str(unit["project_id"]),
                unit_id=body.unit_id,
                tenant_contact_id=str(body.contact_id),
                contact_unit_id=str(contact_unit_id),
                move_in_date=body.event_date,
                move_in_fee=body.fee_amount or Decimal("0"),
                move_event_id=str(inserted["id"]),
            )

        row = await self.move_events_repo.get_by_id(
            organization_id=organization_id,
            move_event_id=inserted["id"],
        )
        if not row:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self._notify_move_recorded(
            organization_id=organization_id,
            project_id=str(unit["project_id"]),
            unit_id=body.unit_id,
            move_event_id=str(row.get("id") or inserted["id"]),
            move_type=move_type,
            moving_contact_id=str(body.contact_id),
            unit_label=unit_label_from_row(
                {
                    "unit_id": body.unit_id,
                    "unit_label": row.get("unit_label"),
                    "unit_code": row.get("unit_code"),
                }
            ),
        )
        return self._serialize_row(row)

    async def list_move_events(
        self,
        *,
        bucket: str | None = None,
        search: str | None = None,
        unit_id: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MoveEventResponse], int]:
        """List move events for the organization."""
        rows, total = await self.move_events_repo.list(
            organization_id=self.user_context.organization_id,
            bucket=bucket,
            search=search,
            unit_id=unit_id,
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
        return [self._serialize_row(row) for row in rows], total

    async def get_move_event(self, move_event_id: str) -> MoveEventResponse:
        """Fetch one move event."""
        row = await self.move_events_repo.get_by_id(
            organization_id=self.user_context.organization_id,
            move_event_id=move_event_id,
        )
        if not row:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_row(row)

    async def update_move_event(
        self,
        move_event_id: str,
        body: UpdateMoveEventRequest,
    ) -> MoveEventResponse:
        """Patch allowed move event fields."""
        organization_id = self.user_context.organization_id
        existing = await self.move_events_repo.get_by_id(
            organization_id=organization_id,
            move_event_id=move_event_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        update_data = body.model_dump(exclude_unset=True)
        if "documents" in update_data:
            update_data["documents"] = self._documents_to_json(
                body.documents,
            )
        if not update_data:
            return self._serialize_row(existing)

        if "fee_amount" in update_data and update_data["fee_amount"] is not None:
            if update_data["fee_amount"] < 0:
                raise ValidationException(
                    message_key="move_events.errors.invalid_fee",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

        updated = await self.move_events_repo.update(
            organization_id=organization_id,
            move_event_id=move_event_id,
            update_data=update_data,
        )
        if not updated:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if "event_date" in update_data and existing.get("contact_unit_id"):
            await self._sync_occupancy_for_move(
                organization_id=organization_id,
                contact_unit_id=existing["contact_unit_id"],
                contact_id=str(existing["contact_id"]),
                unit_id=str(existing["unit_id"]),
                move_type=existing["move_type"],
                event_date=update_data["event_date"],
            )

        return self._serialize_row(updated)

    async def delete_move_event(self, move_event_id: str) -> MoveEventResponse:
        """Soft-void a move event and re-derive occupancy from prior moves."""
        organization_id = self.user_context.organization_id
        existing = await self.move_events_repo.get_by_id(
            organization_id=organization_id,
            move_event_id=move_event_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        deleted = await self.move_events_repo.soft_delete(
            organization_id=organization_id,
            move_event_id=move_event_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key="move_events.errors.move_event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        latest = await self.move_events_repo.get_latest_for_unit_contact(
            organization_id=organization_id,
            unit_id=existing["unit_id"],
            contact_id=existing["contact_id"],
        )
        contact_unit_id = existing.get("contact_unit_id")
        if latest and contact_unit_id:
            await self._sync_occupancy_for_move(
                organization_id=organization_id,
                contact_unit_id=contact_unit_id,
                contact_id=str(existing["contact_id"]),
                unit_id=str(existing["unit_id"]),
                move_type=latest["move_type"],
                event_date=latest["event_date"],
            )

        return self._serialize_row(existing)
