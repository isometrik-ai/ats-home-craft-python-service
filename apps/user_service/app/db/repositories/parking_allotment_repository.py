"""Parking allotment persistence: unit-first slot assignments."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_SLOT_FROM_SQL = """
FROM facility_parking_slots fps
JOIN facilities f
  ON f.id = fps.facility_id
 AND LOWER(f.facility_type) = 'parking'
LEFT JOIN towers t
  ON t.id = f.tower_id
LEFT JOIN unit_parking_allotments upa
  ON upa.parking_slot_id = fps.id
 AND upa.status = 'active'::parking_allotment_status
LEFT JOIN units u
  ON u.id = upa.unit_id
"""

_DISPLAY_STATUS_SQL = """
CASE
  WHEN fps.status = 'blocked'::parking_slot_status THEN 'blocked'
  WHEN f.parking_user_type = 'visitors'::parking_user_type
       AND fps.status = 'available'::parking_slot_status THEN 'visitor_pool'
  WHEN fps.status = 'assigned'::parking_slot_status
       OR upa.id IS NOT NULL THEN 'allotted'
  ELSE 'free'
END
"""

_SLOT_TYPE_SQL = """
CASE
  WHEN f.parking_user_type = 'visitors'::parking_user_type THEN 'visitor'
  WHEN LOWER(COALESCE(f.facility_subtype, '')) LIKE '%ev%' THEN 'ev_charging'
  WHEN LOWER(COALESCE(f.facility_subtype, '')) LIKE '%two%wheel%' THEN 'two_wheeler'
  ELSE 'car_standard'
