"""Walk-in visit persistence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import WALK_IN_RESIDENT_CONTACT_TYPES
from apps.user_service.app.utils.common_utils import serialize_jsonb_param


class WalkInRepository(BaseRepository):
    """Database operations for walk-in workflow tables."""

    async def fetch_units_for_flats(
        self,
        *,
        organization_id: str,
        project_id: str,
        flats: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Validate flats belong to the project and match tower_id."""
        if not flats:
            return []
        unit_ids = [flat["unit_id"] for flat in flats]
        rows = await self.db_connection.fetch(
            """
            SELECT
              u.id::text AS unit_id,
              u.tower_id::text AS tower_id,
              u.code AS unit_code,
              u.unit_label,
              u.project_id::text AS project_id
            FROM units u
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.id = ANY($3::uuid[])
            """,
            organization_id,
            project_id,
            unit_ids,
        )
        by_unit = {str(row["unit_id"]): dict(row) for row in rows}
        validated: list[dict[str, Any]] = []
        for flat in flats:
            row = by_unit.get(flat["unit_id"])
            if not row or row.get("tower_id") != flat["tower_id"]:
                return []
            validated.append(row)
        if len(validated) != len(flats):
            return []
        return validated

    async def insert_entry(
        self,
        *,
        organization_id: str,
        project_id: str,
        visitor_first_name: str,
        visitor_last_name: str | None,
        visitor_phone_isd_code: str,
        visitor_phone_number: str,
        visitor_photo_paths: list[str],
        vehicle_photo_paths: list[str],
        notes: str | None,
        flats_count: int,
        requested_by_user_id: str,
        gate_id: str | None,
    ) -> dict[str, Any]:
        """Insert a walk_in_entries header row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO walk_in_entries (
                organization_id,
                project_id,
                visitor_first_name,
                visitor_last_name,
                visitor_phone_isd_code,
                visitor_phone_number,
                visitor_photo_paths,
                vehicle_photo_paths,
                notes,
                flats_count,
                requested_by_user_id,
                gate_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11::uuid, $12::uuid
            )
            RETURNING *
            """,
            organization_id,
            project_id,
            visitor_first_name,
            visitor_last_name,
            visitor_phone_isd_code,
            visitor_phone_number,
            visitor_photo_paths,
            vehicle_photo_paths,
            notes,
            flats_count,
            requested_by_user_id,
            gate_id,
        )
        return dict(row)

    async def insert_visit_unit(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        tower_id: str,
        unit_id: str,
        sort_order: int,
    ) -> dict[str, Any]:
        """Insert one walk_in_visit_units row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO walk_in_visit_units (
                organization_id,
                walk_in_entry_id,
                tower_id,
                unit_id,
                sort_order
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5)
            RETURNING *
            """,
            organization_id,
            walk_in_entry_id,
            tower_id,
            unit_id,
            sort_order,
        )
        return dict(row)

    async def insert_event(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        event_type: str,
        actor_type: str | None = None,
        actor_user_id: str | None = None,
        actor_contact_id: str | None = None,
        actor_label: str | None = None,
        walk_in_visit_unit_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a walk_in_events row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO walk_in_events (
                organization_id,
                walk_in_entry_id,
                walk_in_visit_unit_id,
                event_type,
                actor_type,
                actor_user_id,
                actor_contact_id,
                actor_label,
                payload
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::walk_in_event_type,
                $5::walk_in_actor_type,
                $6::uuid,
                $7::uuid,
                $8,
                $9::jsonb
            )
            RETURNING *
            """,
            organization_id,
            walk_in_entry_id,
            walk_in_visit_unit_id,
            event_type,
            actor_type,
            actor_user_id,
            actor_contact_id,
            actor_label,
            serialize_jsonb_param("payload", payload or {}, frozenset({"payload"})),
        )
        return dict(row)

    async def get_entry(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a walk-in entry scoped to the organization."""
        if project_id:
            row = await self.db_connection.fetchrow(
                """
                SELECT *
                FROM walk_in_entries
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                  AND id = $3::uuid
                """,
                organization_id,
                project_id,
                walk_in_entry_id,
            )
        else:
            row = await self.db_connection.fetchrow(
                """
                SELECT *
                FROM walk_in_entries
                WHERE organization_id = $1::uuid
                  AND id = $2::uuid
                """,
                organization_id,
                walk_in_entry_id,
            )
        return dict(row) if row else None

    async def list_entries(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
        on_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """List walk-in entries for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              e.*,
              (
                SELECT COALESCE(u.unit_label, u.code)
                FROM walk_in_visit_units vu
                JOIN units u
                  ON u.id = vu.unit_id
                 AND u.organization_id = vu.organization_id
                WHERE vu.walk_in_entry_id = e.id
                ORDER BY vu.sort_order, vu.created_at
                LIMIT 1
              ) AS primary_unit_label
            FROM walk_in_entries e
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND ($3::walk_in_status IS NULL OR e.status = $3::walk_in_status)
              AND (
                $4::date IS NULL
                OR e.requested_at >= $4::date
                   AND e.requested_at < ($4::date + INTERVAL '1 day')
              )
            ORDER BY e.requested_at DESC
            """,
            organization_id,
            project_id,
            status,
            on_date,
        )
        return [dict(row) for row in rows]

    async def list_visit_units(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
    ) -> list[dict[str, Any]]:
        """List visit units for an entry with tower/unit labels."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              vu.id::text AS id,
              vu.organization_id::text AS organization_id,
              vu.walk_in_entry_id::text AS walk_in_entry_id,
              vu.tower_id::text AS tower_id,
              vu.unit_id::text AS unit_id,
              vu.status::text AS status,
              vu.rejection_reason,
              vu.approved_at,
              vu.rejected_at,
              vu.sort_order,
              vu.created_at,
              vu.updated_at,
              t.name AS tower_name,
              u.code AS unit_code,
              u.unit_label
            FROM walk_in_visit_units vu
            JOIN towers t
              ON t.id = vu.tower_id
             AND t.organization_id = vu.organization_id
            JOIN units u
              ON u.id = vu.unit_id
             AND u.organization_id = vu.organization_id
            WHERE vu.organization_id = $1::uuid
              AND vu.walk_in_entry_id = $2::uuid
            ORDER BY vu.sort_order, vu.created_at
            """,
            organization_id,
            walk_in_entry_id,
        )
        return [dict(row) for row in rows]

    async def list_events(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
    ) -> list[dict[str, Any]]:
        """List timeline events for an entry."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM walk_in_events
            WHERE organization_id = $1::uuid
              AND walk_in_entry_id = $2::uuid
            ORDER BY occurred_at, created_at
            """,
            organization_id,
            walk_in_entry_id,
        )
        return [dict(row) for row in rows]

    _VISIT_UNIT_SELECT_SQL = """
            SELECT
              vu.id::text AS id,
              vu.organization_id::text AS organization_id,
              vu.walk_in_entry_id::text AS walk_in_entry_id,
              vu.tower_id::text AS tower_id,
              vu.unit_id::text AS unit_id,
              vu.status::text AS status,
              vu.rejection_reason,
              vu.approved_at,
              vu.rejected_at,
              vu.sort_order,
              vu.created_at,
              vu.updated_at,
              t.name AS tower_name,
              u.code AS unit_code,
              u.unit_label
            FROM walk_in_visit_units vu
            LEFT JOIN towers t
              ON t.id = vu.tower_id
             AND t.organization_id = vu.organization_id
            LEFT JOIN units u
              ON u.id = vu.unit_id
             AND u.organization_id = vu.organization_id
    """

    async def get_visit_unit(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one visit unit on an entry by row id or flat unit id."""
        row = await self.db_connection.fetchrow(
            f"""
            {self._VISIT_UNIT_SELECT_SQL}
            WHERE vu.organization_id = $1::uuid
              AND vu.walk_in_entry_id = $2::uuid
              AND vu.id = $3::uuid
            """,
            organization_id,
            walk_in_entry_id,
            visit_unit_id,
        )
        if row:
            return dict(row)
        row = await self.db_connection.fetchrow(
            f"""
            {self._VISIT_UNIT_SELECT_SQL}
            WHERE vu.organization_id = $1::uuid
              AND vu.walk_in_entry_id = $2::uuid
              AND vu.unit_id = $3::uuid
            """,
            organization_id,
            walk_in_entry_id,
            visit_unit_id,
        )
        return dict(row) if row else None

    async def update_visit_unit_status(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        visit_unit_id: str,
        status: str,
        approved_by_contact_id: str | None = None,
        rejected_by_contact_id: str | None = None,
        rejection_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a visit unit after resident approve/reject."""
        now = datetime.now(timezone.utc)
        row = await self.db_connection.fetchrow(
            """
            UPDATE walk_in_visit_units
            SET status = $4::walk_in_visit_unit_status,
                approved_by_contact_id = CASE
                    WHEN $4::text = 'approved' THEN $5::uuid
                    ELSE approved_by_contact_id
                END,
                rejected_by_contact_id = CASE
                    WHEN $4::text = 'rejected' THEN $6::uuid
                    ELSE rejected_by_contact_id
                END,
                rejection_reason = CASE
                    WHEN $4::text = 'rejected' THEN $7
                    ELSE rejection_reason
                END,
                approved_at = CASE
                    WHEN $4::text = 'approved' THEN $8::timestamptz
                    ELSE approved_at
                END,
                rejected_at = CASE
                    WHEN $4::text = 'rejected' THEN $8::timestamptz
                    ELSE rejected_at
                END,
                updated_at = $8::timestamptz
            WHERE organization_id = $1::uuid
              AND walk_in_entry_id = $2::uuid
              AND id = $3::uuid
              AND status = 'awaiting'::walk_in_visit_unit_status
            RETURNING *
            """,
            organization_id,
            walk_in_entry_id,
            visit_unit_id,
            status,
            approved_by_contact_id,
            rejected_by_contact_id,
            rejection_reason,
            now,
        )
        return dict(row) if row else None

    async def count_visit_units_by_status(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
    ) -> dict[str, int]:
        """Count visit units grouped by status for header recompute."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE status = 'approved'::walk_in_visit_unit_status)::int
                AS approved_count,
              COUNT(*) FILTER (WHERE status = 'awaiting'::walk_in_visit_unit_status)::int
                AS awaiting_count,
              COUNT(*) FILTER (WHERE status = 'rejected'::walk_in_visit_unit_status)::int
                AS rejected_count
            FROM walk_in_visit_units
            WHERE organization_id = $1::uuid
              AND walk_in_entry_id = $2::uuid
            """,
            organization_id,
            walk_in_entry_id,
        )
        return dict(row) if row else {"approved_count": 0, "awaiting_count": 0, "rejected_count": 0}

    async def update_entry_header(
        self,
        *,
        organization_id: str,
        walk_in_entry_id: str,
        status: str | None = None,
        approved_flats_count: int | None = None,
        entered_at: datetime | None = None,
        exited_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Patch walk-in header fields."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE walk_in_entries
            SET status = COALESCE($3::walk_in_status, status),
                approved_flats_count = COALESCE($4, approved_flats_count),
                entered_at = COALESCE($5, entered_at),
                exited_at = COALESCE($6, exited_at),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING *
            """,
            organization_id,
            walk_in_entry_id,
            status,
            approved_flats_count,
            entered_at,
            exited_at,
        )
        return dict(row) if row else None

    async def resident_can_act_on_unit(
        self,
        *,
        organization_id: str,
        contact_id: str,
        unit_id: str,
    ) -> bool:
        """True when contact is Owner/Family/Tenant with active link to unit."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contact_units cu
            JOIN contacts c ON c.id = cu.contact_id
            WHERE cu.organization_id = $1::uuid
              AND cu.contact_id = $2::uuid
              AND cu.unit_id = $3::uuid
              AND cu.status = 'active'::contact_unit_status
              AND c.contact_type = ANY($4::text[])
            LIMIT 1
            """,
            organization_id,
            contact_id,
            unit_id,
            list(WALK_IN_RESIDENT_CONTACT_TYPES),
        )
        return row is not None

    async def list_resident_recipients_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> list[dict[str, Any]]:
        """Return distinct resident contacts on a unit that have Supabase user ids."""
        from apps.user_service.app.db.repositories.contacts_repository import (
            ContactsRepository,
        )

        return await ContactsRepository(self.db_connection).list_unit_resident_recipients(
            organization_id=organization_id,
            unit_id=unit_id,
        )

    async def list_resident_visit_units(
        self,
        *,
        organization_id: str,
        contact_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List visit units pending action for flats the contact occupies."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              vu.id::text AS visit_unit_id,
              vu.walk_in_entry_id::text AS walk_in_entry_id,
              vu.tower_id::text AS tower_id,
              vu.unit_id::text AS unit_id,
              vu.status::text AS status,
              t.name AS tower_name,
              u.code AS unit_code,
              u.unit_label,
              e.visitor_first_name,
              e.visitor_last_name,
              e.visitor_phone_isd_code,
              e.visitor_phone_number,
              e.visitor_photo_paths,
              e.notes,
              e.requested_at,
              e.flats_count
            FROM walk_in_visit_units vu
            JOIN walk_in_entries e
              ON e.id = vu.walk_in_entry_id
             AND e.organization_id = vu.organization_id
            JOIN towers t
              ON t.id = vu.tower_id
             AND t.organization_id = vu.organization_id
            JOIN units u
              ON u.id = vu.unit_id
             AND u.organization_id = vu.organization_id
            WHERE vu.organization_id = $1::uuid
              AND ($2::walk_in_visit_unit_status IS NULL OR vu.status = $2::walk_in_visit_unit_status)
              AND EXISTS (
                SELECT 1
                FROM contact_units cu
                JOIN contacts c ON c.id = cu.contact_id
                WHERE cu.organization_id = vu.organization_id
                  AND cu.unit_id = vu.unit_id
                  AND cu.contact_id = $3::uuid
                  AND cu.status = 'active'::contact_unit_status
                  AND c.contact_type = ANY($4::text[])
              )
            ORDER BY e.requested_at DESC, vu.sort_order
            """,
            organization_id,
            status,
            contact_id,
            list(WALK_IN_RESIDENT_CONTACT_TYPES),
        )
        return [dict(row) for row in rows]
