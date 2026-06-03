"""Tests for Isometrik service helpers."""

import time

import jwt
import pytest

from libs.shared_utils import isometrik_service
from libs.shared_utils.isometrik_service import (
    ISOMETRIK_AUDIENCE,
    create_isometrik_token,
    create_isometrik_user,
)


class _DummyIsometrikSettings:
    """Minimal settings object for Isometrik tests."""

    api_url = "https://isometrik.example.com"
    client_name = "isometrik-client"
    private_key = "test-private-key"
    token_exp_minutes = 540


class _DummySharedSettings:
    """Wrapper that mimics shared_settings.isometrik structure."""

    isometrik = _DummyIsometrikSettings()


class _DummyResponse:
    """Simple stand-in for httpx.Response."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        """Raise for status."""
        return None

    def json(self) -> dict:
        """Return JSON data."""
        return self._data


class _DummyAsyncClient:
    """Async client stub that records request parameters."""

    def __init__(self, calls: dict) -> None:
        self._calls = calls
        self._response = _DummyResponse({"userId": "isometrik-123"})

    async def __aenter__(self) -> "_DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """Exit context manager."""
        return None

    async def post(self, url: str, json: dict, headers: dict) -> _DummyResponse:
        """Post request."""
        self._calls["url"] = url
        self._calls["json"] = json
        self._calls["headers"] = headers
        return self._response


@pytest.mark.asyncio
async def test_create_isometrik_user_default_avatar(monkeypatch) -> None:
    """create_isometrik_user uses default avatar when missing."""
    calls: dict = {}

    monkeypatch.setattr(isometrik_service, "shared_settings", _DummySharedSettings())
    monkeypatch.setattr(
        isometrik_service.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _DummyAsyncClient(calls),
    )

    user = {
        "user_id": "user-1",
        "email": "user@example.com",
        "organization_id": "org-1",
        "role": "member",
        "first_name": "John",
        "last_name": "Doe",
    }
    credentials = {
        "userSecret": "user-secret",
        "licenseKey": "license-key",
        "appSecret": "app-secret",
    }

    result = await create_isometrik_user(user=user, isometrik_credentials=credentials)

    assert result == {"userId": "isometrik-123"}
    assert calls["json"]["userName"] == "John Doe"
    assert calls["json"]["userIdentifier"] == "user-1"
    assert calls["json"]["userProfileImageUrl"] == "https://example.com/default-avatar.jpg"


@pytest.mark.asyncio
async def test_create_isometrik_user_custom_avatar(monkeypatch) -> None:
    """create_isometrik_user uses avatar from user dict."""
    calls: dict = {}

    monkeypatch.setattr(isometrik_service, "shared_settings", _DummySharedSettings())
    monkeypatch.setattr(
        isometrik_service.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _DummyAsyncClient(calls),
    )

    user = {
        "user_id": "user-2",
        "email": "user2@example.com",
        "organization_id": "org-2",
        "role": "member",
        "first_name": "Jane",
        "last_name": "Smith",
        "avatar_url": "https://example.com/custom-avatar.jpg",
    }
    credentials = {
        "userSecret": "user-secret",
        "licenseKey": "license-key",
        "appSecret": "app-secret",
    }

    result = await create_isometrik_user(user=user, isometrik_credentials=credentials)

    assert result == {"userId": "isometrik-123"}
    assert calls["json"]["userName"] == "Jane Smith"
    assert calls["json"]["userIdentifier"] == "user-2"
    assert calls["json"]["userProfileImageUrl"] == "https://example.com/custom-avatar.jpg"


def test_create_isometrik_token(monkeypatch) -> None:
    """create_isometrik_token builds an HS512 JWT with expected claims."""
    monkeypatch.setattr(isometrik_service, "shared_settings", _DummySharedSettings())

    token = create_isometrik_token()

    claims = jwt.decode(
        token,
        "test-private-key",
        algorithms=["HS512"],
        audience=ISOMETRIK_AUDIENCE,
        issuer=ISOMETRIK_AUDIENCE,
        options={"verify_exp": False},
    )
    assert claims["sub"] == "isometrik-client"
    assert claims["typ"] == "access"
    assert claims["aud"] == ISOMETRIK_AUDIENCE
    assert claims["iss"] == ISOMETRIK_AUDIENCE
    assert claims["iat"] <= int(time.time())
    assert claims["nbf"] == claims["iat"] - 1
    assert claims["exp"] == claims["iat"] + 540 * 60
    assert isinstance(claims["jti"], str)
