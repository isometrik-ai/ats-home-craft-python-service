"""Integration tests for community events admin API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.community_events import CommunityEventSummaryResponse
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)

_API = "apps.user_service.app.api.community_events"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
ORG = "org-123"


def _patch_admin_access(monkeypatch) -> None:
    patch_ensure_staff_project_access(monkeypatch, _API, org_id=ORG)


@pytest.mark.asyncio
async def test_get_community_events_summary(client, monkeypatch) -> None:
    _patch_admin_access(monkeypatch)
    summary = CommunityEventSummaryResponse(
        total_events=1,
        upcoming=1,
        total_rsvps=0,
        revenue_collected_minor=0,
        revenue_currency="INR",
        tabs={"all": 1},
    )
    monkeypatch.setattr(
        f"{_API}.CommunityEventsService.get_summary",
        AsyncMock(return_value=summary),
    )
    response = await client.get(f"/v1/projects/{PROJECT_ID}/community-events/summary")
    assert response.status_code == 200
    assert response.json()["data"]["total_events"] == 1