END
"""


class ParkingAllotmentRepository(BaseRepository):
    """Database operations for parking allotment admin screens."""

    @staticmethod
    def _scope_filter_sql(
        *,
        tower_id: str | None,
        facility_id: str | None,
        start_idx: int,
    ) -> tuple[str, list[Any], int]:
        """Build optional tower/facility SQL filters for slot-scoped queries."""
        parts: list[str] = []
        args: list[Any] = []
        idx = start_idx
        if tower_id:
            parts.append(f"f.tower_id = ${idx}::uuid")
            args.append(tower_id)
            idx += 1
        if facility_id:
            parts.append(f"fps.facility_id = ${idx}::uuid")
            args.append(facility_id)
            idx += 1
        if not parts:
            return "", args, idx
        return f" AND {' AND '.join(parts)}", args, idx

    async def get_summary(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str | None = None,
        facility_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate parking dashboard counts."""
        scope_sql, scope_args, _ = self._scope_filter_sql(
            tower_id=tower_id,
            facility_id=facility_id,
            start_idx=3,
        )
        args: list[Any] = [organization_id, project_id, *scope_args]

        row = await self.db_connection.fetchrow(
            f"""
            SELECT
                COUNT(*)::int AS total_slots,
                COUNT(*) FILTER (
                    WHERE {_DISPLAY_STATUS_SQL} = 'allotted'
                )::int AS allotted,
                COUNT(*) FILTER (
                    WHERE {_DISPLAY_STATUS_SQL} = 'free'
                )::int AS free_to_allot,
                COUNT(*) FILTER (
                    WHERE {_DISPLAY_STATUS_SQL} = 'visitor_pool'
                )::int AS visitor_pool,
                COUNT(*) FILTER (
                    WHERE fps.status = 'blocked'::parking_slot_status
                )::int AS blocked
            {_SLOT_FROM_SQL}
            WHERE fps.organization_id = $1::uuid
              AND fps.project_id = $2::uuid
              {scope_sql}
            """,
            *args,
        )

        units_short = await self.db_connection.fetchval(
            """
            WITH unit_counts AS (
                SELECT
                    u.id,
                    COALESCE(uc.parking_entitlement, 0)::int AS parking_entitlement,
                    COUNT(upa.id) FILTER (
                        WHERE upa.status = 'active'::parking_allotment_status
                    )::int AS slots_assigned
                FROM units u
                LEFT JOIN unit_configs uc ON uc.id = u.config_id
                LEFT JOIN unit_parking_allotments upa
                  ON upa.unit_id = u.id
                 AND upa.organization_id = u.organization_id
                 AND upa.project_id = u.project_id
                WHERE u.organization_id = $1::uuid
                  AND u.project_id = $2::uuid
                  AND u.is_parking = false
                  AND COALESCE(uc.parking_entitlement, 0) > 0
                  AND ($3::uuid IS NULL OR u.tower_id = $3::uuid)
                GROUP BY u.id, uc.parking_entitlement
            )
            SELECT COUNT(*)::int
            FROM unit_counts
            WHERE slots_assigned < parking_entitlement
            """,
            organization_id,
            project_id,
            tower_id,
        )

        summary = dict(row) if row else {}
        summary["units_short_of_entitlement"] = int(units_short or 0)
        return summary

    async def list_slots(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str | None = None,
        facility_id: str | None = None,
        floor_level: str | None = None,
        slot_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List parking slots for the by-slot admin view."""
        conditions = [
            "fps.organization_id = $1::uuid",
            "fps.project_id = $2::uuid",
        ]
        args: list[Any] = [organization_id, project_id]
        idx = 3

        if tower_id:
            conditions.append(f"f.tower_id = ${idx}::uuid")
            args.append(tower_id)
            idx += 1
        if facility_id:
            conditions.append(f"fps.facility_id = ${idx}::uuid")
            args.append(facility_id)
            idx += 1
        if floor_level:
            conditions.append(f"LOWER(COALESCE(f.floor_level, '')) = LOWER(${idx})")
            args.append(floor_level.strip())
            idx += 1
        if slot_type:
            conditions.append(f"{_SLOT_TYPE_SQL} = ${idx}")
            args.append(slot_type)
            idx += 1
        if status:
            conditions.append(f"{_DISPLAY_STATUS_SQL} = ${idx}")
            args.append(status)
            idx += 1
        if search:
            needle = f"%{search.strip()}%"
            conditions.append(
                f"""(
                    COALESCE(fps.slot_code, '') ILIKE ${idx}
                    OR CONCAT(
                        COALESCE(t.code, ''), '-',
                        COALESCE(f.floor_level, ''), '-',
                        LPAD(fps.slot_number::text, 3, '0')
                    ) ILIKE ${idx}
                    OR COALESCE(u.code, '') ILIKE ${idx}
                )"""
            )
            args.append(needle)
            idx += 1

        where_sql = " AND ".join(conditions)
        offset = (page - 1) * page_size

        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            {_SLOT_FROM_SQL}
            WHERE {where_sql}
            """,
            *args,
        )

        rows = await self.db_connection.fetch(
            f"""
            SELECT
                fps.id,
                fps.slot_number,
                fps.slot_code,
                fps.status AS slot_status,
                fps.created_at,
                fps.updated_at,
                f.id AS facility_id,
                f.name AS facility_name,
                f.floor_level,
                f.wing,
                f.facility_subtype,
                f.parking_user_type,
                t.id AS tower_id,
                t.code AS tower_code,
                t.name AS tower_name,
                upa.id AS allotment_id,
                upa.effective_from,
                upa.allotment_basis,
                upa.created_at AS allotted_at,
                u.id AS unit_id,
                u.code AS unit_code,
                {_DISPLAY_STATUS_SQL} AS display_status,
                {_SLOT_TYPE_SQL} AS slot_type
            {_SLOT_FROM_SQL}
            WHERE {where_sql}
            ORDER BY t.code NULLS LAST, f.floor_level NULLS LAST, fps.slot_number
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(row) for row in rows], int(total or 0)

    async def get_slot_row(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one slot row with facility and active allotment joins."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
                fps.id,
                fps.slot_number,
                fps.slot_code,
                fps.status AS slot_status,
                fps.created_at,
                fps.updated_at,
                f.id AS facility_id,
                f.name AS facility_name,
                f.floor_level,
                f.wing,
                f.facility_subtype,
                f.parking_user_type,
                t.id AS tower_id,
                t.code AS tower_code,
                t.name AS tower_name,
                upa.id AS allotment_id,
                upa.effective_from,
                upa.allotment_basis,
                upa.created_at AS allotted_at,
                u.id AS unit_id,
                u.code AS unit_code,
                {_DISPLAY_STATUS_SQL} AS display_status,
                {_SLOT_TYPE_SQL} AS slot_type
            {_SLOT_FROM_SQL}
            WHERE fps.organization_id = $1::uuid
              AND fps.project_id = $2::uuid
              AND fps.id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return dict(row) if row else None

    async def list_slot_history(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> list[dict[str, Any]]:
        """Return audit events for one parking slot."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                e.id,
                e.event_type,
                e.unit_id,
                e.allotment_id,
                e.actor_user_id,
                e.payload,
                e.occurred_at,
                u.code AS unit_code
            FROM parking_slot_events e
            LEFT JOIN units u ON u.id = e.unit_id
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.parking_slot_id = $3::uuid
            ORDER BY e.occurred_at DESC, e.id DESC
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return [dict(row) for row in rows]

    async def list_units(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str | None = None,
        entitlement_status: str | None = None,
        search: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List units for the by-unit parking allotment view."""
        conditions = [
            "u.organization_id = $1::uuid",
            "u.project_id = $2::uuid",
            "u.is_parking = false",
        ]
        args: list[Any] = [organization_id, project_id]
        idx = 3

        if tower_id:
            conditions.append(f"u.tower_id = ${idx}::uuid")
            args.append(tower_id)
            idx += 1
        if search:
            needle = f"%{search.strip()}%"
            conditions.append(f"(u.code ILIKE ${idx} OR COALESCE(u.unit_label, '') ILIKE ${idx})")
            args.append(needle)
            idx += 1

        having_sql = ""
        if entitlement_status == "met":
            having_sql = (
                "HAVING COUNT(upa.id) FILTER (WHERE upa.status = 'active') "
                ">= COALESCE(uc.parking_entitlement, 0) "
                "AND COALESCE(uc.parking_entitlement, 0) > 0"
            )
        elif entitlement_status == "short":
            having_sql = (
                "HAVING COUNT(upa.id) FILTER (WHERE upa.status = 'active') "
                "< COALESCE(uc.parking_entitlement, 0) "
                "AND COALESCE(uc.parking_entitlement, 0) > 0"
            )

        where_sql = " AND ".join(conditions)
        offset = (page - 1) * page_size

        base_from = f"""
            FROM units u
            LEFT JOIN unit_configs uc ON uc.id = u.config_id
            LEFT JOIN unit_parking_allotments upa
              ON upa.unit_id = u.id
             AND upa.organization_id = u.organization_id
             AND upa.project_id = u.project_id
            WHERE {where_sql}
            GROUP BY
                u.id,
                u.code,
                u.unit_label,
                u.tower_id,
                uc.display_label,
                uc.name,
                uc.parking_entitlement
        """

        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            FROM (
                SELECT u.id
                {base_from}
                {having_sql}
            ) counted
            """,
            *args,
        )

        rows = await self.db_connection.fetch(
            f"""
            SELECT
                u.id,
                u.code,
                u.unit_label,
                u.tower_id,
                COALESCE(uc.display_label, uc.name) AS configuration_label,
                COALESCE(uc.parking_entitlement, 0)::int AS parking_entitlement,
                COUNT(upa.id) FILTER (
                    WHERE upa.status = 'active'::parking_allotment_status
                )::int AS slots_assigned,
                COALESCE(
                    json_agg(
                        json_build_object(
                            'allotment_id', upa.id,
                            'slot_id', upa.parking_slot_id,
                            'effective_from', upa.effective_from,
                            'allotment_basis', upa.allotment_basis
                        )
                        ORDER BY upa.effective_from, upa.created_at
                    ) FILTER (WHERE upa.id IS NOT NULL AND upa.status = 'active'),
                    '[]'::json
                ) AS active_allotments
            {base_from}
            {having_sql}
            ORDER BY u.code
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(row) for row in rows], int(total or 0)

    async def get_unit_for_allotment_view(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one unit row for the by-unit parking allotment view."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                u.id,
                u.code,
                u.unit_label,
                u.tower_id,
                COALESCE(uc.display_label, uc.name) AS configuration_label,
                COALESCE(uc.parking_entitlement, 0)::int AS parking_entitlement,
                COUNT(upa.id) FILTER (
                    WHERE upa.status = 'active'::parking_allotment_status
                )::int AS slots_assigned,
                COALESCE(
                    json_agg(
                        json_build_object(
                            'allotment_id', upa.id,
                            'slot_id', upa.parking_slot_id,
                            'effective_from', upa.effective_from,
                            'allotment_basis', upa.allotment_basis
                        )
                        ORDER BY upa.effective_from, upa.created_at
                    ) FILTER (WHERE upa.id IS NOT NULL AND upa.status = 'active'),
                    '[]'::json
                ) AS active_allotments
            FROM units u
            LEFT JOIN unit_configs uc ON uc.id = u.config_id
            LEFT JOIN unit_parking_allotments upa
              ON upa.unit_id = u.id
             AND upa.organization_id = u.organization_id
             AND upa.project_id = u.project_id
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.id = $3::uuid
              AND u.is_parking = false
            GROUP BY
                u.id,
                u.code,
                u.unit_label,
                u.tower_id,
                uc.display_label,
                uc.name,
                uc.parking_entitlement
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return dict(row) if row else None

    async def get_unit_allotment_context(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch unit row with parking entitlement and active allotment count."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                u.id,
                u.code,
                u.is_parking,
                COALESCE(uc.parking_entitlement, 0)::int AS parking_entitlement,
                COUNT(upa.id) FILTER (
                    WHERE upa.status = 'active'::parking_allotment_status
                      AND upa.allotment_basis = 'included_with_unit'::parking_allotment_basis
                )::int AS included_slots_assigned,
                COUNT(upa.id) FILTER (
                    WHERE upa.status = 'active'::parking_allotment_status
                )::int AS slots_assigned
            FROM units u
            LEFT JOIN unit_configs uc ON uc.id = u.config_id
            LEFT JOIN unit_parking_allotments upa
              ON upa.unit_id = u.id
             AND upa.organization_id = u.organization_id
             AND upa.project_id = u.project_id
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.id = $3::uuid
            GROUP BY u.id, u.code, u.is_parking, uc.parking_entitlement
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return dict(row) if row else None

    async def get_active_allotment_by_slot(
        self,
        *,
        organization_id: str,
        project_id: str,
        slot_id: str,
    ) -> dict[str, Any] | None:
        """Return the active allotment on a slot, if any."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM unit_parking_allotments
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND parking_slot_id = $3::uuid
              AND status = 'active'::parking_allotment_status
            LIMIT 1
            """,
            organization_id,
            project_id,
            slot_id,
        )
        return dict(row) if row else None

    async def insert_allotment(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        parking_slot_id: str,
        allotment_basis: str,
        effective_from: Any,
        created_by_user_id: str | None,
    ) -> dict[str, Any]:
        """Create an active unit parking allotment."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO unit_parking_allotments (
                organization_id,
                project_id,
                unit_id,
                parking_slot_id,
                allotment_basis,
                effective_from,
                created_by_user_id,
                updated_by_user_id
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::uuid,
                $5::parking_allotment_basis,
                $6::date,
                $7::uuid,
                $7::uuid
            )
            RETURNING *
            """,
            organization_id,
            project_id,
            unit_id,
            parking_slot_id,
            allotment_basis,
            effective_from,
            created_by_user_id,
        )
        return dict(row)

    async def release_allotment(
        self,
        *,
        organization_id: str,
        project_id: str,
        allotment_id: str,
        release_reason: str | None,
        updated_by_user_id: str | None,
    ) -> dict[str, Any] | None:
        """Mark an allotment as released."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE unit_parking_allotments
            SET status = 'released'::parking_allotment_status,
                released_at = now(),
                release_reason = $4,
                updated_by_user_id = $5::uuid,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
              AND status = 'active'::parking_allotment_status
            RETURNING *
            """,
            organization_id,
            project_id,
            allotment_id,
            release_reason,
            updated_by_user_id,
        )
        return dict(row) if row else None

    async def clear_vehicle_slot_references(
        self,
        *,
        organization_id: str,
        parking_slot_id: str,
    ) -> None:
        """Detach vehicles from a released parking slot."""
        await self.db_connection.execute(
            """
            UPDATE vehicles
            SET parking_slot_id = NULL,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND parking_slot_id = $2::uuid
            """,
            organization_id,
            parking_slot_id,
        )

    async def insert_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        parking_slot_id: str,
        event_type: str,
        unit_id: str | None = None,
        allotment_id: str | None = None,
        actor_user_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a parking slot audit event."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO parking_slot_events (
                organization_id,
                project_id,
                parking_slot_id,
                event_type,
                unit_id,
                allotment_id,
                actor_user_id,
                payload
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::parking_slot_event_type,
                $5::uuid,
                $6::uuid,
                $7::uuid,
                $8::jsonb
            )
            RETURNING *
            """,
            organization_id,
            project_id,
            parking_slot_id,
            event_type,
            unit_id,
            allotment_id,
            actor_user_id,
            json.dumps(payload or {}),
        )
        return dict(row)
