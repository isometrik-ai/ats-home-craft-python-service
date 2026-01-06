"""Integration tests for verification codes endpoints."""

import pytest

from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_send_verification_code(monkeypatch, client):
    """Send verification code."""

    async def fake_get_optional_user(request):
        del request
        return None

    async def fake_get_user_from_auth(request):
        del request
        return None

    async def fake_send(self, request, data, current_user):
        del self, request, data, current_user
        return {
            "verification_id": "ver-1",
            "expiryAt": 1700000000000,
            "attemptsLeft": 3,
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.verification_codes.get_optional_user",
        fake_get_optional_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service."
        "VerificationCodeService.send_verification_code",
        fake_send,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        fake_get_user_from_auth,
    )

    res = await client.post(
        "/v1/verification-code/send",
        json={
            "type": "EMAIL",
            "email": "user@example.com",
            "verification_method": "signup_verification",
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["verification_id"] == "ver-1"


@pytest.mark.asyncio
async def test_verify_verification_code(monkeypatch, client):
    """Verify verification code."""

    async def fake_get_optional_user(request):
        del request
        return None

    async def fake_get_user_from_auth(request):
        del request
        return None

    async def fake_verify(self, request, data, current_user):
        del self, request, data, current_user
        return {"verified": True}

    monkeypatch.setattr(
        "apps.user_service.app.api.verification_codes.get_optional_user",
        fake_get_optional_user,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.verification_code_service."
        "VerificationCodeService.verify_verification_code",
        fake_verify,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        fake_get_user_from_auth,
    )

    res = await client.post(
        "/v1/verification-code/verify",
        json={
            "type": "EMAIL",
            "verification_id": "ver-1",
            "verification_code": "123456",
            "email": "user@example.com",
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["verified"] is True
