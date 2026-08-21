"""Integration tests for resident community events API."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.community_events import ResidentEventListItemResponse
from apps.user_service.app.utils.common_utils import UserContext

_API = "apps.user_service.app.api.community_events_resident"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
CONTACT_ID = "880e8400-e29b-41d4-a716-446655440001"
ORG = "org-123"


def _patch_resident_context(monkeypatch) -> None:
    user_context = UserContext(
        user_id="test-user-id",
        email="test@example.com",
        organization_id=ORG,
        user_type="contact",
    )
    contact = {"id": CONTACT_ID, "organization_id": ORG}

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return user_context, contact

    monkeypatch.setattr(
        f"{_API}.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


@pytest.mark.asyncio
async def test_list_resident_community_events(client, monkeypatch) -> None:
    _patch_resident_context(monkeypatch)
    item = ResidentEventListItemResponse(
        id="770e8400-e29b-41d4-a716-446655440001",
        title="Summer Fest",
        category="social",
        category_label="Social",
        start_date=date.today(),
        end_date=date.today(),
        is_multi_day=False,
        facility_name="Clubhouse",
        location_label="Tower A",
        tickets_booked=10,
        total_capacity=100,
        price_label="Free",
        booking_state="open",
        cta="book",
    )
    monkeypatch.setattr(
        f"{_API}.CommunityEventsResidentService.list_events",
        AsyncMock(return_value=([item], 1)),
    )
    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/resident/community-events?timeframe=upcoming"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["title"] == "Summer Fest"
