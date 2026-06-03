"""Integration tests for auth endpoints."""

import datetime as dt

import pytest

from apps.user_service.app.schemas.auth import (
    RefreshSessionResponse,
    SelectOrganizationResponse,
)
from apps.user_service.app.schemas.enums import SelectOrganizationType
from apps.user_service.tests.factories import user_payload
from apps.user_service.tests.utils.assertions import assert_success


@pytest.mark.asyncio
async def test_login_returns_tokens(monkeypatch, client):
    """Test that the login endpoint returns tokens."""
    fake_result = {
        "access_token": "atk",
        "refresh_token": "rtk",
        "expires_in": 3600,
        "expires_at": dt.datetime(2024, 1, 1, 0, 0, 0),
        "user": {
            "id": "user-1",
            "email": "user@example.com",
            "first_name": "Test",
            "last_name": "User",
            "timezone": "UTC",
            "org_setup_status_completed": True,
            "organization_id": "org-123",
        },
    }

    async def fake_login(_self, data):
        del _self, data
        return fake_result

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.login",
        fake_login,
    )

    payload = {"email": "user@example.com", "password": "StrongPass123!"}
    res = await client.post("/v1/auth/login", json=payload)

    body = assert_success(res, 200)
    assert body["data"]["access_token"] == "atk"
    assert body["data"]["user"]["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_refresh_returns_new_tokens(monkeypatch, client):
    """Test that the refresh endpoint returns new tokens."""
    fake_result = {
        "access_token": "new-atk",
        "refresh_token": "new-rtk",
        "expires_in": 3600,
        "expires_at": dt.datetime(2024, 1, 1, 1, 0, 0),
        "token_refreshed": True,
    }

    async def fake_refresh(_self, access_token: str, refresh_token: str):
        del _self
        assert access_token == "old-atk"
        assert refresh_token == "old-rtk"
        return RefreshSessionResponse(**fake_result)

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.refresh_session",
        fake_refresh,
    )

    res = await client.put(
        "/v1/auth/refresh",
        headers={"Access-Token": "old-atk", "Refresh-Token": "old-rtk"},
    )

    body = assert_success(res, 200)
    assert body["data"]["token_refreshed"] is True
    assert body["data"]["access_token"] == "new-atk"


@pytest.mark.asyncio
async def test_refresh_token_not_expired(monkeypatch, client):
    """Test that the refresh endpoint returns new tokens if the refresh token is not expired."""
    fake_result = {
        "access_token": None,
        "refresh_token": None,
        "expires_in": None,
        "expires_at": None,
        "token_refreshed": False,
    }

    async def fake_refresh(_self, access_token: str, refresh_token: str):
        del _self
        assert access_token == "still-valid"
        assert refresh_token == "rtk"
        return RefreshSessionResponse(**fake_result)

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.refresh_session",
        fake_refresh,
    )

    res = await client.put(
        "/v1/auth/refresh",
        headers={"Access-Token": "still-valid", "Refresh-Token": "rtk"},
    )

    body = assert_success(res, 200)
    assert body["data"]["token_refreshed"] is False
    assert "access_token" not in body["data"]


