"""Expire walk-in requests left awaiting with no resident action (cron job)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

from apps.user_service.app.db.repositories.walk_in_repository import WalkInRepository

AWAITING_WALK_IN_TTL_HOURS = 1


async def expire_awaiting_walk_ins(db_connection: asyncpg.Connection) -> list[str]:
    """Expire walk-ins that are still awaiting after the TTL."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=AWAITING_WALK_IN_TTL_HOURS)
    repo = WalkInRepository(db_connection)
    return await repo.expire_stale_awaiting_entries(
        cutoff=cutoff,
        ttl_hours=AWAITING_WALK_IN_TTL_HOURS,
    )
