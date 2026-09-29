"""Unit tests for the FastAPI Redis dependency provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from apps.user_service.app.dependencies.redis import redis_client

_MODULE = "apps.user_service.app.dependencies.redis"


@pytest.mark.asyncio
@patch(f"{_MODULE}.get_redis", new_callable=AsyncMock)
async def test_redis_client_returns_shared_client(mock_get_redis: AsyncMock) -> None:
    """Dependency forwards to get_redis."""
    sentinel = object()
    mock_get_redis.return_value = sentinel

    result = await redis_client()

    assert result is sentinel
    mock_get_redis.assert_awaited_once_with()


@pytest.mark.asyncio
@patch(f"{_MODULE}.get_redis", new_callable=AsyncMock)
async def test_redis_client_returns_none_when_disabled(mock_get_redis: AsyncMock) -> None:
    """Dependency propagates None when Redis is unavailable."""
    mock_get_redis.return_value = None

    assert await redis_client() is None
