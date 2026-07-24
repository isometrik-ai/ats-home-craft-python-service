"""Integration tests for gate pass verification endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

PASS_ID = "pass-1"

_FAKE_VERIFY_RESULT = {
    "pass_id": PASS_ID,
    "code": "4821",
    "guest_name": "Guest User",
    "visitor_count": 1,
    "pass_type": "guest",
    "access_status": "approved",
    "can_check_in": True,
}

_FAKE_CHECK_IN_RESULT = {
    "pass_id": PASS_ID,
    "event_type": "check_in",
    "gate_id": "gate-1",
}


@pytest.mark.asyncio
async def test_verify_pass(monkeypatch, client):
    """POST /passes/verify looks up a pass by code."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.gate_passes")

    async def fake_verify(_self, *, code: str, gate_id=None):
        del _self, gate_id
        assert code == "4821"
        return _FAKE_VERIFY_RESULT

    monkeypatch.setattr(
        "apps.user_service.app.services.pass_verification_service.PassVerificationService.verify",
        fake_verify,
    )

    res = await client.post(
        "/v1/passes/verify",
        json={"code": "4821", "gate_id": "gate-1"},
    )
    body = assert_success(res, 200)
    assert body["data"]["pass_id"] == PASS_ID
    assert body["data"]["can_check_in"] is True


@pytest.mark.asyncio
async def test_check_in_pass(monkeypatch, client):
    """POST /passes/{pass_id}/check-in records entry."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.gate_passes")

    async def fake_check_in(_self, *, pass_id: str, body):
        del _self
        assert pass_id == PASS_ID
        assert body.gate_id == "gate-1"
        assert body.entry_method.value == "code"
        return _FAKE_CHECK_IN_RESULT

    monkeypatch.setattr(
        "apps.user_service.app.services.pass_verification_service.PassVerificationService.check_in",
        fake_check_in,
    )

    res = await client.post(
        f"/v1/passes/{PASS_ID}/check-in",
        json={
            "gate_id": "gate-1",
            "entry_method": "code",
            "access_status": "approved",
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["event_type"] == "check_in"


@pytest.mark.asyncio
async def test_check_out_pass(monkeypatch, client):
    """POST /passes/{pass_id}/check-out records exit."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.gate_passes")

    async def fake_check_out(_self, *, pass_id: str, body):
        del _self
        assert pass_id == PASS_ID
        assert body.gate_id == "gate-1"
        return {"pass_id": PASS_ID, "event_type": "check_out"}

    monkeypatch.setattr(
        "apps.user_service.app.services.pass_verification_service."
        "PassVerificationService.check_out",
        fake_check_out,
    )

    res = await client.post(
        f"/v1/passes/{PASS_ID}/check-out",
        json={"gate_id": "gate-1"},
    )
    body = assert_success(res, 200)
    assert body["data"]["event_type"] == "check_out"
