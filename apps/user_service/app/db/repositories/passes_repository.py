"""Visitor pass persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    PassDisplayStatus,
    PassListBucket,
    PassStatus,
    PassValidityType,
)

_PASS_SELECT_SQL = """
SELECT
  p.id::text AS id,
  p.organization_id::text AS organization_id,
  p.project_id::text AS project_id,
  p.unit_id::text AS unit_id,
  p.host_contact_id::text AS host_contact_id,
  p.pass_type::text AS pass_type,
  p.guest_name,
  p.guest_phone_isd_code,
  p.guest_phone_number,
  p.visitor_count,
  p.vehicle_number,
  p.purpose,
  p.valid_from,
  p.valid_until,
  p.validity_type::text AS validity_type,
  p.allow_multiple_entries,
  p.is_private,
  p.max_entries,
  p.entry_count,
  p.status::text AS status,
  p.code,
  p.pass_image_path,
  p.notes,
  p.created_by_contact_id::text AS created_by_contact_id,
  p.created_at,
  p.updated_at,
  u.code AS unit_code,
  u.unit_label,
  t.name AS tower_name,
  f.display_name AS floor_name,
  uc.display_label AS config_label
FROM passes p
JOIN units u ON u.id = p.unit_id
LEFT JOIN towers t ON t.id = u.tower_id
LEFT JOIN floors f ON f.id = u.floor_id
LEFT JOIN unit_configs uc ON uc.id = u.config_id
WHERE p.organization_id = $1::uuid
"""

_PASS_GATE_SELECT_SQL = """
SELECT
  p.id::text AS id,
  p.organization_id::text AS organization_id,
  p.project_id::text AS project_id,
  p.unit_id::text AS unit_id,
  u.tower_id::text AS tower_id,
  p.host_contact_id::text AS host_contact_id,
  p.pass_type::text AS pass_type,
  p.guest_name,
  p.guest_phone_isd_code,
  p.guest_phone_number,
  p.visitor_count,
  p.vehicle_number,
  p.purpose,
  p.valid_from,
  p.valid_until,
  p.validity_type::text AS validity_type,
  p.allow_multiple_entries,
  p.is_private,
  p.max_entries,
  p.entry_count,
  p.status::text AS status,
  p.code,
  p.pass_image_path,
  p.notes,
  p.created_by_contact_id::text AS created_by_contact_id,
  p.created_at,
  p.updated_at,
  u.code AS unit_code,
  u.unit_label,
  t.name AS tower_name,
  f.display_name AS floor_name,
  uc.display_label AS config_label,
  host.first_name AS host_first_name,
  host.last_name AS host_last_name,
  creator.first_name AS creator_first_name,
  creator.last_name AS creator_last_name
