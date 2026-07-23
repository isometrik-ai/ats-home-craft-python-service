"""Integration tests for visitor passes endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success

CONTACT_ID = "contact-1"
PASS_ID = "pass-1"

_CREATE_PAYLOAD = {
    "unit_id": "unit-1",
    "guest_name": "Guest User",
    "valid_from": "2026-07-22T10:00:00Z",
    "valid_until": "2026-07-22T18:00:00Z",
}

_FAKE_PASS = {
    "id": PASS_ID,
    "organization_id": "org-123",
    "project_id": "proj-1",
    "unit_id": "unit-1",
    "host_contact_id": CONTACT_ID,
    "pass_type": "guest",
    "guest_name": "Guest User",
    "status": "active",
    "display_status": "active",
    "code": "4821",
    "valid_from": "2026-07-22T10:00:00Z",
    "valid_until": "2026-07-22T18:00:00Z",
}

_FAKE_LIST_ITEM = {
    "id": PASS_ID,
    "code": "4821",
    "guest_name": "Guest User",
    "pass_type": "guest",
    "unit_id": "unit-1",
    "unit_label": "A-1203",
    "tower_name": "Tower A",
    "valid_from": "2026-07-22T10:00:00Z",
    "valid_until": "2026-07-22T18:00:00Z",
    "validity_type": "one_time",
    "status": "active",
    "display_status": "active",
    "entry_count": 0,
    "is_private": False,
}


def _patch_contact_context(monkeypatch) -> None:
    """Patch onboarding contact context for resident pass routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {"id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.api.passes.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


@pytest.mark.asyncio
async def test_list_passes(monkeypatch, client):
    """GET passes lists visitor passes for the authenticated resident."""

    _patch_contact_context(monkeypatch)

    async def fake_list_passes(
        _self,
        *,
        contact_id: str,
        bucket=None,
        display_status=None,
        unit_id=None,
        pass_type=None,
        page=1,
        page_size=20,
    ):
        del _self, bucket, display_status, unit_id, pass_type
        assert contact_id == CONTACT_ID
        assert page == 1
        assert page_size == 20
        return [_FAKE_LIST_ITEM], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.passes_service.PassesService.list_passes",
        fake_list_passes,
    )

    res = await client.get("/v1/passes", params={"page": 1, "page_size": 20})
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == PASS_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_create_pass(monkeypatch, client):
    """POST passes creates a visitor pass for a guest."""

    _patch_contact_context(monkeypatch)

    async def fake_create_pass(_self, *, contact_id: str, body):
        del _self
        assert contact_id == CONTACT_ID
        assert body.guest_name == "Guest User"
        assert body.unit_id == "unit-1"
        return _FAKE_PASS

    monkeypatch.setattr(
        "apps.user_service.app.services.passes_service.PassesService.create_pass",
        fake_create_pass,
    )

    res = await client.post("/v1/passes", json=_CREATE_PAYLOAD)
    body = assert_success(res, 201)
    assert body["data"]["id"] == PASS_ID
    assert body["data"]["code"] == "4821"


@pytest.mark.asyncio
async def test_get_pass(monkeypatch, client):
    """GET passes/{pass_id} returns pass details including timeline."""

    _patch_contact_context(monkeypatch)

    async def fake_get_pass(_self, *, contact_id: str, pass_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert pass_id == PASS_ID
        return {**_FAKE_PASS, "events": [{"id": "evt-1", "event_type": "created"}]}

    monkeypatch.setattr(
        "apps.user_service.app.services.passes_service.PassesService.get_pass",
        fake_get_pass,
    )

    res = await client.get(f"/v1/passes/{PASS_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == PASS_ID
    assert body["data"]["events"][0]["event_type"] == "created"


@pytest.mark.asyncio
async def test_cancel_pass(monkeypatch, client):
    """POST passes/{pass_id}/cancel cancels an upcoming or active pass."""

    _patch_contact_context(monkeypatch)

    async def fake_cancel_pass(_self, *, contact_id: str, pass_id: str):
        del _self
        assert contact_id == CONTACT_ID
        assert pass_id == PASS_ID
        return {**_FAKE_PASS, "status": "cancelled", "display_status": "expired"}

    monkeypatch.setattr(
        "apps.user_service.app.services.passes_service.PassesService.cancel_pass",
        fake_cancel_pass,
    )

    res = await client.post(f"/v1/passes/{PASS_ID}/cancel")
    body = assert_success(res, 200)
    assert body["data"]["status"] == "cancelled"
