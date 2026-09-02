"""Admin community events business logic."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.schemas.community_events import (
    AdminCreateEventBookingRequest,
    CancelCommunityEventRequest,
    CommunityEventBookingListItemResponse,
    CommunityEventBookingListQuery,
    CommunityEventDetailResponse,
    CommunityEventExportQuery,
    CommunityEventListItemResponse,
    CommunityEventListQuery,
    CommunityEventMediaInput,
    CommunityEventMediaResponse,
    CommunityEventSummaryResponse,
    CreateCommunityEventRequest,
    MarkBookingPaidRequest,
    MarkBookingWaivedRequest,
    UpdateCommunityEventRequest,
)
from apps.user_service.app.schemas.enums import (
    COMMUNITY_EVENT_ALLOWED_MEDIA_MIMES,
    COMMUNITY_EVENT_CATEGORY_LABELS,
    COMMUNITY_EVENT_EXPORT_MAX_ROWS,
    COMMUNITY_EVENT_MAX_GALLERY,
    CommunityEventAuditAction,
    CommunityEventPublishMode,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_event_booking_service import (
    CommunityEventBookingService,
)
from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext, validate_uuid_format
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_STRUCTURAL_FIELDS = frozenset(
    {
        "start_date",
        "end_date",
        "start_time",
        "end_time",
        "event_type",
        "total_capacity",
        "adult_price_minor",
        "child_ticket_mode",
        "child_price_minor",
        "apply_tax",
        "tax_rate",
        "facility_id",
        "is_multi_day",
        "max_tickets_per_resident",
    }
)


class CommunityEventsService:
    """Admin CRUD and lifecycle for community events."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = CommunityEventsRepository(db_connection)
        self.facilities_repo = FacilitiesRepository(db_connection)
        self.contacts_repo = ContactsRepository(db_connection)
        self.booking_service = CommunityEventBookingService(
            db_connection=db_connection,
            user_context=user_context,
        )
        self.notifications = CommunityEventNotificationService(db_connection=db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @property
    def organization_id(self) -> str:
        """Organization id from context."""
        return self.user_context.organization_id

    @staticmethod
    def _facility_location_label(row: dict[str, Any]) -> str | None:
        """Build facility location string."""
        parts: list[str] = []
        tower = row.get("tower_name")
        if tower:
            parts.append(str(tower))
        for key in ("wing", "floor_level"):
            val = row.get(key)
            if val:
                parts.append(str(val))
        if not parts:
            return row.get("location_notes")
        label = ", ".join(parts)
        notes = row.get("location_notes")
        if notes:
            return f"{label} — {notes}"
        return label

    @staticmethod
    def _derive_booking_state(row: dict[str, Any]) -> str:
        """Admin list booking state label."""
        if str(row.get("publish_status")) != CommunityEventPublishStatus.PUBLISHED.value:
            return "closed"
        closes = row.get("booking_closes_at")
        if closes and closes <= datetime.now(timezone.utc):
            return "closed"
        capacity = row.get("total_capacity")
        booked = int(row.get("tickets_booked") or 0)
        if capacity is not None and booked >= int(capacity):
            return "closed"
        return "open"

    def _serialize_list_item(self, row: dict[str, Any]) -> CommunityEventListItemResponse:
        """Map DB row to admin list item."""
        category = str(row.get("category") or "")
        return CommunityEventListItemResponse(
            id=str(row["id"]),
            display_code=str(row["display_code"]),
            title=str(row["title"]),
            category=category,
            category_label=COMMUNITY_EVENT_CATEGORY_LABELS.get(category, category),
            start_date=row["start_date"],
            end_date=row["end_date"],
            is_multi_day=bool(row.get("is_multi_day")),
            event_type=str(row.get("event_type") or ""),
            facility_name=row.get("facility_name"),
            facility_location_label=self._facility_location_label(row),
            bookings_count=int(row.get("bookings_count") or 0),
            tickets_booked=int(row.get("tickets_booked") or 0),
            total_capacity=row.get("total_capacity"),
            ticket_breakdown_adult=int(row.get("ticket_breakdown_adult") or 0),
            ticket_breakdown_child=int(row.get("ticket_breakdown_child") or 0),
            paid_bookings_count=int(row.get("paid_bookings_count") or 0),
            revenue_collected_minor=int(row.get("revenue_collected_minor") or 0),
            publish_status=str(row.get("publish_status") or ""),
            record_status=str(row.get("record_status") or ""),
            booking_state=self._derive_booking_state(row),
            cover_image_path=row.get("cover_image_path"),
        )

    async def _serialize_detail(
        self,
        *,
        row: dict[str, Any],
    ) -> CommunityEventDetailResponse:
        """Map DB row to admin detail."""
        gallery_rows = await self.repo.list_gallery(
            organization_id=self.organization_id,
            event_id=str(row["id"]),
        )
        category = str(row.get("category") or "")
        return CommunityEventDetailResponse(
            id=str(row["id"]),
            display_code=str(row["display_code"]),
            title=str(row["title"]),
            description=str(row.get("description") or ""),
            category=category,
            category_label=COMMUNITY_EVENT_CATEGORY_LABELS.get(category, category),
            publish_status=str(row.get("publish_status") or ""),
            record_status=str(row.get("record_status") or ""),
            facility_id=row.get("facility_id"),
            facility_name=row.get("facility_name"),
            facility_location_label=self._facility_location_label(row),
            is_multi_day=bool(row.get("is_multi_day")),
            start_date=row["start_date"],
            end_date=row["end_date"],
            start_time=row.get("start_time"),
            end_time=row.get("end_time"),
            event_type=str(row.get("event_type") or ""),
            total_capacity=row.get("total_capacity"),
            max_tickets_per_resident=int(row.get("max_tickets_per_resident") or 4),
            booking_closes_at=row.get("booking_closes_at"),
            adult_price_minor=int(row.get("adult_price_minor") or 0),
            child_ticket_mode=str(row.get("child_ticket_mode") or ""),
            child_price_minor=int(row.get("child_price_minor") or 0),
            apply_tax=bool(row.get("apply_tax")),
            tax_rate=float(row.get("tax_rate") or 18),
            currency=str(row.get("currency") or "INR"),
            cover_image_path=row.get("cover_image_path"),
            gallery=[CommunityEventMediaResponse(**g) for g in gallery_rows],
            tickets_booked=int(row.get("tickets_booked") or 0),
            bookings_count=int(row.get("bookings_count") or 0),
            paid_bookings_count=int(row.get("paid_bookings_count") or 0),
            revenue_collected_minor=int(row.get("revenue_collected_minor") or 0),
            published_at=row.get("published_at"),
            completed_at=row.get("completed_at"),
            cancelled_at=row.get("cancelled_at"),
            cancelled_reason=row.get("cancelled_reason"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _validate_gallery(items: list[CommunityEventMediaInput] | None) -> None:
        """Validate gallery attachments."""
        if not items:
            return
        if len(items) > COMMUNITY_EVENT_MAX_GALLERY:
            raise ValidationException(
                message_key="community_events.errors.gallery_limit_exceeded",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        for item in items:
            if item.mime_type not in COMMUNITY_EVENT_ALLOWED_MEDIA_MIMES:
                raise ValidationException(
                    message_key="community_events.errors.invalid_media_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

    @staticmethod
    def _gallery_items_to_dicts(items: list[Any]) -> list[dict[str, Any]]:
        """Normalize gallery rows for repository persistence."""
        result: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                result.append(item)
            elif hasattr(item, "model_dump"):
                result.append(item.model_dump())
            else:
                result.append(dict(item))
        return result

    async def _validate_facility(
        self,
        *,
        project_id: str,
        facility_id: str | None,
        required: bool,
    ) -> None:
        """Validate facility when required."""
        if not facility_id:
            if required:
                raise ValidationException(
                    message_key="community_events.errors.facility_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return
        validate_uuid_format(facility_id, "facility ID")
        facility = await self.facilities_repo.get_facility(
            organization_id=self.organization_id,
            project_id=project_id,
            facility_id=facility_id,
        )
        CommunityEventBookingService.validate_facility_for_event(facility)

    @staticmethod
    def _validate_pricing(body: CreateCommunityEventRequest | UpdateCommunityEventRequest) -> None:
        """Validate paid event pricing fields."""
        event_type = getattr(body, "event_type", None)
        if event_type == CommunityEventType.PAID or (
            isinstance(body, CreateCommunityEventRequest)
            and body.event_type == CommunityEventType.PAID
        ):
            adult = getattr(body, "adult_price_minor", None)
            if adult is not None and adult <= 0:
                raise ValidationException(
                    message_key="community_events.errors.adult_price_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

    async def get_summary(self, *, project_id: str) -> CommunityEventSummaryResponse:
        """Dashboard summary."""
        await self.setup_service.ensure_project(project_id=project_id)
        counts = await self.repo.get_summary_counts(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        return CommunityEventSummaryResponse(
            total_events=int(counts.get("total_events") or 0),
            upcoming=int(counts.get("upcoming") or 0),
            total_rsvps=int(counts.get("total_rsvps") or 0),
            revenue_collected_minor=int(counts.get("revenue_collected_minor") or 0),
            tabs={
                "all": int(counts.get("tab_all") or 0),
                "draft": int(counts.get("tab_draft") or 0),
                "published": int(counts.get("tab_published") or 0),
                "completed": int(counts.get("tab_completed") or 0),
                "cancelled": int(counts.get("tab_cancelled") or 0),
                "deleted": int(counts.get("tab_deleted") or 0),
            },
        )

    async def list_events(
        self,
        *,
        project_id: str,
        query: CommunityEventListQuery,
    ) -> tuple[list[CommunityEventListItemResponse], int]:
        """Paginated admin list."""
        await self.setup_service.ensure_project(project_id=project_id)
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_events(
            organization_id=self.organization_id,
            project_id=project_id,
            tab=query.tab.value,
            search=query.search,
            limit=query.page_size,
            offset=offset,
        )
        return [self._serialize_list_item(row) for row in rows], total

    async def get_event(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> CommunityEventDetailResponse:
        """Event detail."""
        await self.setup_service.ensure_project(project_id=project_id)
        row = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if not row:
            raise NotFoundException(
                message_key="community_events.errors.event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self._serialize_detail(row=row)

    async def create_event(
        self,
        *,
        project_id: str,
        body: CreateCommunityEventRequest,
    ) -> CommunityEventDetailResponse:
        """Create draft or published event."""
        await self.setup_service.ensure_project(project_id=project_id)
        self._validate_gallery(body.gallery)
        publish = body.publish_mode == CommunityEventPublishMode.PUBLISH
        await self._validate_facility(
            project_id=project_id,
            facility_id=body.facility_id,
            required=publish,
        )
        if publish and not body.booking_closes_at:
            raise ValidationException(
                message_key="community_events.errors.booking_closes_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.end_date < body.start_date:
            raise ValidationException(
                message_key="community_events.errors.invalid_date_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        seq = await self.repo.allocate_event_sequence(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        publish_status = (
            CommunityEventPublishStatus.PUBLISHED.value
            if publish
            else CommunityEventPublishStatus.DRAFT.value
        )
        row = await self.repo.insert_event(
            organization_id=self.organization_id,
            project_id=project_id,
            data={
                "display_code": f"EVT-{seq}",
                "sequence_number": seq,
                "title": body.title,
                "description": body.description,
                "category": body.category.value,
                "publish_status": publish_status,
                "facility_id": body.facility_id,
                "is_multi_day": body.is_multi_day,
                "start_date": body.start_date,
                "end_date": body.end_date,
                "start_time": body.start_time,
                "end_time": body.end_time,
                "event_type": body.event_type.value,
                "total_capacity": body.total_capacity,
                "max_tickets_per_resident": body.max_tickets_per_resident,
                "booking_closes_at": body.booking_closes_at,
                "adult_price_minor": body.adult_price_minor,
                "child_ticket_mode": body.child_ticket_mode.value,
                "child_price_minor": body.child_price_minor,
                "apply_tax": body.apply_tax,
                "tax_rate": body.tax_rate,
                "cover_image_path": body.cover_image_path,
                "published_at": datetime.now(timezone.utc) if publish else None,
                "created_by_user_id": self.user_context.user_id,
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        if body.gallery:
            await self.repo.replace_gallery(
                organization_id=self.organization_id,
                project_id=project_id,
                event_id=str(row["id"]),
                items=self._gallery_items_to_dicts(body.gallery),
            )
        action = (
            CommunityEventAuditAction.PUBLISHED.value
            if publish
            else CommunityEventAuditAction.CREATED.value
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=str(row["id"]),
            booking_id=None,
            action=action,
            actor_user_id=self.user_context.user_id,
        )
        if publish:
            await self._dispatch_publish_push(project_id=project_id, event=row)
        return await self.get_event(project_id=project_id, event_id=str(row["id"]))

    async def update_event(
        self,
        *,
        project_id: str,
        event_id: str,
        body: UpdateCommunityEventRequest,
    ) -> CommunityEventDetailResponse:
        """Update event fields."""
        existing = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="community_events.errors.event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(existing.get("record_status")) == CommunityEventRecordStatus.DELETED.value:
            raise ConflictException(
                message_key="community_events.errors.event_deleted",
                custom_code=CustomStatusCode.CONFLICT,
            )

        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        gallery = patch.pop("gallery", None)
        if gallery is not None:
            self._validate_gallery(body.gallery)

        if (
            str(existing.get("publish_status")) == CommunityEventPublishStatus.PUBLISHED.value
            and int(existing.get("tickets_booked") or 0) > 0
            and _STRUCTURAL_FIELDS.intersection(patch.keys())
        ):
            raise ConflictException(
                message_key="community_events.errors.event_not_editable",
                custom_code=CustomStatusCode.CONFLICT,
            )

        if "facility_id" in patch and patch["facility_id"]:
            await self._validate_facility(
                project_id=project_id,
                facility_id=patch["facility_id"],
                required=False,
            )

        enum_map = {
            "category": lambda v: v.value if hasattr(v, "value") else v,
            "event_type": lambda v: v.value if hasattr(v, "value") else v,
            "child_ticket_mode": lambda v: v.value if hasattr(v, "value") else v,
        }
        db_fields = {k: enum_map[k](v) if k in enum_map else v for k, v in patch.items()}
        db_fields["updated_by_user_id"] = self.user_context.user_id

        if db_fields:
            await self.repo.update_event_fields(
                organization_id=self.organization_id,
                project_id=project_id,
                event_id=event_id,
                fields=db_fields,
            )
        if gallery is not None:
            await self.repo.replace_gallery(
                organization_id=self.organization_id,
                project_id=project_id,
                event_id=event_id,
                items=self._gallery_items_to_dicts(gallery),
            )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.UPDATED.value,
            actor_user_id=self.user_context.user_id,
        )
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def publish_event(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> CommunityEventDetailResponse:
        """Publish a draft event."""
        existing = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="community_events.errors.event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if str(existing.get("publish_status")) != CommunityEventPublishStatus.DRAFT.value:
            raise ConflictException(
                message_key="community_events.errors.event_not_publishable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if not existing.get("facility_id") or not existing.get("booking_closes_at"):
            raise ValidationException(
                message_key="community_events.errors.event_not_publishable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.update_event_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            fields={
                "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
                "published_at": datetime.now(timezone.utc),
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.PUBLISHED.value,
            actor_user_id=self.user_context.user_id,
        )
        row = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        if row:
            await self._dispatch_publish_push(project_id=project_id, event=row)
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def cancel_event(
        self,
        *,
        project_id: str,
        event_id: str,
        body: CancelCommunityEventRequest,
    ) -> CommunityEventDetailResponse:
        """Cancel published event."""
        event_row = await self.repo.fetch_event_by_id(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
        )
        await self.repo.update_event_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            fields={
                "publish_status": CommunityEventPublishStatus.CANCELLED.value,
                "cancelled_at": datetime.now(timezone.utc),
                "cancelled_reason": body.reason,
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.CANCELLED.value,
            actor_user_id=self.user_context.user_id,
            payload={"reason": body.reason},
        )
        if event_row:
            await self._dispatch_cancel_push(event=event_row)
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def complete_event(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> CommunityEventDetailResponse:
        """Mark event completed."""
        await self.repo.update_event_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            fields={
                "publish_status": CommunityEventPublishStatus.COMPLETED.value,
                "completed_at": datetime.now(timezone.utc),
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.COMPLETED.value,
            actor_user_id=self.user_context.user_id,
        )
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def delete_event(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> CommunityEventDetailResponse:
        """Soft delete event."""
        await self.repo.update_event_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            fields={
                "record_status": CommunityEventRecordStatus.DELETED.value,
                "deleted_at": datetime.now(timezone.utc),
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.DELETED.value,
            actor_user_id=self.user_context.user_id,
        )
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def restore_event(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> CommunityEventDetailResponse:
        """Restore soft-deleted event."""
        await self.repo.update_event_fields(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            fields={
                "record_status": CommunityEventRecordStatus.ACTIVE.value,
                "deleted_at": None,
                "updated_by_user_id": self.user_context.user_id,
            },
        )
        await self.repo.insert_audit_log(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_id=None,
            action=CommunityEventAuditAction.RESTORED.value,
            actor_user_id=self.user_context.user_id,
        )
        return await self.get_event(project_id=project_id, event_id=event_id)

    async def list_bookings(
        self,
        *,
        project_id: str,
        event_id: str,
        query: CommunityEventBookingListQuery,
    ) -> tuple[list[CommunityEventBookingListItemResponse], int]:
        """List bookings for an event."""
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_bookings_for_event(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_status=query.booking_status,
            payment_status=query.payment_status,
            limit=query.page_size,
            offset=offset,
        )
        items = [self._booking_list_item(r) for r in rows]
        return items, total

    @staticmethod
    def _booking_list_item(row: dict[str, Any]) -> CommunityEventBookingListItemResponse:
        """Map a booking row to admin list item."""
        return CommunityEventBookingListItemResponse(
            id=str(row["id"]),
            display_code=str(row["display_code"]),
            contact_id=str(row["contact_id"]),
            contact_name=row.get("contact_name"),
            adult_tickets=int(row.get("adult_tickets") or 0),
            child_tickets=int(row.get("child_tickets") or 0),
            total_tickets=int(row.get("total_tickets") or 0),
            total_amount_minor=int(row.get("total_amount_minor") or 0),
            currency=str(row.get("currency") or "INR"),
            booking_status=str(row.get("booking_status") or ""),
            payment_status=str(row.get("payment_status") or ""),
            paid_at=row.get("paid_at"),
            booked_at=row["booked_at"],
        )

    async def create_booking_on_behalf(
        self,
        *,
        project_id: str,
        event_id: str,
        body: AdminCreateEventBookingRequest,
    ) -> CommunityEventBookingListItemResponse:
        """Admin creates a resident booking (optional immediate mark-paid)."""
        row = await self.booking_service.create_admin_booking(
            project_id=project_id,
            event_id=event_id,
            body=body,
        )
        return self._booking_list_item(row)

    async def mark_booking_paid(
        self,
        *,
        project_id: str,
        event_id: str,
        booking_id: str,
        body: MarkBookingPaidRequest,
    ) -> CommunityEventBookingListItemResponse:
        """Delegate mark paid."""
        row = await self.booking_service.mark_paid(
            project_id=project_id,
            event_id=event_id,
            booking_id=booking_id,
            body=body,
        )
        return self._booking_list_item(row)

    async def mark_booking_waived(
        self,
        *,
        project_id: str,
        event_id: str,
        booking_id: str,
        body: MarkBookingWaivedRequest,
    ) -> CommunityEventBookingListItemResponse:
        """Delegate mark waived."""
        row = await self.booking_service.mark_waived(
            project_id=project_id,
            event_id=event_id,
            booking_id=booking_id,
            body=body,
        )
        return self._booking_list_item(row)

    async def export_events_csv(
        self,
        *,
        project_id: str,
        query: CommunityEventExportQuery,
    ) -> str:
        """Export events as CSV."""
        rows = await self.repo.list_events_for_export(
            organization_id=self.organization_id,
            project_id=project_id,
            tab=query.tab.value,
            search=query.search,
            limit=COMMUNITY_EVENT_EXPORT_MAX_ROWS,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "display_code",
                "title",
                "category",
                "publish_status",
                "event_type",
                "start_date",
                "end_date",
                "tickets_booked",
                "total_capacity",
                "revenue_collected_minor",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("display_code"),
                    row.get("title"),
                    row.get("category"),
                    row.get("publish_status"),
                    row.get("event_type"),
                    row.get("start_date"),
                    row.get("end_date"),
                    row.get("tickets_booked"),
                    row.get("total_capacity"),
                    row.get("revenue_collected_minor"),
                ]
            )
        return buffer.getvalue()

    async def export_bookings_csv(
        self,
        *,
        project_id: str,
        event_id: str,
    ) -> str:
        """Export event bookings as CSV."""
        rows = await self.repo.list_bookings_for_export(
            organization_id=self.organization_id,
            project_id=project_id,
            event_id=event_id,
            limit=COMMUNITY_EVENT_EXPORT_MAX_ROWS,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "display_code",
                "contact_name",
                "adult_tickets",
                "child_tickets",
                "total_amount_minor",
                "booking_status",
                "payment_status",
                "booked_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("display_code"),
                    row.get("contact_name"),
                    row.get("adult_tickets"),
                    row.get("child_tickets"),
                    row.get("total_amount_minor"),
                    row.get("booking_status"),
                    row.get("payment_status"),
                    row.get("booked_at"),
                ]
            )
        return buffer.getvalue()

    async def _dispatch_publish_push(
        self,
        *,
        project_id: str,
        event: dict[str, Any],
    ) -> None:
        """Notify project residents on publish (best-effort)."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT c.user_id::text AS user_id
            FROM contacts c
            JOIN contact_units cu ON cu.contact_id = c.id
            JOIN units u ON u.id = cu.unit_id
            WHERE c.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND cu.status = 'active'::contact_unit_status
              AND c.user_id IS NOT NULL
            """,
            self.organization_id,
            project_id,
        )
        user_ids = [str(r["user_id"]) for r in rows]
        await self.notifications.notify_event_published(
            organization_id=self.organization_id,
            event=event,
            recipient_user_ids=user_ids,
        )

    async def _dispatch_cancel_push(self, *, event: dict[str, Any]) -> None:
        """Notify confirmed and waitlisted bookers when event is cancelled (best-effort)."""
        bookers = await self.repo.list_active_bookers_for_notification(
            organization_id=self.organization_id,
            event_id=str(event["id"]),
        )
        for booker in bookers:
            await self.notifications.notify_event_cancelled(
                organization_id=self.organization_id,
                contact_id=str(booker["contact_id"]),
                event=event,
            )
