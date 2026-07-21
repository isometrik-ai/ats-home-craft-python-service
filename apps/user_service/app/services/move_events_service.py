"""Move events business logic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.move_events_repository import (
    MoveEventsRepository,
)
from apps.user_service.app.schemas.enums import ContactUnitStatus, MoveEventType
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    MoveEventResponse,
    UpdateMoveEventRequest,
)
from apps.user_service.app.services.units_service import format_contact_display_name
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
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
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.move_events_repo = move_events_repository or MoveEventsRepository(db_connection)
        self.contact_units_repo = contact_units_repository or ContactUnitsRepository(db_connection)

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
            document_paths=list(row.get("document_paths") or []),
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
        move_type: str,
        event_date: date,
    ) -> None:
        """Apply contact_units occupancy sync for a move type."""
        if move_type == MoveEventType.MOVE_IN.value:
            await self.contact_units_repo.sync_move_in(
                organization_id=organization_id,
                contact_unit_id=contact_unit_id,
                event_date=event_date,
            )
            return
        await self.contact_units_repo.sync_move_out(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
            event_date=event_date,
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
                "document_paths": body.document_paths,
                "recorded_by_user_id": self.user_context.user_id,
            }
        )

        await self._sync_occupancy_for_move(
            organization_id=organization_id,
            contact_unit_id=contact_unit_id,
            move_type=move_type,
            event_date=body.event_date,
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
                move_type=latest["move_type"],
                event_date=latest["event_date"],
            )

        return self._serialize_row(existing)
