"""Persistence for facility reservations, participants and the audit trail."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    decode_jsonb,
)
from apps.user_service.app.schemas.enums import (
    ACTIVE_RESERVATION_STATUSES,
    WEEKLY_CAP_COUNTABLE_STATUSES,
)

RESERVATION_JSONB: frozenset[str] = frozenset({"quote", "cancel_info"})
_EVENT_JSONB: frozenset[str] = frozenset({"payload"})

_RESERVATION_CASTS: dict[str, str] = {
    "status": "::facility_reservation_status",
    "booked_by_actor": "::facility_reservation_actor_type",
}

_RESERVATION_SELECT = """
    SELECT
        r.*,
        f.name AS facility_name,
        f.booking_archetype::text AS archetype,
        u.name AS unit_name,
        NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), '') AS host_name
    FROM facility_reservations r
    INNER JOIN facilities f ON f.id = r.facility_id
    LEFT JOIN facility_booking_units u ON u.id = r.unit_id
    LEFT JOIN contacts c ON c.id = r.host_contact_id
"""

_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "project_id",
    "facility_id",
    "unit_id",
    "host_contact_id",
    "host_unit_id",
    "booked_by_user_id",
    "booked_by_actor",
    "local_date",
    "end_local_date",
    "start_min",
    "end_min",
    "starts_at",
    "ends_at",
    "status",
    "quote",
    "rescheduled_from_id",
    "notes",
    "approved_at",
    "approved_by_user_id",
    "exclusion_key",
)


class FacilityReservationsRepository(BaseRepository):
    """facility_reservations, facility_reservation_participants, facility_reservation_events."""

    async def lock_facility(self, facility_id: str) -> None:
        """Serialize booking writes per facility for the current transaction."""
        await self.db_connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", f"facility_booking:{facility_id}"
        )

    async def insert_reservation(self, data: dict[str, Any]) -> dict[str, Any]:
        present = [col for col in _INSERT_COLUMNS if col in data]
        placeholders: list[str] = []
        values: list[Any] = []
        for idx, col in enumerate(present, start=1):
            cast = "::jsonb" if col in RESERVATION_JSONB else _RESERVATION_CASTS.get(col, "")
            placeholders.append(f"${idx}{cast}")
            values.append(self._param(col, data[col]))
        row = await self.db_connection.fetchrow(
            f"""
            INSERT INTO facility_reservations ({", ".join(present)})
            VALUES ({", ".join(placeholders)})
            RETURNING id::text AS id
            """,
            *values,
        )
        return dict(row)

    @staticmethod
    def _param(col: str, value: Any) -> Any:
        if col in RESERVATION_JSONB and value is not None and not isinstance(value, str):
            return json.dumps(value)
        return value

    async def update_reservation(
        self, *, organization_id: str, reservation_id: str, update_data: dict[str, Any]
    ) -> bool:
        set_parts: list[str] = []
        values: list[Any] = []
        for col, value in update_data.items():
            values.append(self._param(col, value))
            cast = "::jsonb" if col in RESERVATION_JSONB else _RESERVATION_CASTS.get(col, "")
            set_parts.append(f"{col} = ${len(values)}{cast}")
        set_parts.append("updated_at = NOW()")
        values.extend([reservation_id, organization_id])
        n = len(values)
        result = await self.db_connection.execute(
            f"""
            UPDATE facility_reservations SET {", ".join(set_parts)}
            WHERE id = ${n - 1}::uuid AND organization_id = ${n}::uuid
            """,
            *values,
        )
        return result.upper().endswith(" 1")

    async def get_reservation(
        self,
        *,
        organization_id: str,
        project_id: str,
        reservation_id: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        lock = " FOR UPDATE OF r" if for_update else ""
        row = await self.db_connection.fetchrow(
            f"""
            {_RESERVATION_SELECT}
            WHERE r.id = $1::uuid AND r.organization_id = $2::uuid AND r.project_id = $3::uuid
            {lock}
            """,
            reservation_id,
            organization_id,
            project_id,
        )
        return decode_jsonb(dict(row), RESERVATION_JSONB) if row else None

    async def insert_participants(
        self, *, organization_id: str, reservation_id: str, participants: list[dict[str, Any]]
    ) -> None:
        if not participants:
            return
        await self.bulk_insert_returning(
            table="facility_reservation_participants",
            required_columns=[
                "organization_id",
                "reservation_id",
                "kind",
                "contact_id",
                "name",
                "sort_order",
            ],
            optional_columns=[],
            rows=[
                {
                    "organization_id": organization_id,
                    "reservation_id": reservation_id,
                    "kind": p["kind"],
                    "contact_id": p.get("contact_id"),
                    "name": p["name"],
                    "sort_order": idx,
                }
                for idx, p in enumerate(participants)
            ],
        )

    async def participants_by_reservation(
        self, reservation_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        if not reservation_ids:
            return {}
        rows = await self.db_connection.fetch(
            """
            SELECT reservation_id::text AS reservation_id, kind::text AS kind,
                   contact_id::text AS contact_id, name
            FROM facility_reservation_participants
            WHERE reservation_id = ANY($1::uuid[])
            ORDER BY reservation_id, sort_order
            """,
            reservation_ids,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["reservation_id"]].append(dict(row))
        return dict(grouped)

    async def insert_event(
        self,
        *,
        organization_id: str,
        reservation_id: str,
        event_type: str,
        actor_type: str,
        message: str,
        actor_user_id: str | None = None,
        actor_contact_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.bulk_insert_returning(
            table="facility_reservation_events",
            required_columns=[
                "organization_id",
                "reservation_id",
                "event_type",
                "actor_type",
                "actor_user_id",
                "actor_contact_id",
                "message",
                "payload",
            ],
            optional_columns=[],
            rows=[
                {
                    "organization_id": organization_id,
                    "reservation_id": reservation_id,
                    "event_type": event_type,
                    "actor_type": actor_type,
                    "actor_user_id": actor_user_id,
                    "actor_contact_id": actor_contact_id,
                    "message": message,
                    "payload": payload or {},
                }
            ],
            jsonb_columns=_EVENT_JSONB,
        )

    async def list_events(self, reservation_ids: list[str]) -> list[dict[str, Any]]:
        if not reservation_ids:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id, reservation_id::text AS reservation_id,
                   event_type::text AS event_type, actor_type::text AS actor_type,
                   actor_user_id::text AS actor_user_id, actor_contact_id::text AS actor_contact_id,
                   message, occurred_at
            FROM facility_reservation_events
            WHERE reservation_id = ANY($1::uuid[])
            ORDER BY occurred_at, created_at
            """,
            reservation_ids,
        )
        return [dict(r) for r in rows]

    async def list_active_in_range(
        self, *, organization_id: str, facility_id: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Active reservations whose date span intersects [start, end]."""
        rows = await self.db_connection.fetch(
            f"""
            {_RESERVATION_SELECT}
            WHERE r.organization_id = $1::uuid
              AND r.facility_id = $2::uuid
              AND r.status::text = ANY($3::text[])
              AND r.local_date <= $5::date
              AND r.end_local_date >= $4::date
            ORDER BY r.local_date, r.start_min
            """,
            organization_id,
            facility_id,
            list(ACTIVE_RESERVATION_STATUSES),
            start,
            end,
        )
        return [decode_jsonb(dict(r), RESERVATION_JSONB) for r in rows]

    async def list_weekly_pool(
        self,
        *,
        organization_id: str,
        facility_id: str,
        contact_id: str,
        week_start: date,
        week_end: date,
    ) -> list[dict[str, Any]]:
        """Reservations counted towards a resident's weekly cap."""
        rows = await self.db_connection.fetch(
            f"""
            {_RESERVATION_SELECT}
            WHERE r.organization_id = $1::uuid
              AND r.facility_id = $2::uuid
              AND r.status::text = ANY($3::text[])
              AND r.local_date BETWEEN $5::date AND $6::date
              AND (
                  r.host_contact_id = $4::uuid
                  OR EXISTS (
                      SELECT 1 FROM facility_reservation_participants p
                      WHERE p.reservation_id = r.id AND p.contact_id = $4::uuid
                  )
              )
            """,
            organization_id,
            facility_id,
            list(WEEKLY_CAP_COUNTABLE_STATUSES),
            contact_id,
            week_start,
            week_end,
        )
        return [decode_jsonb(dict(r), RESERVATION_JSONB) for r in rows]

    async def list_reservations(
        self,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str | None = None,
        statuses: list[str] | None = None,
        contact_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        exclude_statuses: list[str] | None = None,
        search: str | None = None,
        descending: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["r.organization_id = $1::uuid", "r.project_id = $2::uuid"]
        params: list[Any] = [organization_id, project_id]

        def add(sql: str, value: Any) -> None:
            params.append(value)
            conditions.append(sql.format(n=len(params)))

        if facility_id:
            add("r.facility_id = ${n}::uuid", facility_id)
        if statuses:
            add("r.status::text = ANY(${n}::text[])", statuses)
        if exclude_statuses:
            add("NOT (r.status::text = ANY(${n}::text[]))", exclude_statuses)
        if contact_id:
            add(
                "(r.host_contact_id = ${n}::uuid OR EXISTS ("
                "SELECT 1 FROM facility_reservation_participants p "
                "WHERE p.reservation_id = r.id AND p.contact_id = ${n}::uuid))",
                contact_id,
            )
        if date_from:
            add("r.end_local_date >= ${n}::date", date_from)
        if date_to:
            add("r.local_date <= ${n}::date", date_to)
        if search and search.strip():
            add(
                "(f.name ILIKE ${n} OR CONCAT_WS(' ', c.first_name, c.last_name) ILIKE ${n})",
                f"%{search.strip()}%",
            )
        where_sql = " AND ".join(conditions)
        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            FROM facility_reservations r
            INNER JOIN facilities f ON f.id = r.facility_id
            LEFT JOIN contacts c ON c.id = r.host_contact_id
            WHERE {where_sql}
            """,
            *params,
        )
        order = "DESC" if descending else "ASC"
        n = len(params)
        rows = await self.db_connection.fetch(
            f"""
            {_RESERVATION_SELECT}
            WHERE {where_sql}
            ORDER BY r.local_date {order}, r.start_min {order}, r.created_at {order}
            OFFSET ${n + 1} LIMIT ${n + 2}
            """,
            *params,
            (page - 1) * page_size,
            page_size,
        )
        return [decode_jsonb(dict(r), RESERVATION_JSONB) for r in rows], int(total or 0)

    async def count_upcoming_active(
        self,
        *,
        organization_id: str,
        facility_id: str,
        from_date: date,
        unit_id: str | None = None,
    ) -> int:
        params: list[Any] = [
            organization_id,
            facility_id,
            list(ACTIVE_RESERVATION_STATUSES),
            from_date,
        ]
        unit_sql = ""
        if unit_id:
            params.append(unit_id)
            unit_sql = " AND unit_id = $5::uuid"
        value = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int FROM facility_reservations
            WHERE organization_id = $1::uuid
              AND facility_id = $2::uuid
              AND status::text = ANY($3::text[])
              AND end_local_date >= $4::date
              {unit_sql}
            """,
            *params,
        )
        return int(value or 0)
