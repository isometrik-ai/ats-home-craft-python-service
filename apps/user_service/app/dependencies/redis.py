"""Redis dependency provider for FastAPI."""

from __future__ import annotations

import redis.asyncio as redis

from libs.shared_db.drivers.redis_client import get_redis


async def redis_client() -> redis.Redis | None:
    """Return the shared async Redis client, or None when disabled/unavailable."""
    return await get_redis()
