"""Base repository helpers for work_order schema."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.work_order_service.app.utils.records import record_to_dict


class ScopedRepository:
    """Base class with org/project scoping."""

    table: str

    def __init__(self, conn: asyncpg.Connection) -> None:
        """init  ."""
        self.conn = conn

    async def get_by_id(
        self,
        *,
        entity_id: str,
        organization_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Get by id."""
        row = await self.conn.fetchrow(
            f"""
            SELECT * FROM work_order.{self.table}
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND project_id = $3::uuid
              AND record_status = 'active'
            """,
            entity_id,
            organization_id,
            project_id,
        )
        return record_to_dict(row) if row else None

    async def soft_delete(
        self,
        *,
        entity_id: str,
        organization_id: str,
        project_id: str,
    ) -> bool:
        """Soft delete."""
        result = await self.conn.execute(
            f"""
            UPDATE work_order.{self.table}
            SET record_status = 'deleted',
                deleted_at = now(),
                updated_at = now()
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND project_id = $3::uuid
              AND record_status = 'active'
            """,
            entity_id,
            organization_id,
            project_id,
        )
        return result.endswith("1")
