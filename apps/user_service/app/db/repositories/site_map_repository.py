"""Site map overlays persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class SiteMapRepository(BaseRepository):
    """Database operations for public.site_map_overlays."""

    async def insert_overlays(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert multiple site map overlay markers in one statement."""
        return await self.bulk_insert_returning(
            table="site_map_overlays",
            required_columns=[
                "organization_id",
                "project_id",
                "entity_type",
                "entity_id",
                "latitude",
                "longitude",
            ],
            optional_columns=["label"],
            rows=rows,
        )

    async def list_overlays(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List overlays for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM site_map_overlays
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def delete_overlay(
        self, *, organization_id: str, project_id: str, overlay_id: str
    ) -> bool:
        """Delete an overlay."""
        result = await self.db_connection.execute(
            """
            DELETE FROM site_map_overlays
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            overlay_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")
