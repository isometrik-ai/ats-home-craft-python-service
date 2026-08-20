"""Unit tests for community events admin service helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apps.user_service.app.schemas.enums import CommunityEventPublishStatus
from apps.user_service.app.services.community_events_service import (
    CommunityEventsService,
)


class TestDeriveBookingState:
    def test_closed_when_not_published(self) -> None:
        row = {"publish_status": CommunityEventPublishStatus.DRAFT.value}
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_closed_when_past_booking_deadline(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_closed_when_at_capacity(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
            "total_capacity": 50,
            "tickets_booked": 50,
        }
        assert CommunityEventsService._derive_booking_state(row) == "closed"

    def test_open_when_published_with_capacity(self) -> None:
        row = {
            "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
            "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
            "total_capacity": 50,
            "tickets_booked": 10,
        }
        assert CommunityEventsService._derive_booking_state(row) == "open"
