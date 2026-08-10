"""Integration tests for visitor logs endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import (
    patch_check_permissions,
    patch_ensure_staff_project_access_optional,
)
from apps.user_service.tests.utils.assertions import assert_success

PASS_ID = "pass-1"

_FAKE_LOG_ITEM = {
    "source": "pass",
    "pass_id": PASS_ID,
    "pass_type": "guest",
    "guest_name": "Mayur Sharma",
    "visitor_phone_isd_code": "+91",
    "visitor_phone_number": "9876543210",
    "unit_label": "B-1204",
    "tower_name": "Tower B",
    "created_by": "T. Nair",
    "scheduled_from": "2026-06-09T09:00:00Z",
    "scheduled_until": "2026-06-09T18:00:00Z",
    "validity_type": "one_time",
    "entry_method": "qr",
    "guard_name": "Ramesh Kumar",
    "access_status": "approved",
    "visit_status": "exited",
    "visitor_type": "guest",
    "pass_code": "4821",
    "is_private": False,
    "in_time": "2026-06-09T09:12:00Z",
    "out_time": "2026-06-09T09:18:00Z",
    "time_spent_minutes": 6,
    "pass_image_url": None,
    "visitor_photo_urls": [],
    "vehicle_photo_urls": [],
    "resident": None,
}

_FAKE_OVERVIEW = {
    "start_at": "2026-06-01T00:00:00Z",
    "end_at": "2026-06-30T00:00:00Z",
    "total_entries": 28,
    "inside_now": 3,
    "awaiting_approval": 1,
    "walk_ins": 6,
    "denied_expired": 3,
}

_FAKE_DETAIL = {
    "id": PASS_ID,
    "pass_type": "guest",
    "guest_name": "Ravi Kumar",
    "status": "active",
    "code": "4821",
    "visit_status": "exited",
    "events": [{"id": "evt-1", "event_type": "check_in"}],
}


@pytest.mark.asyncio
async def test_list_visitor_logs(monkeypatch, client):
    """GET visitor-logs returns paginated visitor log rows."""

    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.visitor_logs"
    )

    async def fake_list_logs(
        _self,
        *,
        start_at=None,
        end_at=None,
        search=None,
        bucket=None,
        visitor_type=None,
        pass_type=None,
        entry_method=None,
        access_status=None,
        tower_id=None,
        guard_user_id=None,
        project_id=None,
        unit_id=None,
        page=1,
        page_size=20,
    ):
        del _self, start_at, end_at, search, bucket, visitor_type, pass_type, entry_method
        del access_status, tower_id, guard_user_id, project_id, unit_id
        assert page == 1
        assert page_size == 20
        return [_FAKE_LOG_ITEM], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.visitor_logs_service.VisitorLogsService.list_logs",
        fake_list_logs,
    )

    res = await client.get("/v1/visitor-logs", params={"page": 1, "page_size": 20})
    body = assert_success(res, 200)
    assert body["data"][0]["pass_id"] == PASS_ID
    assert body["total"] == 1
    assert body["data"][0]["time_spent_minutes"] == 6
    assert body["data"][0]["validity_type"] == "one_time"


@pytest.mark.asyncio
async def test_get_visitor_log_overview(monkeypatch, client):
    """GET visitor-logs/overview returns overview card metrics."""

    patch_ensure_staff_project_access_optional(
        monkeypatch, "apps.user_service.app.api.visitor_logs"
    )

    async def fake_get_overview(
        _self,
        *,
        start_at=None,
        end_at=None,
        project_id=None,
        unit_id=None,
    ):
        del _self, start_at, end_at, project_id, unit_id
        return _FAKE_OVERVIEW

    monkeypatch.setattr(
        "apps.user_service.app.services.visitor_logs_service.VisitorLogsService.get_overview",
        fake_get_overview,
    )

    res = await client.get("/v1/visitor-logs/overview")
    body = assert_success(res, 200)
    assert body["data"]["total_entries"] == 28
    assert body["data"]["walk_ins"] == 6


@pytest.mark.asyncio
async def test_get_visitor_log_detail(monkeypatch, client):
    """GET visitor-logs/{pass_id} returns pass detail with timeline."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.visitor_logs")

    async def fake_get_log_detail(_self, *, pass_id: str):
        del _self
        assert pass_id == PASS_ID
        return _FAKE_DETAIL

    monkeypatch.setattr(
        "apps.user_service.app.services.visitor_logs_service.VisitorLogsService.get_log_detail",
        fake_get_log_detail,
    )

    res = await client.get(f"/v1/visitor-logs/{PASS_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == PASS_ID
    assert body["data"]["events"][0]["event_type"] == "check_in"
