"""Tenant request business logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError
from supabase import AsyncClient

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.tenant_requests_repository import (
    TenantRequestsRepository,
)
from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import (
    ContactType,
    TenantRequestDocumentStatus,
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
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.repo = tenant_requests_repository or TenantRequestsRepository(db_connection)
        self.contact_units_repo = contact_units_repository or ContactUnitsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

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
        """Ensure the owner has an active link to the unit and return unit metadata."""
        org_id = self.user_context.organization_id
        assert org_id
        is_owner = await self.contact_units_repo.owner_has_active_unit(
            organization_id=org_id,
            owner_contact_id=owner_contact_id,
            unit_id=unit_id,
        )
        if not is_owner:
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
        return await self._serialize_detail(row)

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
            event_type=event_type,
            actor_user_id=actor_user_id,
            payload=payload,
        )
        if new_status == TenantRequestStatus.READY_TO_APPROVE.value:
            await self.repo.insert_event(
                organization_id=org_id,
                tenant_request_id=tenant_request_id,
                event_type=TenantRequestEventType.READY_TO_APPROVE.value,
                actor_user_id=actor_user_id,
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
        if row.get("status") != TenantRequestStatus.READY_TO_APPROVE.value:
            raise ValidationException(
                message_key="tenant_requests.errors.not_ready_to_approve",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        org_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        assert org_id and user_id
        unit_id = str(row["unit_id"])
        now = datetime.now(timezone.utc)

        existing = await self.repo.find_active_approved_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if existing and existing.get("contact_unit_id"):
            await self.contact_units_repo.sync_move_out(
                organization_id=org_id,
                contact_unit_id=str(existing["contact_unit_id"]),
                event_date=now.date(),
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
        phones = [Phone.model_validate(item) for item in list(row.get("tenant_phones") or [])]
        emails = [Email.model_validate(item) for item in list(row.get("tenant_emails") or [])]
        create_result = await contacts_service.create_contact(
            CreateContactRequest(
                contact_type=ContactType.TENANT,
                portal_access=bool(row.get("portal_access")),
                first_name=row.get("tenant_first_name"),
                last_name=row.get("tenant_last_name"),
                phones=phones,
                emails=emails or [],
            ),
            provision_auth=not bool(row.get("portal_access")),
        )
        tenant_contact_id = str(create_result["contact_id"])
        link = await self.contact_units_repo.insert_primary_occupant_link(
            organization_id=org_id,
            project_id=str(row["project_id"]),
            unit_id=unit_id,
            contact_id=tenant_contact_id,
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
        )
        await self.repo.insert_event(
            organization_id=org_id,
            tenant_request_id=tenant_request_id,
            event_type=TenantRequestEventType.APPROVED.value,
            actor_user_id=str(user_id),
            payload={"tenant_contact_id": tenant_contact_id},
        )
        row = await self._get_request_or_raise(tenant_request_id=tenant_request_id)
        return await self._serialize_detail(row)