@pytest.mark.asyncio
async def test_set_password_for_authenticated_user(monkeypatch, client):
    """Set-password should return a fresh AuthResponse (auto-login)."""

    fake_result = {
        "auth": {
            "access_token": "new-atk",
            "refresh_token": "new-rtk",
            "expires_in": 3600,
            "expires_at": dt.datetime(2024, 1, 1, 0, 0, 0),
            "user": {
                "id": "user-1",
                "email": "user@example.com",
                "first_name": "Test",
                "last_name": "User",
                "timezone": "UTC",
                "org_setup_status_completed": True,
                "organization_id": "org-123",
            },
            "organizations": [],
        },
        "select_organization": {"isometrik_details": None},
    }

    async def fake_set_password(
        _self,
        *,
        user_id: str,
        current_session_id: str | None,
        password: str,
        admin_client,
        anon_client,
    ):
        del _self, admin_client, anon_client
        assert user_id == "test-user-id"
        assert password == "NewPass123!"
        assert current_session_id is None or isinstance(current_session_id, str)
        return fake_result

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.set_password",
        fake_set_password,
    )

    res = await client.post(
        "/v1/auth/set-password",
        json={"password": "NewPass123!"},
    )

    body = assert_success(res, 200)
    assert body["data"]["auth"]["access_token"] == "new-atk"
    assert body["data"]["auth"]["refresh_token"] == "new-rtk"
    assert body["data"]["auth"]["user"]["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_forgot_password(monkeypatch, client):
    """Test that the forgot password endpoint sends a forgot password email."""

    async def fake_forgot(_self, email: str):
        del _self
        assert email == "user@example.com"
        return {"message": "sent"}

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.forgot_password",
        fake_forgot,
    )

    res = await client.post("/v1/auth/forgot-password", json={"email": "user@example.com"})
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_change_password(monkeypatch, client):
    """Test that the change password endpoint changes the password."""

    async def fake_change(
        _self,
        *,
        user_id=None,
        user_email=None,
        current_password=None,
        new_password=None,
        user_metadata=None,
    ):
        del _self, user_metadata
        assert user_id == "test-user-id"
        assert user_email == "test@example.com"
        assert current_password == "OldPass123!"
        assert new_password == "NewPass123!"
        return {"message": "changed"}

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.change_password",
        fake_change,
    )

    res = await client.post(
        "/v1/auth/change-password",
        json={"current_password": "OldPass123!", "new_password": "NewPass123!"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_signup(monkeypatch, client):
    """Test that the signup endpoint signs up a user."""
    fake_result = {
        "access_token": "atk",
        "refresh_token": "rtk",
        "expires_in": 3600,
        "expires_at": dt.datetime(2024, 1, 1, 0, 0, 0),
        "user": {
            "id": "user-1",
            "email": "user@example.com",
            "first_name": "Test",
            "last_name": "User",
            "timezone": "UTC",
            "org_setup_status_completed": True,
            "organization_id": "org-123",
        },
    }

    async def fake_signup(_self, _signup_data):
        del _self, _signup_data
        return fake_result

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.signup",
        fake_signup,
    )

    payload = user_payload(email="user@example.com")
    res = await client.post("/v1/auth/signup", json=payload)
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_validate_account(monkeypatch, client):
    """Test that the validate account endpoint validates credentials and 2FA status."""

    async def fake_validate(_self, trigger: str, email: str, password: str | None):
        del _self
        assert trigger == "LOGIN"
        assert email == "user@example.com"
        assert password == "StrongPass123!"
        return {"two_fa_enabled": True}

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.validate_account",
        fake_validate,
    )

    res = await client.post(
        "/v1/auth/validate/account",
        json={"trigger": "LOGIN", "email": "user@example.com", "password": "StrongPass123!"},
    )
    body = assert_success(res, 200)
    assert body["data"]["two_fa_enabled"] is True


@pytest.mark.asyncio
async def test_switch_organization(monkeypatch, client):
    """Switch-org should succeed and return switch-org payload shape."""

    async def fake_switch(
        _self,
        *,
        user_id: str,
        session_id: str,
        organization_id: str,
        user_type: SelectOrganizationType,
    ):
        del _self
        assert user_id == "test-user-id"
        assert session_id == "test-session-id"
        assert organization_id == "org-456"
        assert user_type == SelectOrganizationType.ORGANIZATION_MEMBER
        return SelectOrganizationResponse(isometrik_details=None)

    monkeypatch.setattr(
        "apps.user_service.app.services.auth_service.AuthService.switch_organization",
        fake_switch,
    )

    res = await client.post(
        "/v1/auth/switch-org",
        json={"organization_id": "org-456", "user_type": "organization_member"},
    )
    body = assert_success(res, 200)
    assert body["data"] == {}


@pytest.mark.asyncio
async def test_validate_token_success(client):
    """Test that the validate token endpoint returns organization_id when token is valid."""
    res = await client.get("/v1/auth/validate")
    body = assert_success(res, 200)
    assert body["data"]["organization_id"] == "org-123"
    assert body["code"]  # ensure custom code present
