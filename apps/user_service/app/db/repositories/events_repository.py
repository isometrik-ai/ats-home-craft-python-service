"""Events repository."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import asyncpg


class EventsRepository:
    """Repository for transactional `events` table operations."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def create_event(
        self,
        *,
        event_id: str,
        event_type: str,
        aggregate_id: str,
        organization_id: str,
        topic: str,
        payload: Mapping[str, Any],
        status: str = "pending",
    ) -> None:
        """Insert a single event row in the current transaction."""
        query = """
            INSERT INTO events (
                event_id,
                event_type,
                aggregate_id,
                organization_id,
                topic,
                payload,
                status
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
        """
        await self.db_connection.execute(
            query,
            event_id,
            event_type,
            aggregate_id,
            organization_id,
            topic,
            json.dumps(dict(payload)),
            status,
        )

    async def bulk_create_events(
        self,
        *,
        events: list[dict[str, Any]],
    ) -> None:
        """Insert multiple event rows in the current transaction."""
        if not events:
            return

        query = """
            INSERT INTO events (
                event_id,
                event_type,
                aggregate_id,
                organization_id,
                topic,
                payload,
                status
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
        """
        values = [
            (
                str(event["event_id"]),
                str(event["event_type"]),
                str(event["aggregate_id"]),
                str(event["organization_id"]),
                str(event["topic"]),
                json.dumps(dict(event["payload"])),
                str(event["status"]),
            )
            for event in events
        ]
        await self.db_connection.executemany(query, values)

    async def update_event_status(
        self,
        *,
        event_id: str,
        status: str,
        mark_published_at: bool = False,
    ) -> None:
        """Update event status, optionally setting published_at."""
        query = """
            UPDATE events
            SET
                status = $2,
                published_at = CASE
                    WHEN $3::boolean THEN COALESCE(published_at, NOW())
                    ELSE published_at
                END
            WHERE event_id = $1
        """
        await self.db_connection.execute(query, event_id, status, mark_published_at)
