"""Tests for Isometrik service helpers."""

import time
from unittest.mock import MagicMock

import httpx
import jwt
import pytest

from libs.shared_utils import isometrik_service
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    RateLimitExceededException,
    ServiceUnavailableException,
)
from libs.shared_utils.isometrik_service import (
    ISOMETRIK_AUDIENCE,
    create_isometrik_ai_agent,
    create_isometrik_application,
    create_isometrik_token,
    create_isometrik_user,
    get_isometrik_data_from_settings,
    login_to_isometrik,
    update_isometrik_user,
)


class _DummyIsometrikSettings:
    """Minimal settings object for Isometrik tests."""

    api_url = "https://isometrik.example.com"
    admin_api_url = "https://admin-apis.isometrik.io"
    client_name = "isometrik-client"
    private_key = "test-private-key"
    token_exp_minutes = 540
    is_enabled = True


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


class _DummyContextAsyncClient:
    """Async context-manager client stub for one-off httpx calls."""

    def __init__(self, calls: dict) -> None:
        self._calls = calls
        self._response = _DummyResponse({"userId": "isometrik-123"})

    async def __aenter__(self) -> "_DummyContextAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, json: dict, headers: dict) -> _DummyResponse:
        """Record POST parameters and return a stub response."""
        self._calls["url"] = url
        self._calls["json"] = json
        self._calls["headers"] = headers
        return self._response


class _DummyAdminHttpClient:
    """Async client stub for cached Isometrik admin HTTP client."""

    def __init__(self, calls: dict) -> None:
        self._calls = calls
        self._response = _DummyResponse({"agentId": "agent-1"})

    async def post(self, url: str, json: dict, headers: dict) -> _DummyResponse:
        """Record POST parameters and return a stub response."""
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
        lambda *args, **kwargs: _DummyContextAsyncClient(calls),
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
        lambda *args, **kwargs: _DummyContextAsyncClient(calls),
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


@pytest.mark.asyncio
async def test_create_isometrik_ai_agent_posts_to_admin_api(monkeypatch) -> None:
    """create_isometrik_ai_agent posts payload with org credentials."""
    calls: dict = {}

    async def _fake_admin_client() -> _DummyAdminHttpClient:
        return _DummyAdminHttpClient(calls)

    monkeypatch.setattr(
        isometrik_service,
        "get_strands_http_client",
        _fake_admin_client,
    )

    result = await create_isometrik_ai_agent(
        payload={"project_id": "project-1", "name": "Pulse Agent"},
        app_secret="app-secret",
        license_key="license-key",
    )

    assert result == {"agentId": "agent-1"}
    assert calls["url"] == "/v1/ai-agent"
    assert calls["headers"]["appsecret"] == "app-secret"
    assert calls["headers"]["licensekey"] == "license-key"
    assert calls["json"]["project_id"] == "project-1"


def test_get_isometrik_data_from_settings_nested():
    """get_isometrik_data_from_settings prefers application_details."""
    settings = {
        "isometrik_application_details": {"licenseKey": "lk-1"},
        "isometrik": {"licenseKey": "lk-legacy"},
    }
    assert get_isometrik_data_from_settings(settings) == {"licenseKey": "lk-1"}


def test_get_isometrik_data_from_settings_legacy_key():
    """get_isometrik_data_from_settings falls back to isometrik key."""
    settings = {"isometrik": {"appSecret": "secret"}}
    assert get_isometrik_data_from_settings(settings) == {"appSecret": "secret"}


def test_get_isometrik_data_from_settings_none():
    """Missing settings return None."""
    assert get_isometrik_data_from_settings(None) is None


@pytest.mark.asyncio
async def test_create_isometrik_application(monkeypatch) -> None:
    """create_isometrik_application posts org payload to admin API."""
    calls: dict = {}

    class _Settings(_DummyIsometrikSettings):
        auth_token = "admin-basic-token"
        region_id = "us-east-1"

    class _Shared(_DummySharedSettings):
        isometrik = _Settings()

    monkeypatch.setattr(isometrik_service, "shared_settings", _Shared())
    monkeypatch.setattr(
        isometrik_service.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _DummyContextAsyncClient(calls),
    )

    result = await create_isometrik_application("Org One", product_types=["chat"])

    assert result == {"userId": "isometrik-123"}
    assert calls["json"]["name"] == "Org One"
    assert calls["json"]["productType"] == ["chat"]


@pytest.mark.asyncio
async def test_login_to_isometrik(monkeypatch) -> None:
    """login_to_isometrik authenticates with derived password."""
    calls: dict = {}

    monkeypatch.setattr(isometrik_service, "shared_settings", _DummySharedSettings())
    monkeypatch.setattr(
        isometrik_service.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _DummyContextAsyncClient(calls),
    )

    creds = {"userSecret": "us", "licenseKey": "lk", "appSecret": "as"}
    result = await login_to_isometrik("user-1", creds)

    assert result == {"userId": "isometrik-123"}
    assert calls["url"].endswith("/chat/user/authenticate")
    assert calls["json"]["userIdentifier"] == "user-1"


