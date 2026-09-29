"""Integration tests for GET /v1/units/{unit_id}/visitor-activities."""

from __future__ import annotations

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success

_API = "apps.user_service.app.api.visitor_activities_resident"
_SERVICE = (
    "apps.user_service.app.services.resident_visitor_activities_service."
    "ResidentVisitorActivitiesService"
)

CONTACT_ID = "contact-1"
UNIT_ID = "unit-1"
ACTIVITY_ID = "activity-1"


def _patch_contact_context(monkeypatch) -> None:
    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {"id": CONTACT_ID}

    monkeypatch.setattr(
        f"{_API}.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


def _fake_list_item(**overrides) -> dict:
    item = {
        "source": "pass",
        "id": ACTIVITY_ID,
        "type": "guest",
        "sub_type": None,
        "visitor_name": "Guest User",
        "visit_status": "exited",
        "visitor_type": "guest",
        "is_private": False,
        "visitor_photo_urls": [],
        "vehicle_photo_urls": [],
    }
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_list_resident_visitor_activities(client, monkeypatch):
    _patch_contact_context(monkeypatch)

    async def fake_list_activities(self, **kwargs):
        del self, kwargs
        return [_fake_list_item()], 1

    monkeypatch.setattr(f"{_SERVICE}.list_activities", fake_list_activities)

    response = await client.get(f"/v1/units/{UNIT_ID}/visitor-activities")
    body = assert_success(response)
    assert body["data"][0]["id"] == ACTIVITY_ID
    assert body["data"][0]["type"] == "guest"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_resident_visitor_activity_detail(client, monkeypatch):
    _patch_contact_context(monkeypatch)

    async def fake_get_activity_detail(self, **kwargs):
        del self, kwargs
        return {
            "source": "pass",
            "id": ACTIVITY_ID,
            "unit_id": UNIT_ID,
            "type": "guest",
            "sub_type": None,
            "visitor_name": "Guest User",
            "visitor_count": 1,
            "validity_type": "one_time",
            "allow_multiple_entries": False,
            "is_private": False,
            "entry_count": 1,
            "status": "completed",
            "display_status": "completed",
            "pass_code": "4821",
            "visit_status": "exited",
            "visitor_type": "guest",
            "events": [],
            "image_urls": [],
        }

    monkeypatch.setattr(f"{_SERVICE}.get_activity_detail", fake_get_activity_detail)

    response = await client.get(f"/v1/units/{UNIT_ID}/visitor-activities/{ACTIVITY_ID}")
    body = assert_success(response)
    assert body["data"]["pass_code"] == "4821"
    assert body["data"]["unit_id"] == UNIT_ID
