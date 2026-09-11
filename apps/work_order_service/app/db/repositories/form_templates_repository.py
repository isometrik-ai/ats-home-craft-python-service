"""Form templates repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


class FormTemplatesRepository(ScopedRepository):
    """Database access for form templates."""

    table = "form_templates"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        total = await self.conn.fetchval(
            """
            SELECT COUNT(*) FROM work_order.form_templates
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'
            """,
            organization_id,
            project_id,
        )
        rows = await self.conn.fetch(
            """
            SELECT * FROM work_order.form_templates
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'
            ORDER BY name
            LIMIT $3 OFFSET $4
            """,
            organization_id,
            project_id,
            page_size,
            offset,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.form_templates (
                organization_id, project_id, name, description, schema
            ) VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["name"],
            data.get("description") or "",
            data.get("schema") or {},
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        schema_json = data["schema"] if "schema" in data else None
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.form_templates
            SET name = COALESCE($4, name),
                description = COALESCE($5, description),
                schema = COALESCE($6::jsonb, schema),
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
            schema_json,
        )
        return record_to_dict(row) if row else None
