"""Unit tests for community event booking service."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from apps.user_service.app.schemas.enums import (
    CommunityEventChildTicketMode,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_event_booking_service import (
    CommunityEventBookingService,
)
from libs.shared_utils.http_exceptions import ValidationException


def _published_event(**overrides) -> dict:
    base = {
        "event_type": CommunityEventType.PAID.value,
        "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
        "record_status": CommunityEventRecordStatus.ACTIVE.value,
        "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
        "end_date": date.today() + timedelta(days=2),
        "end_time": None,
        "adult_price_minor": 58900,
        "child_ticket_mode": CommunityEventChildTicketMode.FREE.value,
        "child_price_minor": 0,
        "apply_tax": True,
        "tax_rate": 18.0,
        "tickets_booked": 0,
        "total_capacity": 100,
    }
    base.update(overrides)
    return base


class TestComputeBookingAmounts:
    def test_free_event_zero_amounts(self) -> None:
        event = _published_event(event_type=CommunityEventType.FREE.value)
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=2,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 0
        assert tax == 0
        assert total == 0

    def test_paid_event_with_tax(self) -> None:
        event = _published_event()
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=2,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 117800
        assert tax == 21204
        assert total == 139004

    def test_paid_event_no_tax(self) -> None:
        event = _published_event(apply_tax=False)
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=1,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 58900
        assert tax == 0
        assert total == 58900


class TestBookingOpen:
    def test_closed_when_past_deadline(self) -> None:
        event = _published_event(
            booking_closes_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert CommunityEventBookingService._is_booking_open(event) is False

    def test_open_when_published_and_future_deadline(self) -> None:
        event = _published_event()
        assert CommunityEventBookingService._is_booking_open(event) is True


class TestFacilityValidation:
    def test_rejects_parking_facility(self) -> None:
        with pytest.raises(ValidationException):
            CommunityEventBookingService.validate_facility_for_event(
                {"facility_type": "parking", "status": "active", "active": True}
            )

    def test_accepts_events_facility(self) -> None:
        CommunityEventBookingService.validate_facility_for_event(
            {"facility_type": "events", "status": "active", "active": True}
        )

    def test_accepts_sports_facility(self) -> None:
        CommunityEventBookingService.validate_facility_for_event(
            {"facility_type": "sports", "status": "active", "active": True}
        )
