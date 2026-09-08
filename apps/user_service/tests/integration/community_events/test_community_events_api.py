"""Integration tests for community events admin API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.community_events import CommunityEventSummaryResponse
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.community_events"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
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


@pytest.mark.asyncio
async def test_export_community_event_revenue_report_success(client, monkeypatch) -> None:
    """GET revenue/export returns CSV attachment."""
    _patch_admin_access(monkeypatch)

    async def fake_export_revenue_csv(_self, *, project_id, event_id):
        del _self
        assert project_id == PROJECT_ID
        assert event_id == EVENT_ID
        return (
            "event_display_code,event_title,currency\n"
            "EVT-8,Brazil fest,INR\n"
            "collected_minor,pending_minor\n"
            "64000,0\n"
            "\n"
            "resident,amount_minor,status,txn_ref\n"
            "Ms. Rasika Bharati,22000,Paid,BKG-34\n"
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.community_events_service.CommunityEventsService.export_revenue_csv",
        fake_export_revenue_csv,
    )

    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/community-events/{EVENT_ID}/revenue/export"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers.get("content-disposition", "")
    assert f"event-revenue-{EVENT_ID}.csv" in response.headers.get("content-disposition", "")
    assert "Brazil fest" in response.text
    assert "BKG-34" in response.text


@pytest.mark.asyncio
async def test_export_community_event_revenue_report_event_not_found(client, monkeypatch) -> None:
    """GET revenue/export returns 404 when event is missing."""
    _patch_admin_access(monkeypatch)

    async def fake_export_revenue_csv(_self, *, project_id, event_id):
        del _self, project_id, event_id
        raise NotFoundException(
            message_key="community_events.errors.event_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.community_events_service.CommunityEventsService.export_revenue_csv",
        fake_export_revenue_csv,
    )

    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/community-events/{EVENT_ID}/revenue/export"
    )
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert body["message"] == "The event could not be found."
