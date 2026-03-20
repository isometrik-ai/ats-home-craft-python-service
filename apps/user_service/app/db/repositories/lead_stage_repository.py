"""Lead Stage Database Repository Module - AsyncPG Implementation.

This module contains lead stage-related database operations using asyncpg.
"""

from typing import Any

import asyncpg


class LeadStageRepository:
    """Database operations class for lead stage management using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection."""
        self.db_connection = db_connection

    async def count_stages(self, organization_id: str) -> int:
        """Return total active stages for the organization."""
        query = """
            SELECT COUNT(*)::int
            FROM lead_stages
            WHERE organization_id = $1
        """
        return await self.db_connection.fetchval(query, organization_id)

    async def get_max_sort_order(self, organization_id: str) -> int:
        """Return current maximum sort_order for the organization."""
        query = """
            SELECT COALESCE(MAX(sort_order), 0)::int
            FROM lead_stages
            WHERE organization_id = $1
        """
        return await self.db_connection.fetchval(query, organization_id)

    async def check_stage_key_exists(self, organization_id: str, stage_key: str) -> bool:
        """Check if stage_key already exists in organization."""
        query = """
            SELECT EXISTS(
                SELECT 1
                FROM lead_stages
                WHERE organization_id = $1
                  AND stage_key = $2
            )
        """
        return bool(await self.db_connection.fetchval(query, organization_id, stage_key))

    async def shift_sort_orders_for_insert(
        self, organization_id: str, target_position: int
    ) -> None:
        """Shift stages at and after target_position up by one."""
        query = """
            UPDATE lead_stages
            SET sort_order = sort_order + 1
            WHERE organization_id = $1
              AND sort_order >= $2
        """
        await self.db_connection.execute(query, organization_id, target_position)

    async def create_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Create a lead stage row and return persisted record."""
        query = """
            INSERT INTO lead_stages (
                organization_id,
                stage_name,
                stage_key,
                description,
                color,
                sort_order,
                is_initial,
                is_final
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING
                id,
                stage_name,
                stage_key,
                description,
                color,
                sort_order,
                is_initial,
                is_final,
                created_at,
                updated_at
        """
        row = await self.db_connection.fetchrow(
            query,
            stage_data["organization_id"],
            stage_data["stage_name"],
            stage_data["stage_key"],
            stage_data.get("description"),
            stage_data.get("color"),
            stage_data["sort_order"],
            stage_data["is_initial"],
            stage_data["is_final"],
        )
        return dict(row)
