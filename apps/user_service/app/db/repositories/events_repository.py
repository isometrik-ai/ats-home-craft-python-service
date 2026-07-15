"""Events repository."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import asyncpg

from libs.shared_utils.logger import get_logger

logger = get_logger("events_repository")


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

    async def fetch_crm_events_for_graphiti_replay(
        self,
        *,
        topic: str,
        organization_id: str | None = None,
        statuses: list[str] | None = None,
        since_occurred_at: str | None = None,
        limit: int | None = None,
        latest_per_aggregate: bool = False,
    ) -> list[dict[str, Any]]:
        """Load stored CRM Kafka envelopes for Graphiti replay/backfill."""
        order_clause = (
            "organization_id, aggregate_id, "
            "(payload->>'occurred_at') DESC NULLS LAST, event_id DESC"
            if latest_per_aggregate
            else "(payload->>'occurred_at') ASC NULLS LAST, event_id ASC"
        )
        distinct_clause = (
            "DISTINCT ON (organization_id, aggregate_id)" if latest_per_aggregate else ""
        )
        query = f"""
            SELECT {distinct_clause}
                event_id,
                event_type,
                aggregate_id,
                organization_id,
                payload,
                status
            FROM events
            WHERE topic = $1
              AND ($2::text IS NULL OR organization_id = $2)
              AND ($3::text[] IS NULL OR status = ANY($3::text[]))
              AND ($4::timestamptz IS NULL OR (payload->>'occurred_at')::timestamptz >= $4)
              AND event_type NOT LIKE 'email.%'
            ORDER BY {order_clause}
            LIMIT COALESCE($5::int, 2147483647)
        """
        rows = await self.db_connection.fetch(
            query,
            topic,
            organization_id,
            statuses,
            since_occurred_at,
            limit,
        )
        return [dict(row) for row in rows]

    async def mark_graphiti_synced(self, *, event_id: str) -> None:
        """Record that a stored CRM event has been applied to Graphiti."""
        query = """
            UPDATE events
            SET graphiti_synced_at = COALESCE(graphiti_synced_at, NOW())
            WHERE event_id = $1
        """
        try:
            await self.db_connection.execute(query, event_id)
        except asyncpg.UndefinedColumnError:
            logger.warning(
                "events_graphiti_synced_at_column_missing "
                "run scripts/sql/add_graphiti_synced_at_to_events.sql"
            )
