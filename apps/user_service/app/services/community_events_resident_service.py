"""Resident community events views and booking orchestration."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)
from apps.user_service.app.schemas.community_events import (
    BookEventResponse,
    CommunityEventMediaResponse,
    CreateEventBookingRequest,
    MyBookingItemResponse,
    MyBookingsSummaryResponse,
    ResidentEventDetailResponse,
    ResidentEventListItemResponse,
    ResidentEventListQuery,
)
from apps.user_service.app.schemas.enums import (
    COMMUNITY_EVENT_CATEGORY_LABELS,
    CommunityEventType,
)
from apps.user_service.app.services.community_event_booking_service import (
    CommunityEventBookingService,
)
from apps.user_service.app.services.community_events_service import (
    CommunityEventsService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class CommunityEventsResidentService:
    """Resident list, detail, and my bookings (project-scoped)."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = CommunityEventsRepository(db_connection)
        self.booking_service = CommunityEventBookingService(
            db_connection=db_connection,
            user_context=user_context,
        )

    @property
    def organization_id(self) -> str:
        """Organization id from context."""
        return self.user_context.organization_id

    @staticmethod
    def _price_label(row: dict[str, Any]) -> str:
        """Format price for cards."""
        if str(row.get("event_type")) == CommunityEventType.FREE.value:
            return "Free"
        adult = int(row.get("adult_price_minor") or 0)
        if adult <= 0:
            return "Free"
        return f"From ₹{adult // 100:,}"

    @staticmethod
    def _booking_state(row: dict[str, Any]) -> str:
        """Derive booking_state for resident UI."""
        my_tickets = int(row.get("my_tickets_count") or 0)
        if my_tickets > 0:
            return "booked"
        if not CommunityEventBookingService._is_booking_open(row):
            return "closed"
        capacity = row.get("total_capacity")
        booked = int(row.get("tickets_booked") or 0)
        if capacity is not None and booked >= int(capacity):
            return "closed"
        return "open"

    @staticmethod
    def _cta(row: dict[str, Any], booking_state: str) -> str:
        """Derive CTA button."""
        if booking_state == "booked":
            return "view_tickets"
        if booking_state == "closed":
            return "closed"
        return "book"

    def _serialize_list_item(self, row: dict[str, Any]) -> ResidentEventListItemResponse:
        """Map row to resident card."""
        category = str(row.get("category") or "")
        booking_state = self._booking_state(row)
        return ResidentEventListItemResponse(
            id=str(row["id"]),
            title=str(row["title"]),
            category=category,
            category_label=COMMUNITY_EVENT_CATEGORY_LABELS.get(category, category),
            price_label=self._price_label(row),
            start_date=row["start_date"],
            end_date=row["end_date"],
            start_time=row.get("start_time"),
            end_time=row.get("end_time"),
            is_multi_day=bool(row.get("is_multi_day")),
            facility_name=row.get("facility_name"),
            location_label=CommunityEventsService._facility_location_label(row),
            tickets_booked=int(row.get("tickets_booked") or 0),
            total_capacity=row.get("total_capacity"),
            cover_image_path=row.get("cover_image_path"),
            booking_state=booking_state,
            my_tickets_count=int(row.get("my_tickets_count") or 0),
            cta=self._cta(row, booking_state),
        )

    async def list_events(
        self,
        *,
        project_id: str,
        contact_id: str,
        query: ResidentEventListQuery,
    ) -> tuple[list[ResidentEventListItemResponse], int]:
        """Resident upcoming/past list for a project."""
        await self.booking_service._ensure_resident_project(
            contact_id=contact_id,
            project_id=project_id,
        )
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_resident_events(
            organization_id=self.organization_id,
            project_id=project_id,
            timeframe=query.timeframe.value,
            category=query.category.value if query.category else None,
            search=query.search,
            contact_id=contact_id,
            limit=query.page_size,
            offset=offset,
        )
        return [self._serialize_list_item(row) for row in rows], total

    async def get_event_detail(
        self,
        *,
        project_id: str,
        contact_id: str,
        event_id: str,
    ) -> ResidentEventDetailResponse:
        """Resident event detail."""
        await self.booking_service._ensure_resident_project(
            contact_id=contact_id,
            project_id=project_id,
        )
        row = await self.repo.fetch_resident_event_by_id(
            organization_id=self.organization_id,
            event_id=event_id,
        )
        if not row or str(row.get("project_id")) != project_id:
            raise NotFoundException(
                message_key="community_events.errors.event_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        gallery = await self.repo.list_gallery(
            organization_id=self.organization_id,
            event_id=event_id,
        )
        my_booking = await self.repo.get_my_booking_for_event(
            organization_id=self.organization_id,
            event_id=event_id,
            contact_id=contact_id,
        )
        my_tickets = int(my_booking.get("total_tickets") or 0) if my_booking else 0
        booking_state = self._booking_state({**row, "my_tickets_count": my_tickets})
        category = str(row.get("category") or "")
        return ResidentEventDetailResponse(
            id=str(row["id"]),
            title=str(row["title"]),
            description=str(row.get("description") or ""),
            category=category,
            category_label=COMMUNITY_EVENT_CATEGORY_LABELS.get(category, category),
            start_date=row["start_date"],
            end_date=row["end_date"],
            start_time=row.get("start_time"),
            end_time=row.get("end_time"),
            is_multi_day=bool(row.get("is_multi_day")),
            facility_name=row.get("facility_name"),
            location_label=CommunityEventsService._facility_location_label(row),
            facility_type=row.get("facility_type"),
            facility_subtype=row.get("facility_subtype"),
            location_notes=row.get("location_notes"),
            event_type=str(row.get("event_type") or ""),
            adult_price_minor=int(row.get("adult_price_minor") or 0),
            child_ticket_mode=str(row.get("child_ticket_mode") or ""),
            child_price_minor=int(row.get("child_price_minor") or 0),
            apply_tax=bool(row.get("apply_tax")),
            tax_rate=float(row.get("tax_rate") or 18),
            currency=str(row.get("currency") or "INR"),
            max_tickets_per_resident=int(row.get("max_tickets_per_resident") or 4),
            booking_closes_at=row.get("booking_closes_at"),
            booking_state=booking_state,
            tickets_booked=int(row.get("tickets_booked") or 0),
            total_capacity=row.get("total_capacity"),
            cover_image_path=row.get("cover_image_path"),
            gallery=[CommunityEventMediaResponse(**g) for g in gallery],
            my_tickets_count=my_tickets,
            my_booking_id=str(my_booking["id"]) if my_booking else None,
            price_label=self._price_label(row),
            cta=self._cta(row, booking_state),
        )

    async def book_event(
        self,
        *,
        project_id: str,
        contact_id: str,
        event_id: str,
        body: CreateEventBookingRequest,
    ) -> BookEventResponse:
        """Delegate booking create."""
        return await self.booking_service.create_booking(
            project_id=project_id,
            contact_id=contact_id,
            event_id=event_id,
            body=body,
        )

    async def get_my_booking_summary(
        self,
        *,
        project_id: str,
        contact_id: str,
    ) -> MyBookingsSummaryResponse:
        """Badge counts for resident in a project."""
        await self.booking_service._ensure_resident_project(
            contact_id=contact_id,
            project_id=project_id,
        )
        counts = await self.repo.sum_my_active_tickets(
            organization_id=self.organization_id,
            project_id=project_id,
            contact_id=contact_id,
        )
        return MyBookingsSummaryResponse(
            active_ticket_count=counts["active_ticket_count"],
            active_booking_count=counts["active_booking_count"],
        )

    async def list_my_bookings(
        self,
        *,
        project_id: str,
        contact_id: str,
    ) -> list[MyBookingItemResponse]:
        """All active bookings for resident in a project."""
        await self.booking_service._ensure_resident_project(
            contact_id=contact_id,
            project_id=project_id,
        )
        rows = await self.repo.list_my_bookings(
            organization_id=self.organization_id,
            project_id=project_id,
            contact_id=contact_id,
        )
        return [
            MyBookingItemResponse(
                booking_id=str(r["booking_id"]),
                display_code=str(r["display_code"]),
                event_id=str(r["event_id"]),
                event_title=str(r["event_title"]),
                event_start_date=r["event_start_date"],
                cover_image_path=r.get("cover_image_path"),
                total_tickets=int(r.get("total_tickets") or 0),
                total_amount_minor=int(r.get("total_amount_minor") or 0),
                payment_status=str(r.get("payment_status") or ""),
                booking_status=str(r.get("booking_status") or ""),
                gate_qr_token=r.get("gate_qr_token"),
            )
            for r in rows
        ]

    async def cancel_booking(
        self,
        *,
        project_id: str,
        contact_id: str,
        booking_id: str,
    ) -> None:
        """Resident cancel booking."""
        await self.booking_service.cancel_booking(
            contact_id=contact_id,
            project_id=project_id,
            booking_id=booking_id,
        )

    async def get_my_booking_for_event(
        self,
        *,
        project_id: str,
        contact_id: str,
        event_id: str,
    ) -> MyBookingItemResponse | None:
        """Single event booking for resident."""
        await self.booking_service._ensure_resident_project(
            contact_id=contact_id,
            project_id=project_id,
        )
        row = await self.repo.get_my_booking_for_event(
            organization_id=self.organization_id,
            event_id=event_id,
            contact_id=contact_id,
        )
        if not row:
            return None
        if str(row.get("project_id")) != project_id:
            return None
        event = await self.repo.fetch_resident_event_by_id(
            organization_id=self.organization_id,
            event_id=event_id,
        )
        return MyBookingItemResponse(
            booking_id=str(row["id"]),
            display_code=str(row["display_code"]),
            event_id=event_id,
            event_title=str(event.get("title") or "") if event else "",
            event_start_date=event["start_date"] if event else row["booked_at"].date(),
            cover_image_path=event.get("cover_image_path") if event else None,
            total_tickets=int(row.get("total_tickets") or 0),
            total_amount_minor=int(row.get("total_amount_minor") or 0),
            payment_status=str(row.get("payment_status") or ""),
            booking_status=str(row.get("booking_status") or ""),
            gate_qr_token=row.get("gate_qr_token"),
        )