class _DummyPatchAsyncClient:
    """Async context client stub for PATCH requests."""

    def __init__(self, calls: dict) -> None:
        self._calls = calls
        self._response = _DummyResponse({"updated": True})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def patch(self, url: str, json: dict, headers: dict) -> _DummyResponse:
        self._calls["url"] = url
        self._calls["json"] = json
        self._calls["headers"] = headers
        return self._response


@pytest.mark.asyncio
async def test_update_isometrik_user(monkeypatch) -> None:
    """update_isometrik_user PATCHes profile fields."""
    calls: dict = {}

    monkeypatch.setattr(isometrik_service, "shared_settings", _DummySharedSettings())
    monkeypatch.setattr(
        isometrik_service.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _DummyPatchAsyncClient(calls),
    )

    creds = {"userToken": "tok", "licenseKey": "lk", "appSecret": "as"}
    result = await update_isometrik_user(
        creds,
        user_name="Jane Doe",
        user_profile_image_url="https://example.com/a.jpg",
    )

    assert result == {"updated": True}
    assert calls["json"]["userName"] == "Jane Doe"
    assert calls["headers"]["userToken"] == "tok"


def test_create_isometrik_token_missing_config(monkeypatch) -> None:
    """create_isometrik_token raises when settings are incomplete."""
    bad_settings = _DummySharedSettings()
    bad_settings.isometrik.private_key = ""
    bad_settings.isometrik.client_name = ""
    monkeypatch.setattr(isometrik_service, "shared_settings", bad_settings)

    with pytest.raises(InternalServerErrorException):
        create_isometrik_token()


def _http_status_error(status_code: int, *, message: str = "error") -> httpx.HTTPStatusError:
    """Build httpx.HTTPStatusError with JSON body."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value={"message": message})
    return httpx.HTTPStatusError("failed", request=MagicMock(), response=response)


def test_handle_isometrik_error_json_parse_failure() -> None:
    """Invalid JSON responses map to ServiceUnavailableException."""
    response = MagicMock()
    response.text = "not-json"
    with pytest.raises(ServiceUnavailableException):
        isometrik_service._handle_isometrik_error(
            ValueError("bad json"),
            "test op",
            response,
        )


def test_handle_isometrik_error_conflict() -> None:
    """409 responses map to ConflictException."""
    with pytest.raises(ConflictException):
        isometrik_service._handle_isometrik_error(
            _http_status_error(409, message="already exists"),
            "test op",
        )


def test_handle_isometrik_error_rate_limit() -> None:
    """429 responses map to RateLimitExceededException."""
    with pytest.raises(RateLimitExceededException):
        isometrik_service._handle_isometrik_error(
            _http_status_error(429),
            "test op",
        )


def test_handle_isometrik_error_bad_request() -> None:
    """4xx responses map to BadRequestException."""
    with pytest.raises(BadRequestException):
        isometrik_service._handle_isometrik_error(
            _http_status_error(400, message="invalid payload"),
            "test op",
        )


def test_handle_isometrik_error_server_error() -> None:
    """5xx responses map to ServiceUnavailableException."""
    with pytest.raises(ServiceUnavailableException):
        isometrik_service._handle_isometrik_error(
            _http_status_error(503),
            "test op",
        )


def test_handle_isometrik_error_connection_error() -> None:
    """Connection errors map to ServiceUnavailableException."""
    with pytest.raises(ServiceUnavailableException):
        isometrik_service._handle_isometrik_error(
            httpx.ConnectError("timeout"),
            "test op",
        )


def test_handle_isometrik_error_unexpected() -> None:
    """Unexpected errors map to InternalServerErrorException."""
    with pytest.raises(InternalServerErrorException):
        isometrik_service._handle_isometrik_error(RuntimeError("boom"), "test op")


@pytest.mark.asyncio
async def test_create_isometrik_ai_agent_invalid_body(monkeypatch) -> None:
    """create_isometrik_ai_agent rejects non-object JSON bodies."""

    class _BadClient:
        async def post(self, url: str, json: dict, headers: dict):
            del url, json, headers
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.json = MagicMock(return_value=[])
            return response

    async def _fake_admin_client():
        return _BadClient()

    monkeypatch.setattr(isometrik_service, "get_strands_http_client", _fake_admin_client)

    with pytest.raises(ServiceUnavailableException):
        await create_isometrik_ai_agent(
            payload={"name": "Agent"},
            app_secret="secret",
            license_key="license",
        )
