"""Towers, wings, gates, lifts, and floors persistence."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_TOWER_COLUMN_CASTS: dict[str, str] = {
    "tower_type": "::tower_type",
    "numbering_pattern": "::unit_numbering_pattern",
}

_TOWER_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "project_id",
    "name",
    "code",
    "tower_type",
    "basement_count",
    "upper_floor_count",
    "units_per_floor_default",
    "numbering_pattern",
    "starting_unit_number",
    "custom_prefix",
    "has_wings",
    "latitude",
    "longitude",
    "sort_order",
    "active",
)


class TowersRepository(BaseRepository):
    """Database operations for towers and their child tables."""

    # -- towers -------------------------------------------------------------

    async def insert_tower(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a tower row."""
        present = [col for col in _TOWER_INSERT_COLUMNS if col in data]
        col_sql = ", ".join(present)
        placeholders = ", ".join(
            f"${idx + 1}{_TOWER_COLUMN_CASTS.get(col, '')}" for idx, col in enumerate(present)
        )
        row = await self.db_connection.fetchrow(
            f"INSERT INTO towers ({col_sql}) VALUES ({placeholders}) RETURNING *",
            *[data.get(col) for col in present],
        )
        return dict(row)

    async def get_tower(
        self, *, organization_id: str, project_id: str, tower_id: str
    ) -> dict[str, Any] | None:
        """Fetch a tower scoped to org + project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT * FROM towers
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            tower_id,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    async def list_towers(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List towers for a project ordered by sort_order."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM towers
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def update_tower(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch a tower."""
        if not update_data:
            return await self.get_tower(
                organization_id=organization_id,
                project_id=project_id,
                tower_id=tower_id,
            )
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            set_parts.append(f"{col} = ${idx}{_TOWER_COLUMN_CASTS.get(col, '')}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([tower_id, project_id, organization_id])
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE towers SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid
              AND organization_id = ${idx + 2}::uuid
            RETURNING *
            """,
            *values,
        )
        return dict(row) if row else None

    async def delete_tower(self, *, organization_id: str, project_id: str, tower_id: str) -> bool:
        """Delete a tower (children cascade)."""
        result = await self.db_connection.execute(
            """
            DELETE FROM towers
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            tower_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")

    # -- wings --------------------------------------------------------------

    async def insert_wing(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a tower wing."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tower_wings (
                organization_id, tower_id, name, code, has_own_gate, sort_order
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
            RETURNING *
            """,
            data["organization_id"],
            data["tower_id"],
            data["name"],
            data.get("code"),
            data.get("has_own_gate", False),
            data.get("sort_order", 0),
        )
        return dict(row)

    async def list_wings(self, *, organization_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List wings for a tower."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM tower_wings
            WHERE organization_id = $1::uuid AND tower_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            tower_id,
        )
        return [dict(row) for row in rows]

    async def delete_wing(self, *, organization_id: str, tower_id: str, wing_id: str) -> bool:
        """Delete a wing."""
        result = await self.db_connection.execute(
            """
            DELETE FROM tower_wings
            WHERE id = $1::uuid AND tower_id = $2::uuid AND organization_id = $3::uuid
            """,
            wing_id,
            tower_id,
            organization_id,
        )
        return result.upper().endswith("1")

    async def wing_belongs_to_tower(
        self, *, organization_id: str, tower_id: str, wing_id: str
    ) -> bool:
        """Return True when the wing belongs to the tower."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1 FROM tower_wings
            WHERE id = $1::uuid AND tower_id = $2::uuid AND organization_id = $3::uuid
            """,
            wing_id,
            tower_id,
            organization_id,
        )
        return row is not None

    # -- gates --------------------------------------------------------------

    async def insert_gate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a tower gate."""
        operating_hours = data.get("operating_hours")
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tower_gates (
                organization_id, tower_id, wing_id, name, gate_type, status,
                is_open_24x7, operating_hours, sort_order
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5::gate_type, $6::gate_status,
                $7, $8::jsonb, $9
            )
            RETURNING *
            """,
            data["organization_id"],
            data["tower_id"],
            data.get("wing_id"),
            data["name"],
            data.get("gate_type", "both"),
            data.get("status", "active"),
            data.get("is_open_24x7", False),
            json.dumps(operating_hours) if operating_hours is not None else None,
            data.get("sort_order", 0),
        )
        return dict(row)

    async def list_gates(self, *, organization_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List gates for a tower."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM tower_gates
            WHERE organization_id = $1::uuid AND tower_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            tower_id,
        )
        return [dict(row) for row in rows]

    async def get_gate_by_id(self, *, organization_id: str, gate_id: str) -> dict[str, Any] | None:
        """Fetch a tower gate scoped to the organization."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM tower_gates
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            """,
            organization_id,
            gate_id,
        )
        return dict(row) if row else None

    async def delete_gate(self, *, organization_id: str, tower_id: str, gate_id: str) -> bool:
        """Delete a gate."""
        result = await self.db_connection.execute(
            """
            DELETE FROM tower_gates
            WHERE id = $1::uuid AND tower_id = $2::uuid AND organization_id = $3::uuid
            """,
            gate_id,
            tower_id,
            organization_id,
        )
        return result.upper().endswith("1")

    # -- lifts --------------------------------------------------------------

    async def insert_lift(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a tower lift."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO tower_lifts (
                organization_id, tower_id, name, lift_type, capacity_persons,
                brand, status, serves_floors, sort_order
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4::lift_type, $5,
                $6, $7::lift_status, $8::integer[], $9
            )
            RETURNING *
            """,
            data["organization_id"],
            data["tower_id"],
            data["name"],
            data.get("lift_type", "passenger"),
            data.get("capacity_persons"),
            data.get("brand"),
            data.get("status", "operational"),
            list(data.get("serves_floors") or []),
            data.get("sort_order", 0),
        )
        return dict(row)

    async def list_lifts(self, *, organization_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List lifts for a tower."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM tower_lifts
            WHERE organization_id = $1::uuid AND tower_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            tower_id,
        )
        return [dict(row) for row in rows]

    async def delete_lift(self, *, organization_id: str, tower_id: str, lift_id: str) -> bool:
        """Delete a lift."""
        result = await self.db_connection.execute(
            """
            DELETE FROM tower_lifts
            WHERE id = $1::uuid AND tower_id = $2::uuid AND organization_id = $3::uuid
            """,
            lift_id,
            tower_id,
            organization_id,
        )
        return result.upper().endswith("1")

    # -- floors -------------------------------------------------------------

    async def insert_floor(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a floor."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO floors (
                organization_id, tower_id, wing_id, level_number,
                display_name, sort_order, is_parking
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7)
            RETURNING *
            """,
            data["organization_id"],
            data["tower_id"],
            data.get("wing_id"),
            data["level_number"],
            data["display_name"],
            data.get("sort_order", 0),
            data.get("is_parking", False),
        )
        return dict(row)

    async def list_floors(self, *, organization_id: str, tower_id: str) -> list[dict[str, Any]]:
        """List floors for a tower."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM floors
            WHERE organization_id = $1::uuid AND tower_id = $2::uuid
            ORDER BY sort_order, level_number
            """,
            organization_id,
            tower_id,
        )
        return [dict(row) for row in rows]

    async def delete_floor(self, *, organization_id: str, tower_id: str, floor_id: str) -> bool:
        """Delete a floor."""
        result = await self.db_connection.execute(
            """
            DELETE FROM floors
            WHERE id = $1::uuid AND tower_id = $2::uuid AND organization_id = $3::uuid
            """,
            floor_id,
            tower_id,
            organization_id,
        )
        return result.upper().endswith("1")
