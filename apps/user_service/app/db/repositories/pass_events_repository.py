"""Pass timeline event persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import PassEventType


class PassEventsRepository(BaseRepository):
    """Database operations for public.pass_events."""

    async def insert_event(self, data: dict[str, Any]) -> dict[str, Any]:
        """Append a pass timeline event."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO pass_events (
                organization_id, pass_id, event_type,
                gate_id, actor_type, actor_user_id, actor_label,
                occurred_at, notes, metadata,
                entry_method, access_status
            )
            VALUES (
                $1::uuid, $2::uuid, $3::pass_event_type,
                $4::uuid, $5::pass_actor_type, $6::uuid, $7,
                COALESCE($8, now()), $9, COALESCE($10::jsonb, '{}'::jsonb),
                $11::pass_entry_method, $12::pass_access_status
            )
            RETURNING
              id::text AS id,
              pass_id::text AS pass_id,
              event_type::text AS event_type,
              gate_id::text AS gate_id,
              actor_type::text AS actor_type,
              actor_user_id::text AS actor_user_id,
              actor_label,
              occurred_at,
              notes,
              metadata,
              entry_method::text AS entry_method,
              access_status::text AS access_status
            """,
            data["organization_id"],
            data["pass_id"],
            data["event_type"],
            data.get("gate_id"),
            data.get("actor_type"),
            data.get("actor_user_id"),
            data.get("actor_label"),
            data.get("occurred_at"),
            data.get("notes"),
            data.get("metadata"),
            data.get("entry_method"),
            data.get("access_status"),
        )
        return dict(row)

    async def list_by_pass(
        self,
        *,
        organization_id: str,
        pass_id: str,
    ) -> list[dict[str, Any]]:
        """List timeline events for a pass."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              pass_id::text AS pass_id,
              event_type::text AS event_type,
              gate_id::text AS gate_id,
              actor_type::text AS actor_type,
              actor_user_id::text AS actor_user_id,
              actor_label,
              occurred_at,
              notes,
              metadata,
              entry_method::text AS entry_method,
              access_status::text AS access_status
            FROM pass_events
            WHERE organization_id = $1::uuid
              AND pass_id = $2::uuid
            ORDER BY occurred_at ASC, created_at ASC
            """,
            organization_id,
            pass_id,
        )
        return [dict(row) for row in rows]

    async def latest_event_by_type(
        self,
        *,
        organization_id: str,
        pass_id: str,
        event_type: str,
    ) -> dict[str, Any] | None:
        """Return the most recent event of a given type for a pass."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              id::text AS id,
              pass_id::text AS pass_id,
              event_type::text AS event_type,
              gate_id::text AS gate_id,
              actor_type::text AS actor_type,
              actor_user_id::text AS actor_user_id,
              actor_label,
              occurred_at,
              notes,
              metadata,
              entry_method::text AS entry_method,
              access_status::text AS access_status
            FROM pass_events
            WHERE organization_id = $1::uuid
              AND pass_id = $2::uuid
              AND event_type = $3::pass_event_type
            ORDER BY occurred_at DESC, created_at DESC
            LIMIT 1
            """,
            organization_id,
            pass_id,
            event_type,
        )
        return dict(row) if row else None

    async def has_open_check_in(
        self,
        *,
        organization_id: str,
        pass_id: str,
    ) -> bool:
        """True when the latest check-in has no later check-out."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              (
                SELECT pe.occurred_at
                FROM pass_events pe
                WHERE pe.organization_id = $1::uuid
                  AND pe.pass_id = $2::uuid
                  AND pe.event_type = $3::pass_event_type
                ORDER BY pe.occurred_at DESC, pe.created_at DESC
                LIMIT 1
              ) AS last_check_in,
              (
                SELECT pe.occurred_at
                FROM pass_events pe
                WHERE pe.organization_id = $1::uuid
                  AND pe.pass_id = $2::uuid
                  AND pe.event_type = $4::pass_event_type
                ORDER BY pe.occurred_at DESC, pe.created_at DESC
                LIMIT 1
              ) AS last_check_out
            """,
            organization_id,
            pass_id,
            PassEventType.CHECKED_IN.value,
            PassEventType.CHECKED_OUT.value,
        )
        if not row or row["last_check_in"] is None:
            return False
        last_check_out = row["last_check_out"]
        if last_check_out is None:
            return True
        return row["last_check_in"] > last_check_out
