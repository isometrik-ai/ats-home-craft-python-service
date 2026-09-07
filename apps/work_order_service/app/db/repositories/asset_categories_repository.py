"""Asset category repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


class AssetCategoryRepository(ScopedRepository):
    """Database access for asset category."""

    table = "asset_categories"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        search_clause = ""
        if search:
            params.append(f"%{search}%")
            search_clause = f" AND name ILIKE ${len(params)}"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.asset_categories
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{search_clause}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.asset_categories
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{search_clause}
            ORDER BY name
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.asset_categories (
                organization_id, project_id, name, description, parent_id
            ) VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid)
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["name"],
            data.get("description") or "",
            data.get("parent_id"),
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.asset_categories
            SET name = COALESCE($4, name),
                description = COALESCE($5, description),
                parent_id = COALESCE($6::uuid, parent_id),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("name"),
            data.get("description"),
            data.get("parent_id"),
        )
        return record_to_dict(row) if row else None
