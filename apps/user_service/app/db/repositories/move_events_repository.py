"""Move events persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_MOVE_EVENT_SELECT_SQL = """
SELECT
  me.id::text AS id,
  me.organization_id::text AS organization_id,
  me.project_id::text AS project_id,
  me.unit_id::text AS unit_id,
  me.contact_id::text AS contact_id,
  me.contact_unit_id::text AS contact_unit_id,
  me.move_type::text AS move_type,
  me.event_date,
  me.fee_amount,
  me.fee_currency,
  me.notes,
  me.document_paths,
  me.recorded_by_user_id::text AS recorded_by_user_id,
  me.deleted_at,
  me.created_at,
  me.updated_at,
  u.code AS unit_code,
  u.unit_label,
  t.name AS unit_tower_name,
  uc.config_kind::text AS unit_type,
  c.first_name AS contact_first_name,
  c.last_name AS contact_last_name,
  c.prefix AS contact_prefix,
  c.contact_type::text AS contact_role
FROM move_events me
JOIN units u ON u.id = me.unit_id
JOIN contacts c ON c.id = me.contact_id
LEFT JOIN towers t ON t.id = u.tower_id
LEFT JOIN unit_configs uc ON uc.id = u.config_id
WHERE me.organization_id = $1::uuid
  AND me.deleted_at IS NULL
"""


class MoveEventsRepository(BaseRepository):
    """Database operations for public.move_events."""

    UPDATABLE_FIELDS: frozenset[str] = frozenset(
        {
            "event_date",
            "fee_amount",
            "fee_currency",
            "notes",
            "document_paths",
        }
    )

    async def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a move event row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO move_events (
                organization_id,
                project_id,
                unit_id,
                contact_id,
                contact_unit_id,
                move_type,
                event_date,
                fee_amount,
                fee_currency,
                notes,
                document_paths,
                recorded_by_user_id
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::uuid,
                $5::uuid,
                $6::move_event_type,
                $7::date,
                $8,
                $9,
                $10,
                $11::text[],
                $12::uuid
            )
            RETURNING id::text AS id
            """,
            data["organization_id"],
            data["project_id"],
            data["unit_id"],
            data["contact_id"],
            data.get("contact_unit_id"),
            data["move_type"],
            data["event_date"],
            data.get("fee_amount"),
            data.get("fee_currency", "INR"),
            data.get("notes"),
            data.get("document_paths") or [],
            data.get("recorded_by_user_id"),
        )
        return dict(row)

    async def get_by_id(
        self,
        *,
        organization_id: str,
        move_event_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one move event with display joins."""
        row = await self.db_connection.fetchrow(
            f"""
            {_MOVE_EVENT_SELECT_SQL}
              AND me.id = $2::uuid
            LIMIT 1
            """,
            organization_id,
            move_event_id,
        )
        return dict(row) if row else None

    async def list(
        self,
        *,
        organization_id: str,
        bucket: str | None = None,
        search: str | None = None,
        unit_id: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List move events for an organization."""
        args: list[Any] = [organization_id]
        where: list[str] = []
        idx = 2

        if bucket:
            where.append(f"me.move_type = ${idx}::move_event_type")
            args.append(bucket)
            idx += 1
        if unit_id:
            where.append(f"me.unit_id = ${idx}::uuid")
            args.append(unit_id)
            idx += 1
        if project_id:
            where.append(f"me.project_id = ${idx}::uuid")
            args.append(project_id)
            idx += 1
        if search:
            where.append(
                f"(u.code ILIKE ${idx} OR u.unit_label ILIKE ${idx} "
                f"OR c.first_name ILIKE ${idx} OR c.last_name ILIKE ${idx} "
                f"OR CONCAT_WS(' ', c.first_name, c.last_name) ILIKE ${idx})"
            )
            pattern = f"%{search.strip()}%"
            args.append(pattern)
            idx += 1

        extra_where = f" AND {' AND '.join(where)}" if where else ""
        offset = (page - 1) * page_size

        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM move_events me
            JOIN units u ON u.id = me.unit_id
            JOIN contacts c ON c.id = me.contact_id
            WHERE me.organization_id = $1::uuid
              AND me.deleted_at IS NULL
              {extra_where}
            """,
            *args,
        )

        rows = await self.db_connection.fetch(
            f"""
            {_MOVE_EVENT_SELECT_SQL}
              {extra_where}
            ORDER BY me.event_date DESC, me.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *args,
            page_size,
            offset,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def update(
        self,
        *,
        organization_id: str,
        move_event_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch allowed columns on a move event."""
        if not update_data:
            return await self.get_by_id(
                organization_id=organization_id,
                move_event_id=move_event_id,
            )

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col not in self.UPDATABLE_FIELDS:
                continue
            if col == "event_date":
                set_parts.append(f"{col} = ${idx}::date")
            elif col == "document_paths":
                set_parts.append(f"{col} = ${idx}::text[]")
            else:
                set_parts.append(f"{col} = ${idx}")
            values.append(val)
            idx += 1

        if not set_parts:
            return await self.get_by_id(
                organization_id=organization_id,
                move_event_id=move_event_id,
            )

        set_parts.append("updated_at = now()")
        values.extend([organization_id, move_event_id])
        org_idx = idx
        id_idx = idx + 1

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE move_events
            SET {", ".join(set_parts)}
            WHERE organization_id = ${org_idx}::uuid
              AND id = ${id_idx}::uuid
              AND deleted_at IS NULL
            RETURNING id::text AS id
            """,
            *values,
        )
        if not row:
            return None
        return await self.get_by_id(
            organization_id=organization_id,
            move_event_id=move_event_id,
        )

    async def soft_delete(
        self,
        *,
        organization_id: str,
        move_event_id: str,
    ) -> dict[str, Any] | None:
        """Soft-void a move event."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE move_events
            SET deleted_at = now(),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
              AND deleted_at IS NULL
            RETURNING
                id::text AS id,
                unit_id::text AS unit_id,
                contact_id::text AS contact_id,
                contact_unit_id::text AS contact_unit_id,
                move_type::text AS move_type,
                event_date
            """,
            organization_id,
            move_event_id,
        )
        return dict(row) if row else None

    async def get_latest_for_unit_contact(
        self,
        *,
        organization_id: str,
        unit_id: str,
        contact_id: str,
    ) -> dict[str, Any] | None:
        """Latest non-deleted move event for a unit+contact pair."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                id::text AS id,
                contact_unit_id::text AS contact_unit_id,
                move_type::text AS move_type,
                event_date
            FROM move_events
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND contact_id = $3::uuid
              AND deleted_at IS NULL
            ORDER BY event_date DESC, created_at DESC
            LIMIT 1
            """,
            organization_id,
            unit_id,
            contact_id,
        )
        return dict(row) if row else None

    async def contact_exists(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """True if contact exists in the org and is not deleted."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contacts
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
              AND status <> 'deleted'
            LIMIT 1
            """,
            organization_id,
            contact_id,
        )
        return row is not None
