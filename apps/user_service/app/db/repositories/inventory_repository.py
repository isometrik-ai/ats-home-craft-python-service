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

    async def list_summary_towers(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List towers for the inventory summary."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM towers
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def list_summary_units(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List units with config_kind and tower_type for inventory aggregation."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                u.id,
                u.code,
                u.tower_id,
                u.floor_id,
                u.config_id,
                u.status,
                u.sort_order,
                u.is_parking,
                u.plot_item_id,
                uc.config_kind,
                t.tower_type
            FROM units u
            LEFT JOIN unit_configs uc
                ON uc.id = u.config_id
               AND uc.organization_id = u.organization_id
            LEFT JOIN towers t
                ON t.id = u.tower_id
               AND t.organization_id = u.organization_id
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND ($3::uuid IS NULL OR u.tower_id = $3::uuid)
              AND ($4::unit_status IS NULL OR u.status = $4::unit_status)
            ORDER BY u.sort_order, u.code
            """,
            organization_id,
            project_id,
            tower_id,
            status,
        )
        return [dict(row) for row in rows]

    async def list_summary_floors(
        self,
        *,
        organization_id: str,
        project_id: str,
        tower_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List floors for all towers or one tower in a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                f.id,
                f.tower_id,
                f.level_number,
                f.display_name,
                f.sort_order,
                f.is_parking
            FROM floors f
            JOIN towers t ON t.id = f.tower_id
            WHERE f.organization_id = $1::uuid
              AND t.project_id = $2::uuid
              AND ($3::uuid IS NULL OR f.tower_id = $3::uuid)
            ORDER BY f.tower_id, f.sort_order, f.level_number
            """,
            organization_id,
            project_id,
            tower_id,
        )
        return [dict(row) for row in rows]

    async def list_summary_plot_configs(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List plot unit configs for the inventory summary."""
        rows = await self.db_connection.fetch(
            """
            SELECT id, name, code, sort_order, active
            FROM unit_configs
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND config_kind = 'plot'::unit_config_kind
            ORDER BY sort_order, name
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def list_summary_plot_items(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List plot items with optional linked unit status."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                pci.id,
                pci.config_id,
                pci.plot_no,
                pci.size_sqft,
                pci.status,
                pci.is_corner,
                pci.sort_order,
                u.id AS unit_id,
                u.status AS unit_status
            FROM plot_config_items pci
            JOIN unit_configs uc
                ON uc.id = pci.config_id
               AND uc.organization_id = pci.organization_id
            LEFT JOIN units u
                ON u.plot_item_id = pci.id
               AND u.organization_id = pci.organization_id
            WHERE pci.organization_id = $1::uuid
              AND uc.project_id = $2::uuid
              AND uc.config_kind = 'plot'::unit_config_kind
            ORDER BY pci.config_id, pci.sort_order, pci.plot_no
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]