FROM passes p
JOIN units u ON u.id = p.unit_id
LEFT JOIN towers t ON t.id = u.tower_id
LEFT JOIN floors f ON f.id = u.floor_id
LEFT JOIN unit_configs uc ON uc.id = u.config_id
JOIN contacts host ON host.id = p.host_contact_id
JOIN contacts creator ON creator.id = p.created_by_contact_id
WHERE p.organization_id = $1::uuid
"""


class PassesRepository(BaseRepository):
    """Database operations for public.passes."""

    @staticmethod
    def _bucket_predicate(bucket: str | None, *, param_index: int) -> tuple[str, list[Any]]:
        """Build SQL fragment for list bucket filter."""
        if not bucket:
            return "", []
        if bucket == PassListBucket.UPCOMING.value:
            return (
                f"p.status = ${param_index}::pass_status AND p.valid_from > now()",
                [PassStatus.ACTIVE.value],
            )
        if bucket == PassListBucket.ACTIVE.value:
            return (
                f"p.status = ${param_index}::pass_status"
                f" AND p.valid_from <= now()"
                f" AND p.valid_until >= now()",
                [PassStatus.ACTIVE.value],
            )
        if bucket == PassListBucket.EXPIRED.value:
            return (
                "("
                f"  p.status = ANY(${param_index}::pass_status[])"
                "   OR (p.status = $"
                f"{param_index + 1}::pass_status AND p.valid_until < now())"
                ")",
                [
                    [
                        PassStatus.EXPIRED.value,
                        PassStatus.CANCELLED.value,
                        PassStatus.COMPLETED.value,
                    ],
                    PassStatus.ACTIVE.value,
                ],
            )
        return "", []

    @staticmethod
    def _display_status_predicate(
        display_status: str | None,
        *,
        param_index: int,
    ) -> tuple[str, list[Any]]:
        """Build SQL fragment matching PassesService.derive_display_status."""
        if not display_status:
            return "", []

        if display_status == PassDisplayStatus.CANCELLED.value:
            return (
                f"p.status = ${param_index}::pass_status",
                [PassStatus.CANCELLED.value],
            )

        if display_status == PassDisplayStatus.USED.value:
            return (
                "("
                f"  p.status = ${param_index}::pass_status"
                f"  OR (p.validity_type = ${param_index + 1}::pass_validity_type"
                " AND p.entry_count > 0)"
                ")",
                [PassStatus.COMPLETED.value, PassValidityType.ONE_TIME.value],
            )

        not_cancelled_idx = param_index
        not_completed_idx = param_index + 1
        one_time_idx = param_index + 2
        expired_status_idx = param_index + 3
        not_used = (
            f"p.status <> ${not_cancelled_idx}::pass_status"
            f" AND p.status <> ${not_completed_idx}::pass_status"
            f" AND NOT (p.validity_type = ${one_time_idx}::pass_validity_type"
            " AND p.entry_count > 0)"
        )
        not_used_args = [
            PassStatus.CANCELLED.value,
            PassStatus.COMPLETED.value,
            PassValidityType.ONE_TIME.value,
        ]

        if display_status == PassDisplayStatus.EXPIRED.value:
            return (
                f"({not_used} AND (p.valid_until < now()"
                f" OR p.status = ${expired_status_idx}::pass_status))",
                [*not_used_args, PassStatus.EXPIRED.value],
            )

        if display_status == PassDisplayStatus.UPCOMING.value:
            return (
                f"({not_used} AND p.valid_until >= now()"
                f" AND p.status <> ${expired_status_idx}::pass_status"
                " AND p.valid_from > now())",
                [*not_used_args, PassStatus.EXPIRED.value],
            )

        if display_status == PassDisplayStatus.ACTIVE.value:
            return (
                f"({not_used} AND p.valid_until >= now()"
                f" AND p.status <> ${expired_status_idx}::pass_status"
                " AND p.valid_from <= now())",
                [*not_used_args, PassStatus.EXPIRED.value],
            )

        return "", []

    async def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a pass row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO passes (
                organization_id, project_id, unit_id, host_contact_id,
                pass_type, guest_name, guest_phone_isd_code, guest_phone_number,
                visitor_count, vehicle_number, purpose,
                valid_from, valid_until, validity_type,
                allow_multiple_entries, is_private, max_entries, entry_count,
                status, code, pass_image_path, notes, created_by_contact_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::pass_type, $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14::pass_validity_type,
                $15, $16, $17, $18,
                $19::pass_status, $20, $21, $22, $23::uuid
            )
            RETURNING id::text AS id
            """,
            data["organization_id"],
            data["project_id"],
            data["unit_id"],
            data["host_contact_id"],
            data["pass_type"],
            data["guest_name"],
            data.get("guest_phone_isd_code"),
            data.get("guest_phone_number"),
            data.get("visitor_count", 1),
            data.get("vehicle_number"),
            data.get("purpose"),
            data["valid_from"],
            data["valid_until"],
            data["validity_type"],
            data.get("allow_multiple_entries", False),
            data.get("is_private", False),
            data.get("max_entries"),
            data.get("entry_count", 0),
            data.get("status", PassStatus.ACTIVE.value),
            data["code"],
            data.get("pass_image_path"),
            data.get("notes"),
            data["created_by_contact_id"],
        )
        return dict(row)

    async def get_owned_by_contact(
        self,
        *,
        organization_id: str,
        host_contact_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one pass owned by the host contact."""
        row = await self.db_connection.fetchrow(
            f"""
            {_PASS_SELECT_SQL}
              AND p.host_contact_id = $2::uuid
              AND p.id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            host_contact_id,
            pass_id,
        )
        return dict(row) if row else None

    async def get_by_code(
        self,
        *,
        organization_id: str,
        code: str,
    ) -> dict[str, Any] | None:
        """Fetch an active pass by org-scoped 4-digit code."""
        row = await self.db_connection.fetchrow(
            f"""
            {_PASS_GATE_SELECT_SQL}
              AND p.code = $2
              AND p.status = $3::pass_status
            LIMIT 1
            """,
            organization_id,
            code,
            PassStatus.ACTIVE.value,
        )
        return dict(row) if row else None

    async def get_by_id(
        self,
        *,
        organization_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a pass by id within the organization."""
        row = await self.db_connection.fetchrow(
            f"""
            {_PASS_GATE_SELECT_SQL}
              AND p.id = $2::uuid
            LIMIT 1
            """,
            organization_id,
            pass_id,
        )
        return dict(row) if row else None

    async def increment_entry_count(
        self,
        *,
        organization_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Increment entry_count for a pass."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE passes
            SET entry_count = entry_count + 1,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, entry_count
            """,
            organization_id,
            pass_id,
        )
        return dict(row) if row else None

    async def complete(
        self,
        *,
        organization_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Mark a pass as completed."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE passes
            SET status = $3::pass_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            pass_id,
            PassStatus.COMPLETED.value,
        )
        return dict(row) if row else None

    async def list_by_contact(
        self,
        *,
        organization_id: str,
        host_contact_id: str,
        bucket: str | None = None,
        display_status: str | None = None,
        unit_id: str | None = None,
        pass_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List passes for a host with optional filters."""
        args: list[Any] = [organization_id, host_contact_id]
        where = ["p.host_contact_id = $2::uuid"]
        idx = 3

        bucket_sql, bucket_args = self._bucket_predicate(bucket, param_index=idx)
        if bucket_sql:
            where.append(bucket_sql)
        args.extend(bucket_args)
        idx += len(bucket_args)

        display_sql, display_args = self._display_status_predicate(
            display_status,
            param_index=idx,
        )
        if display_sql:
            where.append(display_sql)
        args.extend(display_args)
        idx += len(display_args)

        if unit_id:
            where.append(f"p.unit_id = ${idx}::uuid")
            args.append(unit_id)
            idx += 1
        if pass_type:
            where.append(f"p.pass_type = ${idx}::pass_type")
            args.append(pass_type)
            idx += 1

        where_sql = " AND ".join(part for part in where if part)
        offset = (page - 1) * page_size

        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM passes p
            WHERE p.organization_id = $1::uuid
              AND {where_sql}
            """,
            *args,
        )

        rows = await self.db_connection.fetch(
            f"""
            {_PASS_SELECT_SQL}
              AND {where_sql}
            ORDER BY p.valid_from DESC, p.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *args,
            page_size,
            offset,
        )
        return [dict(row) for row in rows], int(count or 0)

    async def code_exists_active(
        self,
        *,
        organization_id: str,
        code: str,
    ) -> bool:
        """True if an active pass already uses this code in the org."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM passes
            WHERE organization_id = $1::uuid
              AND code = $2
              AND status = $3::pass_status
            LIMIT 1
            """,
            organization_id,
            code,
            PassStatus.ACTIVE.value,
        )
        return row is not None

    async def update(
        self,
        *,
        organization_id: str,
        host_contact_id: str,
        pass_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch pass fields for an owned pass."""
        if not update_data:
            return await self.get_owned_by_contact(
                organization_id=organization_id,
                host_contact_id=host_contact_id,
                pass_id=pass_id,
            )

        enum_cols = {
            "pass_type": "pass_type",
            "validity_type": "pass_validity_type",
            "status": "pass_status",
        }
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col in enum_cols:
                set_parts.append(f"{col} = ${idx}::{enum_cols[col]}")
            else:
                set_parts.append(f"{col} = ${idx}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([organization_id, host_contact_id, pass_id])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE passes p
            SET {", ".join(set_parts)}
            WHERE p.organization_id = ${idx}::uuid
              AND p.host_contact_id = ${idx + 1}::uuid
              AND p.id = ${idx + 2}::uuid
            RETURNING p.id::text AS id
            """,
            *values,
        )
        if not row:
            return None
        return await self.get_owned_by_contact(
            organization_id=organization_id,
            host_contact_id=host_contact_id,
            pass_id=pass_id,
        )

    async def cancel(
        self,
        *,
        organization_id: str,
        host_contact_id: str,
        pass_id: str,
    ) -> dict[str, Any] | None:
        """Cancel an active pass owned by the host."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE passes
            SET status = $4::pass_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND host_contact_id = $2::uuid
              AND id = $3::uuid
              AND status = $5::pass_status
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            host_contact_id,
            pass_id,
            PassStatus.CANCELLED.value,
            PassStatus.ACTIVE.value,
        )
        return dict(row) if row else None
