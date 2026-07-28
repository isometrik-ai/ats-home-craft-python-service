"""Integration tests for resident /v1/walk-ins endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.walk_ins_owner"
_SERVICE = "apps.user_service.app.services.walk_in_service.WalkInService"

CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
ENTRY_ID = "660e8400-e29b-41d4-a716-446655440001"
VISIT_UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
TOWER_ID = "aa0e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "bb0e8400-e29b-41d4-a716-446655440002"


def _patch_resident_context(monkeypatch) -> None:
    """Patch onboarding contact context for resident walk-in routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {
            "id": CONTACT_ID,
            "prefix": "Mr",
            "first_name": "Resident",
            "last_name": "Owner",
            "contact_type": "owner",
        }

    monkeypatch.setattr(
        f"{_API}.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


def _fake_detail(**overrides) -> dict:
    """Build walk-in detail payload."""
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc).isoformat()
    data = {
        "id": ENTRY_ID,
        "project_id": "proj-1",
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


def _fake_visit_unit_item(**overrides) -> dict:
    """Build resident visit unit list row."""
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc).isoformat()
    data = {
        "visit_unit_id": VISIT_UNIT_ID,
        "walk_in_entry_id": ENTRY_ID,
        "tower_id": TOWER_ID,
        "unit_id": UNIT_ID,
        "tower_name": "Sunflower",
        "unit_code": "A-2102",
        "unit_label": "A-2102",
        "status": "awaiting",
        "visitor_first_name": "Sushil",
        "visitor_last_name": "Jha",
        "visitor_phone_isd_code": "+91",
        "visitor_phone_number": "9876543210",
        "visitor_photo_paths": ["org/photo.jpg"],
        "notes": "Delivery",
        "requested_at": now,
        "flats_count": 1,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_list_resident_visit_units(monkeypatch, client):
    """GET visit-units returns pending rows for resident flats."""

    _patch_resident_context(monkeypatch)

    async def fake_list_resident_visit_units(_self, *, contact_id: str, query):
        del _self
        assert contact_id == CONTACT_ID
        assert query.status is None
        return [_fake_visit_unit_item()]

    monkeypatch.setattr(f"{_SERVICE}.list_resident_visit_units", fake_list_resident_visit_units)

    res = await client.get("/v1/walk-ins/visit-units")
    body = assert_success(res, 200)
    assert body["data"][0]["visit_unit_id"] == VISIT_UNIT_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_resident_visit_units_empty(monkeypatch, client):
    """GET visit-units returns empty collection."""

    _patch_resident_context(monkeypatch)

    async def fake_list_resident_visit_units(_self, **kwargs):
        del _self, kwargs
        return []

    monkeypatch.setattr(f"{_SERVICE}.list_resident_visit_units", fake_list_resident_visit_units)

    res = await client.get("/v1/walk-ins/visit-units")
    body = assert_success(res, 200)
    assert body["data"] == []


@pytest.mark.asyncio
async def test_get_resident_walk_in(monkeypatch, client):
    """GET detail returns walk-in for resident flat."""

    _patch_resident_context(monkeypatch)

    async def fake_get_resident_walk_in(_self, *, contact_id: str, walk_in_entry_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert walk_in_entry_id == ENTRY_ID
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.get_resident_walk_in", fake_get_resident_walk_in)

    res = await client.get(f"/v1/walk-ins/{ENTRY_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == ENTRY_ID


@pytest.mark.asyncio
async def test_get_resident_walk_in_not_found(monkeypatch, client):
    """GET detail returns 404 when resident has no linked flat."""

    _patch_resident_context(monkeypatch)

    async def fake_get_resident_walk_in(_self, **kwargs):
        del _self, kwargs
        raise NotFoundException(
            message_key="walk_in.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_resident_walk_in", fake_get_resident_walk_in)

    res = await client.get(f"/v1/walk-ins/{ENTRY_ID}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_approve_visit_unit(monkeypatch, client):
    """POST approve marks visit unit approved."""

    _patch_resident_context(monkeypatch)

    async def fake_approve_visit_unit(
        _self,
        *,
        contact_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
        actor_label=None,
    ):
        del _self
        assert contact_id == CONTACT_ID
        assert walk_in_entry_id == ENTRY_ID
        assert visit_unit_id == VISIT_UNIT_ID
        assert actor_label == "Mr Resident Owner"
        return _fake_detail(status="approved", approved_flats_count=1)

    monkeypatch.setattr(f"{_SERVICE}.approve_visit_unit", fake_approve_visit_unit)

    res = await client.post(f"/v1/walk-ins/{ENTRY_ID}/visit-units/{VISIT_UNIT_ID}/approve")
    body = assert_success(res, 200)
    assert body["data"]["approved_flats_count"] == 1


@pytest.mark.asyncio
async def test_reject_visit_unit(monkeypatch, client):
    """POST reject marks visit unit rejected with reason."""

    _patch_resident_context(monkeypatch)

    async def fake_reject_visit_unit(
        _self,
        *,
        contact_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
        body,
        actor_label=None,
    ):
        del _self
        assert contact_id == CONTACT_ID
        assert walk_in_entry_id == ENTRY_ID
        assert visit_unit_id == VISIT_UNIT_ID
        assert body.rejection_reason == "Not expecting anyone"
        assert actor_label == "Mr Resident Owner"
        return _fake_detail(status="awaiting", approved_flats_count=0)

    monkeypatch.setattr(f"{_SERVICE}.reject_visit_unit", fake_reject_visit_unit)

    res = await client.post(
        f"/v1/walk-ins/{ENTRY_ID}/visit-units/{VISIT_UNIT_ID}/reject",
        json={"rejection_reason": "Not expecting anyone"},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "awaiting"


@pytest.mark.asyncio
async def test_reject_visit_unit_validation_error(monkeypatch, client):
    """POST reject returns 422 when visit unit is not awaiting."""

    _patch_resident_context(monkeypatch)

    async def fake_reject_visit_unit(_self, **kwargs):
        del _self, kwargs
        raise ValidationException(
            message_key="walk_in.errors.visit_unit_not_awaiting",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.reject_visit_unit", fake_reject_visit_unit)

    res = await client.post(
        f"/v1/walk-ins/{ENTRY_ID}/visit-units/{VISIT_UNIT_ID}/reject",
        json={"rejection_reason": "Too late"},
    )
    assert res.status_code == 422
