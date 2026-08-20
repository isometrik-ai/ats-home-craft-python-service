"""Unit tests for community events resident service helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from apps.user_service.app.schemas.enums import (
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_events_resident_service import (
    CommunityEventsResidentService,
)


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
