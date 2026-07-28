"""Unit tests for CacheRequestBodyMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from apps.user_service.app.dependencies.exception_middleware import (
    CacheRequestBodyMiddleware,
)


def _request(*, method: str = "POST", cached_body=..., body: bytes = b'{"ok": true}'):
    """Build a minimal FastAPI/Starlette request mock."""
    request = MagicMock()
    request.method = method
    request.url = "http://test.example/api/items"
    state = type("State", (), {})()
    if cached_body is not ...:
        state.cached_body = cached_body
    request.state = state
    request.body = AsyncMock(return_value=body)
    return request


@pytest.mark.asyncio
async def test_options_passthrough_without_caching():
    """OPTIONS requests skip body caching."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="OPTIONS")
    call_next = AsyncMock(return_value=Response(status_code=204))

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 204
    call_next.assert_awaited_once_with(request)
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_caches_request_body():
    """POST bodies are stored on request.state for downstream middleware."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="POST", body=b'{"name":"test"}')
    call_next = AsyncMock(return_value=Response(status_code=200))

    await middleware.dispatch(request, call_next)

    assert request.state.cached_body == b'{"name":"test"}'
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_sets_empty_cached_body():
    """Non-body HTTP methods cache an empty body marker."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="GET")
    call_next = AsyncMock(return_value=Response(status_code=200))

    await middleware.dispatch(request, call_next)

    assert request.state.cached_body == b""
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_read_when_body_already_cached():
    """Existing cached_body is left untouched."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="POST", cached_body=b"already")
    call_next = AsyncMock(return_value=Response(status_code=200))

    await middleware.dispatch(request, call_next)

    assert request.state.cached_body == b"already"
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_disconnect_returns_499():
    """Client disconnect while reading body short-circuits with 499."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="POST")
    request.body = AsyncMock(side_effect=ClientDisconnect())
    call_next = AsyncMock()

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 499
    call_next.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [OSError("broken pipe"), ValueError("invalid body")])
async def test_body_read_errors_cache_empty_and_continue(exc):
    """Body read failures cache empty bytes and still invoke the handler."""
    middleware = CacheRequestBodyMiddleware(app=MagicMock())
    request = _request(method="PUT")
    request.body = AsyncMock(side_effect=exc)
    call_next = AsyncMock(return_value=Response(status_code=200))

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert request.state.cached_body == b""
    call_next.assert_awaited_once()
