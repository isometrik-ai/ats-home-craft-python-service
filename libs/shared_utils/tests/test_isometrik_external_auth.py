"""Unit tests for Isometrik external auth dependency."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from libs.shared_middleware import isometrik_external_auth as auth
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ServiceUnavailableException,
    UnauthorizedException,
)


@pytest.fixture(autouse=True)
def _reset_client_state():
    """Reset cached HTTP client between tests."""
    auth._state.client = None
    yield
    auth._state.client = None


@pytest.mark.asyncio
async def test_get_isometrik_http_client_creates_singleton() -> None:
    """HTTP client is created once and reused."""
    client_one = await auth.get_isometrik_http_client()
    client_two = await auth.get_isometrik_http_client()
    assert client_one is client_two
    await auth.close_isometrik_http_client()


@pytest.mark.asyncio
async def test_close_isometrik_http_client_noop_when_missing() -> None:
    """Closing without a client is safe."""
    await auth.close_isometrik_http_client()


@pytest.mark.asyncio
async def test_isometrik_auth_success() -> None:
    """Valid decode response returns external context."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"projectId": " project-1 "}
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)

    with patch.object(auth, "get_isometrik_http_client", AsyncMock(return_value=mock_client)):
        ctx = await auth.isometrik_auth_without_token_middleware(
            license_key="lk",
            app_secret="secret",
        )

    assert ctx.project_id == "project-1"
    assert ctx.raw["projectId"] == " project-1 "


@pytest.mark.asyncio
async def test_isometrik_auth_missing_headers() -> None:
    """Blank credentials raise BadRequestException."""
    with pytest.raises(BadRequestException):
        await auth.isometrik_auth_without_token_middleware(license_key="", app_secret="")


@pytest.mark.asyncio
async def test_isometrik_auth_unauthorized() -> None:
    """4xx decode responses raise UnauthorizedException."""
    request = httpx.Request("GET", "https://example.com/decode")
    response = httpx.Response(401, request=request)
    mock_client = MagicMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError("unauthorized", request=request, response=response)
    )

    with patch.object(auth, "get_isometrik_http_client", AsyncMock(return_value=mock_client)):
        with pytest.raises(UnauthorizedException):
            await auth.isometrik_auth_without_token_middleware(
                license_key="lk",
                app_secret="secret",
            )


@pytest.mark.asyncio
async def test_isometrik_auth_upstream_error() -> None:
    """5xx decode responses raise ServiceUnavailableException."""
    request = httpx.Request("GET", "https://example.com/decode")
    response = httpx.Response(503, request=request)
    mock_client = MagicMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError("upstream", request=request, response=response)
    )

    with patch.object(auth, "get_isometrik_http_client", AsyncMock(return_value=mock_client)):
        with pytest.raises(ServiceUnavailableException):
            await auth.isometrik_auth_without_token_middleware(
                license_key="lk",
                app_secret="secret",
            )


@pytest.mark.asyncio
async def test_isometrik_auth_missing_project_id() -> None:
    """Decode payload without project id raises BadRequestException."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"projectId": "   "}
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)

    with patch.object(auth, "get_isometrik_http_client", AsyncMock(return_value=mock_client)):
        with pytest.raises(BadRequestException):
            await auth.isometrik_auth_without_token_middleware(
                license_key="lk",
                app_secret="secret",
            )
