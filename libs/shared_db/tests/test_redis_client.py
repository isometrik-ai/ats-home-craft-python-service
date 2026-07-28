"""Unit tests for Redis client helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_db.drivers import redis_client


@pytest.fixture(autouse=True)
def reset_redis_holder():
    """Clear shared client between tests."""
    redis_client._client_holder["client"] = None
    yield
    redis_client._client_holder["client"] = None


@pytest.mark.asyncio
async def test_init_redis_connects_and_pings():
    """init_redis creates client and stores it."""
    mock_client = AsyncMock()
    mock_client.ping = AsyncMock()
    with (
        patch("redis.asyncio.from_url", AsyncMock(return_value=mock_client)),
        patch.object(redis_client.shared_settings.redis, "url", "redis://localhost:6379/0"),
        patch.object(redis_client.shared_settings.redis, "max_connections", 5),
    ):
        await redis_client.init_redis()

    assert redis_client._client_holder["client"] is mock_client
    mock_client.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_redis_skips_when_already_initialized():
    """Second init is a no-op when client exists."""
    existing = MagicMock()
    redis_client._client_holder["client"] = existing
    with patch("redis.asyncio.from_url", AsyncMock()) as from_url:
        await redis_client.init_redis()
    from_url.assert_not_called()


@pytest.mark.asyncio
async def test_get_redis_returns_none_when_disabled():
    """Disabled redis settings yield None without connecting."""
    with patch.object(redis_client.shared_settings.redis, "enabled", False):
        result = await redis_client.get_redis()
    assert result is None


@pytest.mark.asyncio
async def test_get_redis_returns_existing_client():
    """get_redis returns cached client when present."""
    existing = MagicMock()
    redis_client._client_holder["client"] = existing
    with patch.object(redis_client.shared_settings.redis, "enabled", True):
        result = await redis_client.get_redis()
    assert result is existing


@pytest.mark.asyncio
async def test_get_redis_init_failure_returns_none():
    """Connection failures are swallowed and return None."""
    with (
        patch.object(redis_client.shared_settings.redis, "enabled", True),
        patch("redis.asyncio.from_url", AsyncMock(side_effect=ConnectionError("down"))),
    ):
        result = await redis_client.get_redis()
    assert result is None


@pytest.mark.asyncio
async def test_close_redis_closes_and_clears_client():
    """close_redis shuts down active client."""
    mock_client = AsyncMock()
    redis_client._client_holder["client"] = mock_client

    await redis_client.close_redis()

    mock_client.aclose.assert_awaited_once()
    assert redis_client._client_holder["client"] is None
