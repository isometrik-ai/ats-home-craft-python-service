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
