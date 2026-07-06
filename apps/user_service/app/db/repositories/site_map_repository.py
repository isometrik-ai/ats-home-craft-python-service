"""Site map overlays persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class SiteMapRepository(BaseRepository):
    """Database operations for public.site_map_overlays."""

    async def media_belongs_to_project(
        self, *, organization_id: str, project_id: str, media_id: str
    ) -> bool:
        """Return True when the media id belongs to the project."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1 FROM project_media
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            media_id,
            project_id,
            organization_id,
        )
        return row is not None

    async def insert_overlay(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a site map overlay marker."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO site_map_overlays (
                organization_id, project_id, site_map_media_id, entity_type,
                entity_id, x_percent, y_percent, label
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5::uuid, $6, $7, $8
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["site_map_media_id"],
            data["entity_type"],
            data["entity_id"],
            data["x_percent"],
            data["y_percent"],
            data.get("label"),
        )
        return dict(row)

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
