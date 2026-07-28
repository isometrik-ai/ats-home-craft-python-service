"""Integration tests for user push token register/unregister routes."""

import pytest

from apps.user_service.tests.utils.assertions import assert_success


class _FakePushService:
    """Minimal push token service stub for API tests."""

    async def register_device(self, *, body):
        """Return a fake registered device summary."""
        return {
            "device_id": body.device_id,
            "platform": body.platform.value if hasattr(body.platform, "value") else body.platform,
            "registered_at": "2026-06-01T12:00:00+00:00",
        }

    async def unregister_device(self, *, device_id: str):
        """Return a fake unregistered device summary."""
        return {"device_id": device_id}


@pytest.mark.asyncio
async def test_register_push_device_api(monkeypatch, client):
    """POST /v1/users/me/push-devices returns 201."""
    fake_service = _FakePushService()

    async def fake_for_end_user(*, db_connection, current_user, request=None):
        del db_connection, current_user, request
        return fake_service

    monkeypatch.setattr(
        "apps.user_service.app.api.user_push_tokens.UserPushTokenService.for_end_user",
        fake_for_end_user,
    )

    res = await client.post(
        "/v1/users/me/push-devices",
        json={
            "device_id": "device-xyz",
            "push_token": "token-abc",
            "platform": "ios",
            "app_version": "2.0.0",
        },
    )

    body = assert_success(res, 201)
    assert body["data"]["device_id"] == "device-xyz"


@pytest.mark.asyncio
async def test_unregister_push_device_api(monkeypatch, client):
    """DELETE /v1/users/me/push-devices/{device_id} returns 200."""
    fake_service = _FakePushService()

    async def fake_for_end_user(*, db_connection, current_user, request=None):
        del db_connection, current_user, request
        return fake_service

    monkeypatch.setattr(
        "apps.user_service.app.api.user_push_tokens.UserPushTokenService.for_end_user",
        fake_for_end_user,
    )

    res = await client.delete("/v1/users/me/push-devices/device-xyz")

    body = assert_success(res, 200)
    assert body["data"]["device_id"] == "device-xyz"
