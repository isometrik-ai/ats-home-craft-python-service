"""Unit tests for community events resident service helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.community_events import (
    CreateEventBookingRequest,
    ResidentEventListQuery,
)
from apps.user_service.app.schemas.enums import (
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
    ResidentEventTimeframe,
)
from apps.user_service.app.services.community_events_resident_service import (
    CommunityEventsResidentService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"
BOOKING_ID = "55555555-5555-5555-5555-555555555555"


def _service() -> CommunityEventsResidentService:
    svc = CommunityEventsResidentService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="resident@example.com",
            organization_id="org-1",
        ),
    )
    svc.repo = MagicMock()
    svc.booking_service = MagicMock()
    svc.booking_service._ensure_resident_project = AsyncMock()
    return svc


def _event_row(**overrides) -> dict:
    base = {
        "event_type": CommunityEventType.FREE.value,
        "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
        "record_status": CommunityEventRecordStatus.ACTIVE.value,
        "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
        "end_date": date.today() + timedelta(days=2),
        "end_time": None,
        "adult_price_minor": 0,
        "tickets_booked": 10,
        "total_capacity": 100,
        "my_tickets_count": 0,
        "category": "social",
        "title": "Test Event",
        "start_date": date.today(),
        "is_multi_day": False,
    }
    base.update(overrides)
    return base


class TestResidentHelpers:
    def test_price_label_free(self) -> None:
        assert CommunityEventsResidentService._price_label(_event_row()) == "Free"

    def test_price_label_from_amount(self) -> None:
        row = _event_row(
            event_type=CommunityEventType.PAID.value,
            adult_price_minor=58900,
        )
        assert CommunityEventsResidentService._price_label(row) == "From ₹589"

    def test_cta_book_when_open(self) -> None:
        row = _event_row()
        state = CommunityEventsResidentService._booking_state(row)
        assert CommunityEventsResidentService._cta(row, state) == "book"

    def test_cta_view_tickets_when_booked(self) -> None:
        row = _event_row(my_tickets_count=2)
        state = CommunityEventsResidentService._booking_state(row)
        assert state == "booked"
        assert CommunityEventsResidentService._cta(row, state) == "view_tickets"

    def test_cta_closed_when_full(self) -> None:
        row = _event_row(tickets_booked=100, total_capacity=100)
        state = CommunityEventsResidentService._booking_state(row)
        assert state == "closed"
        assert CommunityEventsResidentService._cta(row, state) == "closed"


@pytest.mark.asyncio
async def test_list_events_and_serialize():
    svc = _service()
    svc.repo.list_resident_events = AsyncMock(
        return_value=(
            [
                {
                    **_event_row(),
                    "id": EVENT_ID,
                    "facility_name": "Clubhouse",
                }
            ],
            1,
        )
    )
    items, total = await svc.list_events(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        query=ResidentEventListQuery(
            timeframe=ResidentEventTimeframe.UPCOMING,
        ),
    )
    assert total == 1
    assert items[0].cta == "book"


@pytest.mark.asyncio
async def test_get_event_detail_success():
    svc = _service()
    svc.repo.fetch_resident_event_by_id = AsyncMock(
        return_value={
            **_event_row(),
            "id": EVENT_ID,
            "project_id": PROJECT_ID,
            "description": "Fun day",
            "child_ticket_mode": "not_applicable",
            "apply_tax": False,
            "tax_rate": 18.0,
            "currency": "INR",
            "max_tickets_per_resident": 4,
        }
    )
    svc.repo.list_gallery = AsyncMock(return_value=[])
    svc.repo.get_my_booking_for_event = AsyncMock(return_value=None)

    detail = await svc.get_event_detail(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        event_id=EVENT_ID,
    )
    assert detail.title == "Test Event"
    assert detail.booking_state == "open"


@pytest.mark.asyncio
async def test_get_event_detail_not_found():
    svc = _service()
    svc.repo.fetch_resident_event_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_event_detail(
            project_id=PROJECT_ID,
            contact_id=CONTACT_ID,
            event_id=EVENT_ID,
        )


@pytest.mark.asyncio
async def test_book_event_delegates():
    svc = _service()
    svc.booking_service.create_booking = AsyncMock(
        return_value=MagicMock(id=BOOKING_ID, booking_status="confirmed")
    )
    result = await svc.book_event(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        event_id=EVENT_ID,
        body=CreateEventBookingRequest(adult_tickets=1),
    )
    assert result.id == BOOKING_ID


@pytest.mark.asyncio
async def test_my_bookings_summary_and_list():
    svc = _service()
    svc.repo.sum_my_active_tickets = AsyncMock(
        return_value={"active_ticket_count": 3, "active_booking_count": 1}
    )
    svc.repo.list_my_bookings = AsyncMock(
        return_value=[
            {
                "booking_id": BOOKING_ID,
                "display_code": "BKG-1",
                "event_id": EVENT_ID,
                "event_title": "Test Event",
                "event_start_date": date.today(),
                "total_tickets": 2,
                "total_amount_minor": 0,
                "payment_status": "paid",
                "booking_status": "confirmed",
                "gate_qr_token": "token",
            }
        ]
    )

    summary = await svc.get_my_booking_summary(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
    )
    assert summary.active_ticket_count == 3

    bookings = await svc.list_my_bookings(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
    )
    assert bookings[0].display_code == "BKG-1"


@pytest.mark.asyncio
async def test_cancel_and_get_my_booking_for_event():
    svc = _service()
    svc.booking_service.cancel_booking = AsyncMock()
    svc.repo.get_my_booking_for_event = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "project_id": PROJECT_ID,
            "display_code": "BKG-1",
            "total_tickets": 2,
            "total_amount_minor": 0,
            "payment_status": "paid",
            "booking_status": "confirmed",
            "gate_qr_token": "token",
            "booked_at": datetime.now(timezone.utc),
        }
    )
    svc.repo.fetch_resident_event_by_id = AsyncMock(
        return_value={**_event_row(), "id": EVENT_ID, "title": "Test Event"}
    )

    await svc.cancel_booking(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        booking_id=BOOKING_ID,
    )
    svc.booking_service.cancel_booking.assert_awaited_once()

    booking = await svc.get_my_booking_for_event(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        event_id=EVENT_ID,
    )
    assert booking is not None
    assert booking.display_code == "BKG-1"

    svc.repo.get_my_booking_for_event = AsyncMock(return_value=None)
    assert (
        await svc.get_my_booking_for_event(
            project_id=PROJECT_ID,
            contact_id=CONTACT_ID,
            event_id=EVENT_ID,
        )
        is None
    )
