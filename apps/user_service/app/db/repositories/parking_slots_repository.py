"""Facility parking slot persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class ParkingSlotsRepository(BaseRepository):
    """Database operations for public.facility_parking_slots."""

    async def bulk_insert_slots(
        self,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str,
        slot_count: int,
    ) -> list[dict[str, Any]]:
        """Create numbered parking slots for a facility."""
        rows = await self.db_connection.fetch(
            """
            INSERT INTO facility_parking_slots (
                organization_id, project_id, facility_id, slot_number
            )
            SELECT $1::uuid, $2::uuid, $3::uuid, gs
            FROM generate_series(1, $4::int) AS gs
            RETURNING *
            """,
            organization_id,
            project_id,
            facility_id,
            slot_count,
        )
        return [dict(row) for row in rows]

    async def list_by_facility(
        self,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List parking slots for a facility."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM facility_parking_slots
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND facility_id = $3::uuid
              AND ($4::parking_slot_status IS NULL OR status = $4::parking_slot_status)
            ORDER BY slot_number
            """,
            organization_id,
            project_id,
            facility_id,
            status,
        )
        return [dict(row) for row in rows]

    async def get_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a parking slot scoped to org + project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM facility_parking_slots
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return dict(row) if row else None

    async def assign_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any] | None:
        """Mark a slot as assigned."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE facility_parking_slots
            SET status = 'assigned'::parking_slot_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
              AND status = 'available'::parking_slot_status
            RETURNING *
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return dict(row) if row else None

    async def release_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any] | None:
        """Mark a slot as available."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE facility_parking_slots
            SET status = 'available'::parking_slot_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            RETURNING *
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return dict(row) if row else None

    async def delete_by_facility(
        self,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str,
    ) -> None:
        """Delete all slots for a facility."""
        await self.db_connection.execute(
            """
            DELETE FROM facility_parking_slots
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND facility_id = $3::uuid
            """,
            organization_id,
            project_id,
            facility_id,
        )
