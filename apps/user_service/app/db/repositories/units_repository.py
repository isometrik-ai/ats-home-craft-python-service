"""Units and parking zones persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_UNIT_COLUMN_CASTS: dict[str, str] = {"status": "::unit_status"}

_UNIT_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "project_id",
    "tower_id",
    "wing_id",
    "floor_id",
    "config_id",
    "code",
    "unit_label",
    "status",
    "sort_order",
    "is_parking",
    "plot_item_id",
)


class UnitsRepository(BaseRepository):
    """Database operations for public.units and public.parking_zones."""

    # -- units --------------------------------------------------------------

    async def insert_unit(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a unit row."""
        present = [col for col in _UNIT_INSERT_COLUMNS if col in data]
        col_sql = ", ".join(present)
        placeholders = ", ".join(
            f"${idx + 1}{_UNIT_COLUMN_CASTS.get(col, '')}" for idx, col in enumerate(present)
        )
        row = await self.db_connection.fetchrow(
            f"INSERT INTO units ({col_sql}) VALUES ({placeholders}) RETURNING *",
            *[data.get(col) for col in present],
        )
        return dict(row)

    async def get_unit(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any] | None:
        """Fetch a unit scoped to org + project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT * FROM units
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            unit_id,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    async def list_units(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List units for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM units
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY sort_order, code
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def update_unit(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch a unit."""
        if not update_data:
            return await self.get_unit(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
            )
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            set_parts.append(f"{col} = ${idx}{_UNIT_COLUMN_CASTS.get(col, '')}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([unit_id, project_id, organization_id])
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE units SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid
              AND organization_id = ${idx + 2}::uuid
            RETURNING *
            """,
            *values,
        )
        return dict(row) if row else None

    async def delete_unit(self, *, organization_id: str, project_id: str, unit_id: str) -> bool:
        """Delete a unit."""
        result = await self.db_connection.execute(
            """
            DELETE FROM units
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            unit_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")

    async def get_unit_detail_base(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a unit with tower, floor, config, and plot joins."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                u.id,
                u.organization_id,
                u.project_id,
                u.tower_id,
                u.wing_id,
                u.floor_id,
                u.config_id,
                u.code,
                u.unit_label,
                u.status,
                u.sort_order,
                u.is_parking,
                u.plot_item_id,
                u.created_at,
                u.updated_at,
                t.name AS tower_name,
                t.code AS tower_code,
                t.tower_type,
                f.display_name AS floor_display_name,
                f.level_number AS floor_level_number,
                uc.config_kind,
                uc.name AS config_name,
                uc.code AS config_code,
                uc.display_label AS config_display_label,
                uc.bedrooms,
                uc.bathrooms,
                uc.area_sqft,
                uc.carpet_area_sqft,
                uc.parking_entitlement,
                uc.default_facing,
                uc.facing AS config_facing,
                uc.commercial_unit_type,
                pci.plot_no,
                pci.size_sqft AS plot_size_sqft,
                pci.status AS plot_item_status,
                pci.description AS plot_description
            FROM units u
            LEFT JOIN towers t
                ON t.id = u.tower_id
               AND t.organization_id = u.organization_id
            LEFT JOIN floors f
                ON f.id = u.floor_id
               AND f.organization_id = u.organization_id
            LEFT JOIN unit_configs uc
                ON uc.id = u.config_id
               AND uc.organization_id = u.organization_id
            LEFT JOIN plot_config_items pci
                ON pci.id = u.plot_item_id
               AND pci.organization_id = u.organization_id
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return dict(row) if row else None

    async def list_unit_residents(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> list[dict[str, Any]]:
        """List active contacts linked to a unit."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                cu.id AS contact_unit_id,
                cu.contact_id,
                cu.is_primary,
                cu.relationship::text AS relationship,
                cu.status::text AS status,
                c.contact_type,
                c.prefix,
                c.first_name,
                c.last_name
            FROM contact_units cu
            JOIN contacts c
                ON c.id = cu.contact_id
               AND c.organization_id = cu.organization_id
            WHERE cu.organization_id = $1::uuid
              AND cu.unit_id = $2::uuid
              AND cu.status = 'active'::contact_unit_status
              AND c.status = 'active'
            ORDER BY cu.is_primary DESC, cu.sort_order, cu.created_at
            """,
            organization_id,
            unit_id,
        )
        return [dict(row) for row in rows]

    async def count_unit_vehicles(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> tuple[int, int]:
        """Return (approved_vehicle_count, assigned_parking_slot_count) for a unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                COUNT(*)::int AS vehicles_count,
                COUNT(*) FILTER (WHERE parking_slot_id IS NOT NULL)::int AS parking_slots_assigned
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.unit_id = $2::uuid
              AND v.deleted_at IS NULL
              AND v.status = 'approved'::vehicle_status
            """,
            organization_id,
            unit_id,
        )
        if not row:
            return 0, 0
        return int(row["vehicles_count"] or 0), int(row["parking_slots_assigned"] or 0)

    # -- parking zones ------------------------------------------------------

    async def insert_parking_zone(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a parking zone."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO parking_zones (
                organization_id, project_id, tower_id, floor_id, name,
                slot_from, slot_to, sort_order
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8)
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["tower_id"],
            data["floor_id"],
            data["name"],
            data.get("slot_from"),
            data.get("slot_to"),
            data.get("sort_order", 0),
        )
        return dict(row)

    async def list_parking_zones(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List parking zones for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM parking_zones
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def delete_parking_zone(
        self, *, organization_id: str, project_id: str, zone_id: str
    ) -> bool:
        """Delete a parking zone."""
        result = await self.db_connection.execute(
            """
            DELETE FROM parking_zones
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            zone_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")
