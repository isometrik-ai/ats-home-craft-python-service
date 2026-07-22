"""Maintenance fee invoice event persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class MaintenanceFeeInvoiceEventsRepository(BaseRepository):
    """Database operations for public.maintenance_fee_invoice_events."""

    async def insert(self, *, data: dict[str, Any]) -> dict[str, Any]:
        """Append an invoice timeline event."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO maintenance_fee_invoice_events (
                organization_id,
                invoice_id,
                event_type,
                occurred_at,
                actor_user_id,
                notes,
                metadata
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::maintenance_fee_invoice_event_type,
                COALESCE($4, now()),
                $5::uuid,
                $6,
                $7::jsonb
            )
            RETURNING
                id::text AS id,
                organization_id::text AS organization_id,
                invoice_id::text AS invoice_id,
                event_type::text AS event_type,
                occurred_at,
                actor_user_id::text AS actor_user_id,
                notes,
                metadata,
                created_at
            """,
            data["organization_id"],
            data["invoice_id"],
            data["event_type"],
            data.get("occurred_at"),
            data.get("actor_user_id"),
            data.get("notes"),
            data.get("metadata", {}),
        )
        return dict(row)
