"""Integration tests for security /v1/projects/{project_id}/walk-ins endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.walk_ins"
_SERVICE = "apps.user_service.app.services.walk_in_service.WalkInService"

PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
ENTRY_ID = "660e8400-e29b-41d4-a716-446655440001"
VISIT_UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
TOWER_ID = "aa0e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "bb0e8400-e29b-41d4-a716-446655440002"
ORG = "org-123"

_CREATE_BODY = {
    "visitor_first_name": "Sushil",
    "visitor_last_name": "Jha",
    "visitor_phone_isd_code": "+91",
    "visitor_phone_number": "9876543210",
    "visitor_photo_paths": ["org/photo.jpg"],
    "vehicle_photo_paths": [],
    "notes": "Delivery",
    "flats": [{"tower_id": TOWER_ID, "unit_id": UNIT_ID}],
}


def _patch_security_access(monkeypatch) -> None:
    """Bypass RBAC for security walk-in routes."""
    patch_check_permissions(monkeypatch, _API, org_id=ORG)


def _fake_detail(**overrides) -> dict:
    """Build walk-in detail payload."""
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc).isoformat()
    data = {
        "id": ENTRY_ID,
        "project_id": PROJECT_ID,
        "visitor_first_name": "Sushil",
        "visitor_last_name": "Jha",
        "visitor_phone_isd_code": "+91",
        "visitor_phone_number": "9876543210",
        "status": "awaiting",
        "flats_count": 1,
        "approved_flats_count": 0,
        "primary_unit_label": "A-2102",
        "notes": "Delivery",
        "requested_at": now,
        "entered_at": None,
        "exited_at": None,
        "visitor_photo_paths": ["org/photo.jpg"],
        "vehicle_photo_paths": [],
        "visit_units": [
            {
                "id": VISIT_UNIT_ID,
                "tower_id": TOWER_ID,
                "unit_id": UNIT_ID,
                "status": "awaiting",
                "sort_order": 0,
            }
        ],
        "events": [],
        "milestones": [],
    }
    data.update(overrides)
    return data


def _fake_summary(**overrides) -> dict:
    """Build walk-in list row."""
    detail = _fake_detail(**overrides)
    detail.pop("visitor_photo_paths", None)
    detail.pop("vehicle_photo_paths", None)
    detail.pop("visit_units", None)
    detail.pop("events", None)
    detail.pop("milestones", None)
    return detail


@pytest.mark.asyncio
async def test_create_walk_in(monkeypatch, client):
    """POST create registers a walk-in visit."""

    _patch_security_access(monkeypatch)

    async def fake_create_walk_in(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.visitor_first_name == "Sushil"
        assert len(body.flats) == 1
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.create_walk_in", fake_create_walk_in)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/walk-ins",
        json=_CREATE_BODY,
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == ENTRY_ID
    assert body["data"]["visit_units"][0]["id"] == VISIT_UNIT_ID


@pytest.mark.asyncio
async def test_create_walk_in_validation_error(monkeypatch, client):
    """POST create returns 422 when unit is invalid."""

    _patch_security_access(monkeypatch)

    async def fake_create_walk_in(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="walk_in.errors.unit_not_in_project",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.create_walk_in", fake_create_walk_in)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/walk-ins",
        json=_CREATE_BODY,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_list_walk_ins(monkeypatch, client):
    """GET list returns walk-in visits for a project."""

    _patch_security_access(monkeypatch)

    async def fake_list_project_walk_ins(_self, *, project_id: str, query):
        del _self
        assert project_id == PROJECT_ID
        assert query.status is None
        return [_fake_summary()]

    monkeypatch.setattr(f"{_SERVICE}.list_project_walk_ins", fake_list_project_walk_ins)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/walk-ins")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == ENTRY_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_walk_ins_empty(monkeypatch, client):
    """GET list returns empty collection."""

    _patch_security_access(monkeypatch)

    async def fake_list_project_walk_ins(_self, **kwargs):
        del _self, kwargs
        return []

    monkeypatch.setattr(f"{_SERVICE}.list_project_walk_ins", fake_list_project_walk_ins)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/walk-ins")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_walk_in(monkeypatch, client):
    """GET detail returns one walk-in visit."""

    _patch_security_access(monkeypatch)

    async def fake_get_project_walk_in(_self, *, project_id: str, walk_in_entry_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert walk_in_entry_id == ENTRY_ID
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.get_project_walk_in", fake_get_project_walk_in)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/walk-ins/{ENTRY_ID}")
    body = assert_success(res, 200)
    assert body["data"]["visitor_first_name"] == "Sushil"


@pytest.mark.asyncio
async def test_get_walk_in_not_found(monkeypatch, client):
    """GET detail returns 404 when walk-in is missing."""

    _patch_security_access(monkeypatch)

    async def fake_get_project_walk_in(_self, **kwargs):
        del _self, kwargs
        raise NotFoundException(
            message_key="walk_in.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_project_walk_in", fake_get_project_walk_in)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/walk-ins/{ENTRY_ID}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_enter_walk_in(monkeypatch, client):
    """POST enter marks visitor physically inside."""

    _patch_security_access(monkeypatch)

    async def fake_enter_walk_in(_self, *, project_id: str, walk_in_entry_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert walk_in_entry_id == ENTRY_ID
        return _fake_detail(status="entered", approved_flats_count=1)

    monkeypatch.setattr(f"{_SERVICE}.enter_walk_in", fake_enter_walk_in)

    res = await client.post(f"/v1/projects/{PROJECT_ID}/walk-ins/{ENTRY_ID}/enter")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "entered"


@pytest.mark.asyncio
async def test_enter_walk_in_validation_error(monkeypatch, client):
    """POST enter returns 422 when no flat is approved."""

    _patch_security_access(monkeypatch)

    async def fake_enter_walk_in(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="walk_in.errors.no_approved_visit_units",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.enter_walk_in", fake_enter_walk_in)

    res = await client.post(f"/v1/projects/{PROJECT_ID}/walk-ins/{ENTRY_ID}/enter")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_exit_walk_in(monkeypatch, client):
    """POST exit marks visitor exited."""

    _patch_security_access(monkeypatch)

    async def fake_exit_walk_in(_self, *, project_id: str, walk_in_entry_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert walk_in_entry_id == ENTRY_ID
        return _fake_detail(status="exited")

    monkeypatch.setattr(f"{_SERVICE}.exit_walk_in", fake_exit_walk_in)

    res = await client.post(f"/v1/projects/{PROJECT_ID}/walk-ins/{ENTRY_ID}/exit")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "exited"
