"""Tenant request business logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError
from supabase import AsyncClient

from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import (
    ContactsRepository,
)
from apps.user_service.app.db.repositories.move_events_repository import (
    MoveEventsRepository,
)
from apps.user_service.app.db.repositories.tenant_requests_repository import (
    TenantRequestsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import (
    ContactType,
    MoveEventType,
    TenantRequestDocumentStatus,
    TenantRequestDocumentType,
    TenantRequestEventType,
    TenantRequestListBucket,
    TenantRequestStatus,
)
from apps.user_service.app.schemas.tenant_requests import (
    ApproveTenantRequestRequest,
    CreateTenantRequestRequest,
    OwnerTenantRequestListQuery,
    RejectTenantDocumentRequest,
    ReuploadTenantDocumentRequest,
    TenantRequestDocumentResponse,
    TenantRequestEventResponse,
    TenantRequestListItemResponse,
    TenantRequestListQuery,
    TenantRequestMilestoneResponse,
    TenantRequestResponse,
    TenantRequestSummaryResponse,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    unit_label_from_row,
)
from apps.user_service.app.services.unit_occupancy_turnover_service import (
    UnitOccupancyTurnoverService,
)
from apps.user_service.app.services.units_service import (
    format_contact_display_name,
    format_primary_contact_email,
    format_primary_contact_phone,
    serialize_unit_list_item,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_any,
)
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_INFLIGHT_STATUSES = {
    TenantRequestStatus.DRAFT.value,
    TenantRequestStatus.SUBMITTED.value,
    TenantRequestStatus.PENDING_REVIEW.value,
    TenantRequestStatus.AWAITING_RESUBMISSION.value,
    TenantRequestStatus.READY_TO_APPROVE.value,
}

_TENANT_REQUEST_DOCUMENT_LABELS: dict[str, str] = {
    TenantRequestDocumentType.ID_PROOF.value: "ID proof",
    TenantRequestDocumentType.RENTAL_AGREEMENT.value: "Rental agreement",
    TenantRequestDocumentType.POLICE_VERIFICATION.value: "Police verification",
}


class TenantRequestsService:
    """Owner submit + admin review workflow for unit tenants."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
        tenant_requests_repository: TenantRequestsRepository | None = None,
        contact_units_repository: ContactUnitsRepository | None = None,
        move_events_repository: MoveEventsRepository | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.repo = tenant_requests_repository or TenantRequestsRepository(db_connection)
        self.contact_units_repo = contact_units_repository or ContactUnitsRepository(db_connection)
        self.move_events_repo = move_events_repository or MoveEventsRepository(db_connection)
        self.contact_roles_repo = ContactRolesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self._push_dispatcher: PushNotificationDispatcher | None = None

    def _push(self) -> PushNotificationDispatcher:
        """Return the push dispatcher, creating it on first use."""
        if self._push_dispatcher is None:
            self._push_dispatcher = PushNotificationDispatcher(db_connection=self.db_connection)
        return self._push_dispatcher

    async def _record_move_event_ledger(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        contact_unit_id: str | None,
        move_type: str,
        event_date: date,
        fee_amount: Decimal | None = None,
        notes: str | None = None,
        documents: list[dict[str, Any]] | None = None,
    ) -> None:
        """Insert a move-in/out ledger row without re-syncing contact_units."""
        user_id = self.user_context.user_id
        await self.move_events_repo.insert(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "unit_id": unit_id,
                "contact_id": contact_id,
                "contact_unit_id": contact_unit_id,
                "move_type": move_type,
                "event_date": event_date,
                "fee_amount": fee_amount,
                "fee_currency": "INR",
                "notes": notes,
                "documents": documents or [],
                "recorded_by_user_id": str(user_id) if user_id else None,
            }
        )

    @staticmethod
    def _snapshot_tenant_request_documents(
        documents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Copy tenant request document metadata into move_events.documents jsonb."""
        return [
            {
                "document_type": str(doc.get("document_type") or ""),
                "file_path": str(doc.get("file_path") or ""),
                "file_name": doc.get("file_name"),
                "status": doc.get("status"),
            }
            for doc in documents
            if doc.get("document_type") and doc.get("file_path")
        ]

    async def _ensure_project(self, *, project_id: str) -> None:
        """Raise when the project is missing or outside the organization."""
        await self.setup_service.ensure_project(project_id=project_id)

    async def _get_admin_request_or_raise(
        self,
        *,
        project_id: str,
        tenant_request_id: str,
    ) -> dict[str, Any]:
        """Fetch a tenant request scoped to a project or raise not found."""
        await self._ensure_project(project_id=project_id)
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        if str(row["project_id"]) != project_id:
            raise NotFoundException(
                message_key="tenant_requests.errors.request_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    @staticmethod
    def _format_date(value: Any) -> str | None:
        """Format a date value for API responses."""
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _format_decimal(value: Any) -> str | None:
        """Format a decimal value for API responses."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return format(value, "f")
        return str(value)

    @staticmethod
    def _document_display_name(document: dict[str, Any]) -> str:
        """Return a readable label from the tenant request document type."""
        document_type = str(document.get("document_type") or "").strip()
        if document_type in _TENANT_REQUEST_DOCUMENT_LABELS:
            return _TENANT_REQUEST_DOCUMENT_LABELS[document_type]
        if document_type:
            return document_type.replace("_", " ").title()
        return "Document"

    async def _notify_document_review(
        self,
        *,
        organization_id: str,
        project_id: str,
        tenant_request_id: str,
        document_id: str,
        contact_id: str,
        request_row: dict[str, Any],
        document: dict[str, Any],
        message_key: str,
        idempotency_suffix: str,
    ) -> None:
        """Push document verify/reject updates to the request submitter."""
        if not contact_id:
            return
        document_name = self._document_display_name(document)
        unit_label = unit_label_from_row(request_row)
        await self._push().send_to_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            message_key=message_key,
            notification_type="NOTIFICATION_TYPE_TENANT",
            feed_type="tenant",
            params={"document_name": document_name, "unit_label": unit_label},
            data={
                "tenant_request_id": tenant_request_id,
                "document_id": document_id,
                "project_id": project_id,
                "unit_id": str(request_row.get("unit_id") or ""),
                "screen": "tenant_request_detail",
            },
            entity={"kind": "tenant_request", "id": tenant_request_id},
            options={
                "click_action": "OPEN_TENANT_REQUEST",
                "idempotency_key": (
                    f"tenant_request:{tenant_request_id}:{idempotency_suffix}:{document_id}"
                ),
            },
        )

    @staticmethod
    def _derive_header_status(documents: list[dict[str, Any]]) -> str:
        """Compute request header status from document rows."""
        if not documents:
            return TenantRequestStatus.SUBMITTED.value
        statuses = {str(doc.get("status")) for doc in documents}
        if TenantRequestDocumentStatus.REJECTED.value in statuses:
            return TenantRequestStatus.AWAITING_RESUBMISSION.value
        if all(status == TenantRequestDocumentStatus.VERIFIED.value for status in statuses):
            return TenantRequestStatus.READY_TO_APPROVE.value
        return TenantRequestStatus.PENDING_REVIEW.value

    async def _sync_header_status_from_documents(
        self,
        *,
        row: dict[str, Any],
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Align tenant_requests.status with document rows when still in-flight."""
        current = str(row.get("status") or "")
        if current not in _INFLIGHT_STATUSES:
            return row
        derived = self._derive_header_status(documents)
        if derived == current:
            return row
        org_id = self.user_context.organization_id
        assert org_id
        tenant_request_id = str(row["id"])
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            status=derived,
        )
        if (
            derived == TenantRequestStatus.READY_TO_APPROVE.value
            and current != TenantRequestStatus.READY_TO_APPROVE.value
        ):
            user_id = self.user_context.user_id
            await self.repo.insert_event(
                organization_id=org_id,
                tenant_request_id=tenant_request_id,
                event_type=TenantRequestEventType.READY_TO_APPROVE.value,
                actor_user_id=str(user_id) if user_id else None,
            )
        return {**row, "status": derived}

    @staticmethod
    def _derive_milestones(
        *,
        row: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> list[TenantRequestMilestoneResponse]:
        """Build mobile timeline milestones from events and header."""
        submitted_at = format_iso_datetime(row.get("submitted_at"))
        approved_at = format_iso_datetime(row.get("approved_at"))
        ready_at = next(
            (
                format_iso_datetime(event.get("occurred_at"))
                for event in events
                if event.get("event_type") == TenantRequestEventType.READY_TO_APPROVE.value
            ),
            None,
        )
        docs_verified = bool(ready_at) or row.get("status") in {
            TenantRequestStatus.READY_TO_APPROVE.value,
            TenantRequestStatus.APPROVED.value,
            TenantRequestStatus.SUPERSEDED.value,
        }
        superseded_at = format_iso_datetime(row.get("superseded_at"))
        move_out_at = superseded_at or next(
            (
                format_iso_datetime(event.get("occurred_at"))
                for event in events
                if event.get("event_type") == TenantRequestEventType.SUPERSEDED.value
            ),
            None,
        )
        tenant_moved_out = row.get("status") == TenantRequestStatus.SUPERSEDED.value and bool(
            move_out_at
        )
        return [
            TenantRequestMilestoneResponse(
                key="submitted",
                label="Request submitted",
                completed=bool(submitted_at),
                occurred_at=submitted_at,
            ),
            TenantRequestMilestoneResponse(
                key="documents_verified",
                label="Documents verified",
                completed=docs_verified,
                occurred_at=ready_at,
            ),
            TenantRequestMilestoneResponse(
                key="tenant_added",
                label="Tenant added",
                completed=row.get("status")
                in {
                    TenantRequestStatus.APPROVED.value,
                    TenantRequestStatus.SUPERSEDED.value,
                },
                occurred_at=approved_at,
            ),
            TenantRequestMilestoneResponse(
                key="tenant_moved_out",
                label="Tenant moved out",
                completed=tenant_moved_out,
                occurred_at=move_out_at if tenant_moved_out else None,
            ),
        ]

    _OWNER_ROW_KEYS = (
        "owner_contact_id",
        "owner_prefix",
        "owner_first_name",
        "owner_last_name",
        "owner_phones",
        "owner_emails",
        "owner_profile_photo_url",
    )

    _UNIT_ROW_KEYS = (
        "unit_code",
        "unit_label",
        "unit_status",
        "unit_tower_id",
        "unit_config_id",
        "unit_plot_item_id",
        "unit_sort_order",
        "unit_tower_name",
        "unit_tower_type",
        "unit_floor_display_name",
        "unit_floor_level_number",
        "unit_config_kind",
        "unit_config_display_label",
        "unit_config_name",
        "unit_plot_description",
        "unit_resolved_property_type",
        "unit_resolved_config_kind",
    )

    def _build_owner_summary(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build owner (submitter) summary from a repository join row."""
        contact_id = row.get("owner_contact_id") or row.get("submitted_by_contact_id")
        if not contact_id:
            return None
        phone = format_primary_contact_phone(parse_json_any(row.get("owner_phones"), default=[]))
        email = format_primary_contact_email(parse_json_any(row.get("owner_emails"), default=[]))
        profile_photo_url = row.get("owner_profile_photo_url")
        return {
            "contact_id": str(contact_id),
            "display_name": format_contact_display_name(
                prefix=row.get("owner_prefix"),
                first_name=row.get("owner_first_name"),
                last_name=row.get("owner_last_name"),
            )
            or None,
            "phone": str(phone).strip() if phone else None,
            "email": str(email).strip() if email else None,
            "profile_photo_url": str(profile_photo_url).strip() if profile_photo_url else None,
        }

    def _build_unit_summary(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Build unit summary from a repository join row."""
        unit_id = row.get("unit_id")
        if not unit_id:
            return None
        unit_item = serialize_unit_list_item(
            {
                "id": str(unit_id),
                "code": row.get("unit_code") or "",
                "unit_label": row.get("unit_label"),
                "status": row.get("unit_status") or "",
                "sort_order": row.get("unit_sort_order") or 0,
                "tower_id": row.get("unit_tower_id"),
                "config_id": row.get("unit_config_id"),
                "plot_item_id": row.get("unit_plot_item_id"),
                "tower_name": row.get("unit_tower_name"),
                "tower_type": row.get("unit_tower_type"),
                "floor_display_name": row.get("unit_floor_display_name"),
                "floor_level_number": row.get("unit_floor_level_number"),
                "config_kind": row.get("unit_config_kind"),
                "config_display_label": row.get("unit_config_display_label"),
                "config_name": row.get("unit_config_name"),
                "plot_description": row.get("unit_plot_description"),
                "resolved_property_type": row.get("unit_resolved_property_type"),
                "resolved_config_kind": row.get("unit_resolved_config_kind"),
            }
        )
        unit_item.pop("owner", None)
        return unit_item

    def _serialize_list_item(self, row: dict[str, Any]) -> TenantRequestListItemResponse:
        """Map a tenant request row to the admin list API shape."""
        owner_name = format_contact_display_name(
            prefix=row.get("owner_prefix"),
            first_name=row.get("owner_first_name"),
            last_name=row.get("owner_last_name"),
        )
        return TenantRequestListItemResponse(
            id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            unit_id=str(row["unit_id"]),
            submitted_by_contact_id=str(row["submitted_by_contact_id"]),
            owner_name=owner_name or None,
            tenant_first_name=row.get("tenant_first_name") or "",
            tenant_last_name=row.get("tenant_last_name"),
            tenant_phones=parse_json_any(row.get("tenant_phones"), default=[]),
            tenant_emails=parse_json_any(row.get("tenant_emails"), default=[]),
            move_in_date=self._format_date(row.get("move_in_date")),
            move_in_fee=self._format_decimal(row.get("move_in_fee")) or "0",
            status=str(row.get("status")),
            portal_access=bool(row.get("portal_access", False)),
            submitted_at=format_iso_datetime(row.get("submitted_at")),
            approved_at=format_iso_datetime(row.get("approved_at")),
            cancelled_at=format_iso_datetime(row.get("cancelled_at")),
            documents_verified_count=int(row.get("documents_verified_count") or 0),
            documents_total_count=int(row.get("documents_total_count") or 0),
            owner=self._build_owner_summary(row),
            unit=self._build_unit_summary(row),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    async def _serialize_detail(self, row: dict[str, Any]) -> TenantRequestResponse:
        """Load documents/events and map a DB row to the API response."""
        org_id = self.user_context.organization_id
        assert org_id
        documents = await self.repo.list_documents(
            organization_id=org_id,
            tenant_request_id=str(row["id"]),
        )
        row = await self._sync_header_status_from_documents(row=row, documents=documents)
        events = await self.repo.list_events(
            organization_id=org_id,
            tenant_request_id=str(row["id"]),
        )
        verified_count = sum(
            1
            for doc in documents
            if doc.get("status") == TenantRequestDocumentStatus.VERIFIED.value
        )
        return TenantRequestResponse(
            id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            project_id=str(row["project_id"]),
            unit_id=str(row["unit_id"]),
            unit_code=row.get("unit_code"),
            unit_label=row.get("unit_label"),
            submitted_by_contact_id=str(row["submitted_by_contact_id"]),
            owner_name=format_contact_display_name(
                prefix=row.get("owner_prefix"),
                first_name=row.get("owner_first_name"),
                last_name=row.get("owner_last_name"),
            ),
            tenant_first_name=row.get("tenant_first_name") or "",
            tenant_last_name=row.get("tenant_last_name"),
            tenant_phones=parse_json_any(row.get("tenant_phones"), default=[]),
            tenant_emails=parse_json_any(row.get("tenant_emails"), default=[]),
            move_in_date=self._format_date(row.get("move_in_date")),
            move_in_fee=self._format_decimal(row.get("move_in_fee")) or "0",
            status=str(row.get("status")),
            portal_access=bool(row.get("portal_access", False)),
            tenant_contact_id=row.get("tenant_contact_id"),
            contact_unit_id=row.get("contact_unit_id"),
            submitted_at=format_iso_datetime(row.get("submitted_at")),
            approved_at=format_iso_datetime(row.get("approved_at")),
            superseded_at=format_iso_datetime(row.get("superseded_at")),
            cancelled_at=format_iso_datetime(row.get("cancelled_at")),
            admin_notes=row.get("admin_notes"),
            documents=[
                TenantRequestDocumentResponse(
                    id=str(doc["id"]),
                    document_type=str(doc["document_type"]),
                    file_path=str(doc["file_path"]),
                    file_name=doc.get("file_name"),
                    status=str(doc["status"]),
                    rejection_reason=doc.get("rejection_reason"),
                    verified_at=format_iso_datetime(doc.get("verified_at")),
                    uploaded_at=format_iso_datetime(doc.get("uploaded_at")),
                )
                for doc in documents
            ],
            events=[
                TenantRequestEventResponse(
                    id=str(event["id"]),
                    event_type=str(event["event_type"]),
                    occurred_at=format_iso_datetime(event.get("occurred_at")) or "",
                    payload=parse_json_any(event.get("payload"), default={}) or {},
                )
                for event in events
            ],
            milestones=self._derive_milestones(row=row, events=events),
            documents_verified_count=verified_count,
            documents_total_count=len(documents),
            owner=self._build_owner_summary(row),
            unit=self._build_unit_summary(row),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    async def _get_request_or_raise(
        self,
        *,
        tenant_request_id: str,
    ) -> dict[str, Any]:
        """Fetch a tenant request header or raise not found."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.repo.get_request_by_id(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
        )
        if not row:
            raise NotFoundException(
                message_key="tenant_requests.errors.request_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def _assert_owner_access(
        self,
        *,
        owner_contact_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Ensure active primary-occupant link to the unit and return metadata."""
        org_id = self.user_context.organization_id
        assert org_id
        is_primary_occupant = await self.contact_units_repo.owner_has_active_unit(
            organization_id=org_id,
            owner_contact_id=owner_contact_id,
            unit_id=unit_id,
        )
        if not is_primary_occupant:
            raise ValidationException(
                message_key="tenant_requests.errors.unit_not_owned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    async def _assert_no_active_tenant_on_unit(self, *, unit_id: str) -> None:
        """Reject new tenant requests while the unit still has an active tenant."""
        org_id = self.user_context.organization_id
        assert org_id
        tenant_id = await self.contact_roles_repo.get_active_tenant_contact_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if tenant_id:
            raise ValidationException(
                message_key="tenant_requests.errors.active_tenant_exists",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _assert_owner_owns_request(
        self,
        *,
        row: dict[str, Any],
        owner_contact_id: str,
    ) -> None:
        """Ensure the request belongs to the authenticated owner."""
        if str(row["submitted_by_contact_id"]) != owner_contact_id:
            raise NotFoundException(
                message_key="tenant_requests.errors.request_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def create_request(
        self,
        *,
        owner_contact_id: str,
        body: CreateTenantRequestRequest,
    ) -> TenantRequestResponse:
        """Owner creates and submits a tenant request with all documents."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self._assert_owner_access(
            owner_contact_id=owner_contact_id,
            unit_id=body.unit_id,
        )
        await self._assert_no_active_tenant_on_unit(unit_id=body.unit_id)
        phones_payload = [phone.model_dump(exclude_none=True) for phone in body.phones]
        emails_payload = [email.model_dump(exclude_none=True) for email in (body.emails or [])]
        now = datetime.now(timezone.utc)
        try:
            inserted = await self.repo.insert_request(
                organization_id=org_id,
                project_id=str(unit["project_id"]),
                unit_id=body.unit_id,
                submitted_by_contact_id=owner_contact_id,
                tenant_first_name=body.first_name,
                tenant_last_name=body.last_name,
                tenant_phones=phones_payload,
                tenant_emails=emails_payload,
                move_in_date=body.move_in_date,
                portal_access=body.portal_access,
                status=TenantRequestStatus.SUBMITTED.value,
                submitted_at=now,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="tenant_requests.errors.inflight_request_exists",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        request_id = str(inserted["id"])
        for document in body.documents:
            await self.repo.insert_document(
                organization_id=org_id,
                tenant_request_id=request_id,
                document_type=document.document_type.value,
                file_path=document.file_path,
                file_name=document.file_name,
            )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=request_id,
            event_type=TenantRequestEventType.CREATED.value,
            actor_contact_id=owner_contact_id,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=request_id,
            event_type=TenantRequestEventType.SUBMITTED.value,
            actor_contact_id=owner_contact_id,
        )
        row = await self._get_request_or_raise(tenant_request_id=request_id)
        await self._push().send_to_org_members(
            organization_id=org_id,
            message_key="notifications.push.tenant_request.submitted",
            notification_type="NOTIFICATION_TYPE_TENANT",
            feed_type="tenant",
            params={"unit_label": unit_label_from_row(unit)},
            data={
                "tenant_request_id": request_id,
                "project_id": str(unit["project_id"]),
                "unit_id": body.unit_id,
                "screen": "tenant_request_detail",
            },
            entity={"kind": "tenant_request", "id": request_id},
            options={
                "click_action": "OPEN_TENANT_REQUEST",
                "idempotency_key": f"tenant_request:{request_id}:submitted",
            },
        )
        return await self._serialize_detail(row)

    async def sync_after_admin_move_in(
        self,
        *,
        project_id: str,
        unit_id: str,
        tenant_contact_id: str,
        contact_unit_id: str,
        move_in_date: date,
        move_in_fee: Decimal,
        move_event_id: str,
    ) -> None:
        """Reflect an admin-recorded move-in on the owner tenant-requests list."""
        org_id = self.user_context.organization_id
        assert org_id
        user_id = self.user_context.user_id

        existing_approved = await self.repo.find_active_approved_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if (
            existing_approved
            and str(existing_approved.get("tenant_contact_id") or "") == tenant_contact_id
        ):
            return

        now = datetime.now(timezone.utc)
        admin_notes = f"Approved via move event {move_event_id}"
        approve_fields = {
            "tenant_contact_id": tenant_contact_id,
            "contact_unit_id": contact_unit_id,
            "approved_at": now,
            "approved_by_user_id": str(user_id) if user_id else None,
            "move_in_date": move_in_date,
            "move_in_fee": move_in_fee,
            "admin_notes": admin_notes,
        }

        open_request = await self.repo.find_latest_open_request_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if open_request:
            await self.repo.update_request_status(
                organization_id=org_id,
                tenant_request_id=str(open_request["id"]),
                status=TenantRequestStatus.APPROVED.value,
                **approve_fields,
            )
            await self.repo.insert_event(
                organization_id=org_id,
                tenant_request_id=str(open_request["id"]),
                event_type=TenantRequestEventType.APPROVED.value,
                actor_user_id=str(user_id) if user_id else None,
                payload={
                    "tenant_contact_id": tenant_contact_id,
                    "move_event_id": move_event_id,
                },
            )
            return

        owner = await self.units_repo.get_unit_owner_contact(
            organization_id=org_id,
            unit_id=unit_id,
        )
        owner_contact_id = str(owner["contact_id"]) if owner and owner.get("contact_id") else None
        if not owner_contact_id:
            return

        tenant = await self.contacts_repo.get_contact_details(
            contact_id=tenant_contact_id,
            organization_id=org_id,
        )
        if not tenant:
            return

        phones = parse_json_any(tenant.get("phones"), default=[]) or []
        emails = parse_json_any(tenant.get("emails"), default=[]) or []
        inserted = await self.repo.insert_request(
            organization_id=org_id,
            project_id=project_id,
            unit_id=unit_id,
            submitted_by_contact_id=owner_contact_id,
            tenant_first_name=str(tenant.get("first_name") or ""),
            tenant_last_name=tenant.get("last_name"),
            tenant_phones=phones if isinstance(phones, list) else [],
            tenant_emails=emails if isinstance(emails, list) else [],
            move_in_date=move_in_date,
            portal_access=bool(tenant.get("portal_access", False)),
            status=TenantRequestStatus.APPROVED.value,
            submitted_at=now,
        )
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=str(inserted["id"]),
            status=TenantRequestStatus.APPROVED.value,
            **approve_fields,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=str(inserted["id"]),
            event_type=TenantRequestEventType.APPROVED.value,
            actor_user_id=str(user_id) if user_id else None,
            payload={
                "tenant_contact_id": tenant_contact_id,
                "move_event_id": move_event_id,
            },
        )

    async def sync_after_admin_move_out(
        self,
        *,
        unit_id: str,
        tenant_contact_id: str,
        move_event_id: str,
    ) -> None:
        """Close the active approved tenant request after an admin move-out."""
        org_id = self.user_context.organization_id
        assert org_id
        user_id = self.user_context.user_id

        existing = await self.repo.find_active_approved_for_unit_by_tenant(
            organization_id=org_id,
            tenant_contact_id=tenant_contact_id,
        )
        if not existing or str(existing.get("unit_id") or "") != unit_id:
            return

        now = datetime.now(timezone.utc)
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=str(existing["id"]),
            status=TenantRequestStatus.SUPERSEDED.value,
            superseded_at=now,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=str(existing["id"]),
            event_type=TenantRequestEventType.SUPERSEDED.value,
            actor_user_id=str(user_id) if user_id else None,
            payload={
                "reason": "admin_move_out",
                "move_event_id": move_event_id,
                "tenant_contact_id": tenant_contact_id,
            },
        )

    async def list_owner_requests(
        self,
        *,
        owner_contact_id: str,
        query: OwnerTenantRequestListQuery,
    ) -> tuple[list[TenantRequestResponse], int]:
        """Return paginated tenant requests for an owner."""
        org_id = self.user_context.organization_id
        assert org_id
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_for_owner(
            organization_id=org_id,
            owner_contact_id=owner_contact_id,
            unit_id=query.unit_id,
            limit=query.page_size,
            offset=offset,
        )
        items = [await self._serialize_detail(row) for row in rows]
        return items, total

    async def get_owner_request(
        self,
        *,
        owner_contact_id: str,
        tenant_request_id: str,
    ) -> TenantRequestResponse:
        """Return one tenant request for the authenticated owner."""
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        await self._assert_owner_owns_request(row=row, owner_contact_id=owner_contact_id)
        return await self._serialize_detail(row)

    async def cancel_request(
        self,
        *,
        owner_contact_id: str,
        tenant_request_id: str,
    ) -> TenantRequestResponse:
        """Cancel an in-flight tenant request."""
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        await self._assert_owner_owns_request(row=row, owner_contact_id=owner_contact_id)
        if row.get("status") not in _INFLIGHT_STATUSES:
            raise ValidationException(
                message_key="tenant_requests.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        assert org_id
        now = datetime.now(timezone.utc)
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            status=TenantRequestStatus.CANCELLED.value,
            cancelled_at=now,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            event_type=TenantRequestEventType.CANCELLED.value,
            actor_contact_id=owner_contact_id,
        )
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        return await self._serialize_detail(row)

    async def reupload_document(
        self,
        *,
        owner_contact_id: str,
        tenant_request_id: str,
        document_id: str,
        body: ReuploadTenantDocumentRequest,
    ) -> TenantRequestResponse:
        """Replace a rejected document and resubmit the request for review."""
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        await self._assert_owner_owns_request(row=row, owner_contact_id=owner_contact_id)
        if row.get("status") not in {
            TenantRequestStatus.AWAITING_RESUBMISSION.value,
            TenantRequestStatus.PENDING_REVIEW.value,
            TenantRequestStatus.SUBMITTED.value,
        }:
            raise ValidationException(
                message_key="tenant_requests.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        assert org_id
        document = await self.repo.get_document_by_id(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
        )
        if not document:
            raise NotFoundException(
                message_key="tenant_requests.errors.document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if document.get("status") != TenantRequestDocumentStatus.REJECTED.value:
            raise ValidationException(
                message_key="tenant_requests.errors.document_not_rejected",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        updated = await self.repo.update_document_reupload(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
            file_path=body.file_path,
            file_name=body.file_name,
        )
        if not updated:
            raise NotFoundException(
                message_key="tenant_requests.errors.document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        documents = await self.repo.list_documents(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
        )
        new_status = self._derive_header_status(documents)
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            status=new_status,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            event_type=TenantRequestEventType.RESUBMITTED.value,
            actor_contact_id=owner_contact_id,
            payload={"document_type": updated.get("document_type")},
        )
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        return await self._serialize_detail(row)

    @staticmethod
    def _bucket_to_statuses(bucket: TenantRequestListBucket | None) -> list[str] | None:
        """Map admin list bucket filters to underlying request statuses."""
        if bucket is None:
            return None
        mapping = {
            TenantRequestListBucket.PENDING_REVIEW: [
                TenantRequestStatus.SUBMITTED.value,
                TenantRequestStatus.PENDING_REVIEW.value,
            ],
            TenantRequestListBucket.AWAITING_RESUBMISSION: [
                TenantRequestStatus.AWAITING_RESUBMISSION.value,
            ],
            TenantRequestListBucket.READY_TO_APPROVE: [
                TenantRequestStatus.READY_TO_APPROVE.value,
            ],
            TenantRequestListBucket.APPROVED: [TenantRequestStatus.APPROVED.value],
            TenantRequestListBucket.CANCELLED: [TenantRequestStatus.CANCELLED.value],
            TenantRequestListBucket.SUPERSEDED: [TenantRequestStatus.SUPERSEDED.value],
        }
        return mapping.get(bucket)

    async def get_admin_summary(self, *, project_id: str) -> TenantRequestSummaryResponse:
        """Return dashboard summary card counts for a project."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_project(project_id=project_id)
        counts = await self.repo.get_summary_counts(
            organization_id=org_id,
            project_id=project_id,
        )
        return TenantRequestSummaryResponse(**counts)

    async def list_admin_requests(
        self,
        *,
        project_id: str,
        query: TenantRequestListQuery,
    ) -> tuple[list[TenantRequestListItemResponse], int]:
        """Return paginated tenant requests for admin review within a project."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_project(project_id=project_id)
        statuses = [query.status.value] if query.status else self._bucket_to_statuses(query.bucket)
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_for_admin(
            organization_id=org_id,
            statuses=statuses,
            search=query.search,
            unit_id=query.unit_id,
            project_id=project_id,
            limit=query.page_size,
            offset=offset,
        )
        items = [self._serialize_list_item(row) for row in rows]
        return items, total

    async def get_admin_request(
        self,
        *,
        project_id: str,
        tenant_request_id: str,
    ) -> TenantRequestResponse:
        """Return one tenant request for admin review within a project."""
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        return await self._serialize_detail(row)

    async def _recompute_after_document_review(
        self,
        *,
        tenant_request_id: str,
        actor_user_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Recompute header status and append timeline events after doc review."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        documents = await self.repo.list_documents(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
        )
        await self._sync_header_status_from_documents(row=row, documents=documents)
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            payload=payload,
        )

    async def verify_document(
        self,
        *,
        project_id: str,
        tenant_request_id: str,
        document_id: str,
    ) -> TenantRequestResponse:
        """Admin marks one document as verified."""
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        if row.get("status") not in _INFLIGHT_STATUSES:
            raise ValidationException(
                message_key="tenant_requests.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id
        updated = await self.repo.verify_document(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
            verified_by_user_id=str(user_id),
        )
        if not updated:
            raise NotFoundException(
                message_key="tenant_requests.errors.document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self._recompute_after_document_review(
            tenant_request_id=tenant_request_id,
            actor_user_id=str(user_id),
            event_type=TenantRequestEventType.DOCUMENT_VERIFIED.value,
            payload={"document_type": updated.get("document_type")},
        )
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        await self._notify_document_review(
            organization_id=org_id,
            project_id=project_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
            contact_id=str(row.get("submitted_by_contact_id") or ""),
            request_row=row,
            document=updated,
            message_key="notifications.push.tenant_request.document_verified",
            idempotency_suffix="document_verified",
        )
        return await self._serialize_detail(row)

    async def reject_document(
        self,
        *,
        project_id: str,
        tenant_request_id: str,
        document_id: str,
        body: RejectTenantDocumentRequest,
    ) -> TenantRequestResponse:
        """Admin rejects one document with a reason."""
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        if row.get("status") not in _INFLIGHT_STATUSES:
            raise ValidationException(
                message_key="tenant_requests.errors.invalid_status_transition",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id
        updated = await self.repo.reject_document(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
            verified_by_user_id=str(user_id),
            rejection_reason=body.rejection_reason,
        )
        if not updated:
            raise NotFoundException(
                message_key="tenant_requests.errors.document_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self._recompute_after_document_review(
            tenant_request_id=tenant_request_id,
            actor_user_id=str(user_id),
            event_type=TenantRequestEventType.DOCUMENT_REJECTED.value,
            payload={
                "document_type": updated.get("document_type"),
                "rejection_reason": body.rejection_reason,
            },
        )
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        await self._notify_document_review(
            organization_id=org_id,
            project_id=project_id,
            tenant_request_id=tenant_request_id,
            document_id=document_id,
            contact_id=str(row.get("submitted_by_contact_id") or ""),
            request_row=row,
            document=updated,
            message_key="notifications.push.tenant_request.document_rejected",
            idempotency_suffix="document_rejected",
        )
        return await self._serialize_detail(row)

    async def approve_request(
        self,
        *,
        project_id: str,
        tenant_request_id: str,
        body: ApproveTenantRequestRequest,
    ) -> TenantRequestResponse:
        """Admin approves a ready request and provisions the tenant contact."""
        row = await self._get_admin_request_or_raise(
            project_id=project_id,
            tenant_request_id=tenant_request_id,
        )
        org_id = self.user_context.organization_id
        assert org_id
        documents = await self.repo.list_documents(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
        )
        row = await self._sync_header_status_from_documents(row=row, documents=documents)
        if row.get("status") != TenantRequestStatus.READY_TO_APPROVE.value:
            raise ValidationException(
                message_key="tenant_requests.errors.not_ready_to_approve",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        user_id = self.user_context.user_id
        assert org_id and user_id
        unit_id = str(row["unit_id"])
        now = datetime.now(timezone.utc)
        project_id = str(row["project_id"])

        existing = await self.repo.find_active_approved_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        old_tenant_contact_id = (
            str(existing["tenant_contact_id"])
            if existing and existing.get("tenant_contact_id")
            else None
        )
        old_contact_unit_id = (
            str(existing["contact_unit_id"])
            if existing and existing.get("contact_unit_id")
            else None
        )

        turnover_service = UnitOccupancyTurnoverService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await turnover_service.release_outgoing_tenant_household(
            organization_id=org_id,
            project_id=project_id,
            unit_id=unit_id,
            reason="Tenant request approved; clearing outgoing household.",
        )

        if existing and old_contact_unit_id:
            if old_tenant_contact_id:
                await self._record_move_event_ledger(
                    organization_id=org_id,
                    project_id=project_id,
                    unit_id=unit_id,
                    contact_id=old_tenant_contact_id,
                    contact_unit_id=old_contact_unit_id,
                    move_type=MoveEventType.MOVE_OUT.value,
                    event_date=now.date(),
                    notes=f"Superseded by tenant request {tenant_request_id}",
                )
            await self.repo.update_request_status(
                organization_id=org_id,
                tenant_request_id=str(existing["id"]),
                status=TenantRequestStatus.SUPERSEDED.value,
                superseded_at=now,
                superseded_by_request_id=tenant_request_id,
            )
            await self.repo.insert_event(
                organization_id=org_id,
                tenant_request_id=str(existing["id"]),
                event_type=TenantRequestEventType.SUPERSEDED.value,
                actor_user_id=str(user_id),
                payload={"superseded_by_request_id": tenant_request_id},
            )

        contacts_service = ContactsService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=self.supabase_client,
        )
        phones = [
            Phone.model_validate(item)
            for item in parse_json_any(row.get("tenant_phones"), default=[]) or []
        ]
        emails = [
            Email.model_validate(item)
            for item in parse_json_any(row.get("tenant_emails"), default=[]) or []
        ]
        create_result = await contacts_service.create_contact(
            CreateContactRequest(
                portal_access=bool(row.get("portal_access")),
                first_name=row.get("tenant_first_name"),
                last_name=row.get("tenant_last_name"),
                phones=phones,
                emails=emails or [],
            ),
        )
        tenant_contact_id = str(create_result["contact_id"])
        link = await self.contact_units_repo.insert_primary_occupant_link(
            organization_id=org_id,
            project_id=str(row["project_id"]),
            unit_id=unit_id,
            contact_id=tenant_contact_id,
        )
        await self.contact_roles_repo.end_active_roles_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
            role_types=[ContactType.TENANT.value],
        )
        await self.contact_roles_repo.insert_tenant_role(
            organization_id=org_id,
            contact_id=tenant_contact_id,
            project_id=str(row["project_id"]),
            unit_id=unit_id,
            contact_unit_id=str(link["id"]),
        )
        await self._record_move_event_ledger(
            organization_id=org_id,
            project_id=str(row["project_id"]),
            unit_id=unit_id,
            contact_id=tenant_contact_id,
            contact_unit_id=str(link["id"]),
            move_type=MoveEventType.MOVE_IN.value,
            event_date=body.move_in_date,
            fee_amount=body.move_in_fee,
            notes=f"Tenant request approved ({tenant_request_id})",
            documents=self._snapshot_tenant_request_documents(documents),
        )
        await self.repo.update_request_status(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            status=TenantRequestStatus.APPROVED.value,
            tenant_contact_id=tenant_contact_id,
            contact_unit_id=str(link["id"]),
            approved_at=now,
            approved_by_user_id=str(user_id),
            admin_notes=body.admin_notes,
            move_in_date=body.move_in_date,
            move_in_fee=body.move_in_fee,
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            event_type=TenantRequestEventType.APPROVED.value,
            actor_user_id=str(user_id),
            payload={"tenant_contact_id": tenant_contact_id},
        )
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if unit:
            await self.units_repo.reconcile_unit_inventory_status(
                organization_id=org_id,
                project_id=str(unit["project_id"]),
                unit_id=unit_id,
            )
        unit_label = unit_label_from_row(unit or {"unit_id": unit_id})
        await self._push().send_to_contact(
            organization_id=org_id,
            contact_id=str(row.get("submitted_by_contact_id") or ""),
            message_key="notifications.push.tenant_request.approved",
            notification_type="NOTIFICATION_TYPE_TENANT",
            feed_type="tenant",
            params={"unit_label": unit_label},
            data={
                "tenant_request_id": tenant_request_id,
                "project_id": project_id,
                "screen": "tenant_request_detail",
            },
            entity={"kind": "tenant_request", "id": tenant_request_id},
            options={
                "click_action": "OPEN_TENANT_REQUEST",
                "idempotency_key": f"tenant_request:{tenant_request_id}:approved",
            },
        )
        return await self._serialize_detail(row)
