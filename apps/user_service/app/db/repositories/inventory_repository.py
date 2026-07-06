"""Floor inventory matrix persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class InventoryRepository(BaseRepository):
    """Database operations for public.floor_inventory."""

    async def references_valid(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_ids: list[str],
        floor_ids: list[str],
        config_ids: list[str],
    ) -> bool:
        """Verify every referenced tower/floor/config belongs to the project."""
        towers_ok = await self.db_connection.fetchval(
            """
            SELECT COUNT(DISTINCT id) = cardinality($3::uuid[])
            FROM towers
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND id = ANY($3::uuid[])
            """,
            organization_id,
            project_id,
            list(set(tower_ids)),
        )
        floors_ok = await self.db_connection.fetchval(
            """
            SELECT COUNT(DISTINCT f.id) = cardinality($3::uuid[])
            FROM floors f
            JOIN towers t ON t.id = f.tower_id
            WHERE f.organization_id = $1::uuid AND t.project_id = $2::uuid
              AND f.id = ANY($3::uuid[])
            """,
            organization_id,
            project_id,
            list(set(floor_ids)),
        )
        configs_ok = await self.db_connection.fetchval(
            """
            SELECT COUNT(DISTINCT id) = cardinality($3::uuid[])
            FROM unit_configs
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND id = ANY($3::uuid[])
            """,
            organization_id,
            project_id,
            list(set(config_ids)),
        )
        return bool(towers_ok) and bool(floors_ok) and bool(configs_ok)

    async def upsert_items(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_ids: list[str],
        floor_ids: list[str],
        config_ids: list[str],
        quantities: list[int],
    ) -> list[dict[str, Any]]:
        """Upsert the floor inventory cells and return the affected rows."""
        rows = await self.db_connection.fetch(
            """
            INSERT INTO floor_inventory (
                organization_id, project_id, tower_id, floor_id, config_id, quantity
            )
            SELECT $1::uuid, $2::uuid, t.tower_id, t.floor_id, t.config_id, t.quantity
            FROM unnest($3::uuid[], $4::uuid[], $5::uuid[], $6::int[])
                AS t(tower_id, floor_id, config_id, quantity)
            ON CONFLICT (organization_id, floor_id, config_id) DO UPDATE
              SET quantity = EXCLUDED.quantity,
                  tower_id = EXCLUDED.tower_id,
                  updated_at = now()
            RETURNING *
            """,
            organization_id,
            project_id,
            tower_ids,
            floor_ids,
            config_ids,
            quantities,
        )
        return [dict(row) for row in rows]

    async def list_inventory(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List all inventory cells for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM floor_inventory
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY tower_id, floor_id, config_id
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]
