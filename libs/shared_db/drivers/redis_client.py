"""Async Redis client helpers for session-context caching."""

from __future__ import annotations

import logging

import redis.asyncio as redis

from libs.shared_config.app_settings import shared_settings

logger = logging.getLogger(__name__)

_client_holder: dict[str, redis.Redis | None] = {"client": None}


async def init_redis() -> None:
    """Initialize the Redis client."""
    if _client_holder["client"] is not None:
        return
    client = await redis.from_url(
        shared_settings.redis.url, encoding="utf-8", decode_responses=True, max_connections=100
    )
    await client.ping()

    _client_holder["client"] = client
    logger.info("Redis connected for session context cache")


async def get_redis() -> redis.Redis | None:
    """Return a shared async Redis client, or None when disabled/unavailable."""
    settings = shared_settings.redis
    if not settings.enabled:
        return None

    client = _client_holder["client"]
    if client is not None:
        return client
    try:
        await init_redis()
        return _client_holder["client"]
    except Exception as exc:
        logger.warning("Redis unavailable, continuing without cache: %s", exc)
        return None


async def close_redis() -> None:
    """Close and reset the shared Redis client."""
    client = _client_holder["client"]
    if client is not None:
        await client.aclose()
        _client_holder["client"] = None
