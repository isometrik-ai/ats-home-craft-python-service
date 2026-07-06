"""Unit configs, plot config items, and config media persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_CONFIG_COLUMN_CASTS: dict[str, str] = {
    "config_kind": "::unit_config_kind",
    "default_facing": "::facing",
    "commercial_unit_type": "::commercial_unit_type",
    "plot_type": "::plot_type",
    "facing": "::facing",
}

_CONFIG_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "project_id",
    "config_kind",
    "name",
    "code",
    "display_label",
    "active",
    "sort_order",
    "bedrooms",
    "bathrooms",
    "area_sqft",
    "parking_entitlement",
    "balconies",
    "default_facing",
    "view",
    "commercial_unit_type",
    "carpet_area_sqft",
    "dimensions_ft",
    "height_ft",
    "power_load_kw",
    "has_mezzanine",
    "mezzanine_area_sqft",
    "plot_type",
    "facing",
    "latitude",
    "longitude",
)


class UnitConfigsRepository(BaseRepository):
    """Database operations for unit_configs and child tables."""

    async def insert_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a unit config row."""
        present = [col for col in _CONFIG_INSERT_COLUMNS if col in data]
        col_sql = ", ".join(present)
        placeholders = ", ".join(
            f"${idx + 1}{_CONFIG_COLUMN_CASTS.get(col, '')}" for idx, col in enumerate(present)
        )
        row = await self.db_connection.fetchrow(
            f"INSERT INTO unit_configs ({col_sql}) VALUES ({placeholders}) RETURNING *",
            *[data.get(col) for col in present],
        )
        return dict(row)

    async def get_config(
        self, *, organization_id: str, project_id: str, config_id: str
    ) -> dict[str, Any] | None:
        """Fetch a config scoped to org + project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT * FROM unit_configs
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            config_id,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    async def list_configs(
        self,
        *,
        organization_id: str,
        project_id: str,
        config_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """List configs for a project, optionally filtered by kind."""
        if config_kind:
            rows = await self.db_connection.fetch(
                """
                SELECT * FROM unit_configs
                WHERE organization_id = $1::uuid AND project_id = $2::uuid
                  AND config_kind = $3::unit_config_kind
                ORDER BY sort_order, created_at
                """,
                organization_id,
                project_id,
                config_kind,
            )
        else:
            rows = await self.db_connection.fetch(
                """
                SELECT * FROM unit_configs
                WHERE organization_id = $1::uuid AND project_id = $2::uuid
                ORDER BY sort_order, created_at
                """,
                organization_id,
                project_id,
            )
        return [dict(row) for row in rows]

    async def update_config(
        self,
        *,
        organization_id: str,
        project_id: str,
        config_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch a unit config."""
        if not update_data:
            return await self.get_config(
                organization_id=organization_id,
                project_id=project_id,
                config_id=config_id,
            )
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            set_parts.append(f"{col} = ${idx}{_CONFIG_COLUMN_CASTS.get(col, '')}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([config_id, project_id, organization_id])
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE unit_configs SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid
              AND organization_id = ${idx + 2}::uuid
            RETURNING *
            """,
            *values,
        )
        return dict(row) if row else None

    async def delete_config(self, *, organization_id: str, project_id: str, config_id: str) -> bool:
        """Delete a unit config (children cascade)."""
        result = await self.db_connection.execute(
            """
            DELETE FROM unit_configs
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            config_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")

    # -- plot config items --------------------------------------------------

    async def insert_plot_item(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a plot config item."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO plot_config_items (
                organization_id, config_id, plot_no, size_sqft, status,
                is_corner, sort_order
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5::plot_item_status, $6, $7)
            RETURNING *
            """,
            data["organization_id"],
            data["config_id"],
            data["plot_no"],
            data["size_sqft"],
            data.get("status", "empty"),
            data.get("is_corner", False),
            data.get("sort_order", 0),
        )
        return dict(row)

    async def list_plot_items(
        self, *, organization_id: str, config_id: str
    ) -> list[dict[str, Any]]:
        """List plot items for a config."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM plot_config_items
            WHERE organization_id = $1::uuid AND config_id = $2::uuid
            ORDER BY sort_order, plot_no
            """,
            organization_id,
            config_id,
        )
        return [dict(row) for row in rows]

    async def delete_plot_item(self, *, organization_id: str, config_id: str, item_id: str) -> bool:
        """Delete a plot item."""
        result = await self.db_connection.execute(
            """
            DELETE FROM plot_config_items
            WHERE id = $1::uuid AND config_id = $2::uuid AND organization_id = $3::uuid
            """,
            item_id,
            config_id,
            organization_id,
        )
        return result.upper().endswith("1")

    # -- config media -------------------------------------------------------

    async def insert_media(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert config media metadata as provided."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO config_media (
                organization_id, config_id, kind, path,
                mime, size_bytes, original_name, sort_order, uploaded_by
            )
            VALUES (
                $1::uuid, $2::uuid, $3::config_media_kind, $4,
                $5, $6, $7, $8, $9::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["config_id"],
            data["kind"],
            data["path"],
            data["mime"],
            data["size_bytes"],
            data.get("original_name"),
            data.get("sort_order", 0),
            data.get("uploaded_by"),
        )
        return dict(row)

    async def list_media(self, *, organization_id: str, config_id: str) -> list[dict[str, Any]]:
        """List media for a config."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM config_media
            WHERE organization_id = $1::uuid AND config_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            config_id,
        )
        return [dict(row) for row in rows]

    async def delete_media(self, *, organization_id: str, config_id: str, media_id: str) -> bool:
        """Delete config media."""
        result = await self.db_connection.execute(
            """
            DELETE FROM config_media
            WHERE id = $1::uuid AND config_id = $2::uuid AND organization_id = $3::uuid
            """,
            media_id,
            config_id,
            organization_id,
        )
        return result.upper().endswith("1")
