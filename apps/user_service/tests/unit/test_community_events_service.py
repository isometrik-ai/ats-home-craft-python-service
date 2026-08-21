"""Unit tests for community events admin service helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import CommunityEventPublishStatus
from apps.user_service.app.services.community_events_service import (
    CommunityEventsService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException


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


class TestValidateFacility:
    @pytest.mark.asyncio
    async def test_rejects_non_uuid_facility_id(self) -> None:
        """Display codes like FAC-04 must not reach the database as UUID casts."""
        service = CommunityEventsService(
            db_connection=MagicMock(),
            user_context=UserContext(
                user_id="u1",
                email="a@b.com",
                organization_id="org-123",
                user_type="admin",
            ),
        )
        service.facilities_repo.get_facility = AsyncMock()

        with pytest.raises(ValidationException):
            await service._validate_facility(
                project_id="990e8400-e29b-41d4-a716-446655440004",
                facility_id="FAC-04",
                required=True,
            )

        service.facilities_repo.get_facility.assert_not_awaited()
