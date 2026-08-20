"""Complete past community events (cron job)."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)


async def complete_past_community_events(db_connection: asyncpg.Connection) -> list[str]:
    """Mark published past events as completed."""
    repo = CommunityEventsRepository(db_connection)
    return await repo.complete_past_events()
