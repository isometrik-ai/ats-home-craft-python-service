"""Daily help category persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_CATEGORY_SELECT_COLUMNS = """
  c.id::text AS id,
  c.organization_id::text AS organization_id,
  c.project_id::text AS project_id,
  c.name,
  c.sort_order,
  c.status::text AS status,
  c.created_by_user_id::text AS created_by_user_id,
  c.updated_by_user_id::text AS updated_by_user_id,
  c.created_at,
  c.updated_at
"""


class DailyHelpCategoriesRepository(BaseRepository):
    """Database operations for daily_help_categories."""

    async def list_by_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List categories for a project ordered by sort_order then name."""
        filters = ["c.organization_id = $1::uuid", "c.project_id = $2::uuid"]
        args: list[Any] = [organization_id, project_id]
        if status:
            args.append(status)
            filters.append(f"c.status = ${len(args)}::daily_help_category_status")
        where_sql = " AND ".join(filters)
        rows = await self.db_connection.fetch(
            f"""
            SELECT
            {_CATEGORY_SELECT_COLUMNS}
            FROM daily_help_categories c
            WHERE {where_sql}
            ORDER BY c.sort_order ASC, lower(c.name) ASC
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def get_by_id(
        self,
        *,
        organization_id: str,
        project_id: str,
        category_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one category row scoped to organization and project."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
            {_CATEGORY_SELECT_COLUMNS}
            FROM daily_help_categories c
            WHERE c.organization_id = $1::uuid
              AND c.project_id = $2::uuid
              AND c.id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            project_id,
            category_id,
        )
        return dict(row) if row else None

    async def insert(
        self,
        *,
        organization_id: str,
        project_id: str,
        name: str,
        sort_order: int,
        status: str,
        created_by_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a daily_help_categories row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO daily_help_categories (
                organization_id,
                project_id,
                name,
                sort_order,
                status,
                created_by_user_id,
                updated_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4,
                $5::daily_help_category_status, $6::uuid, $6::uuid
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            name.strip(),
            sort_order,
            status,
            created_by_user_id,
        )
        return await self.get_by_id(
            organization_id=organization_id,
            project_id=project_id,
            category_id=str(row["id"]),
        )

    async def update(
        self,
        *,
        organization_id: str,
        project_id: str,
        category_id: str,
        fields: dict[str, Any],
        updated_by_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch category fields."""
        if not fields and updated_by_user_id is None:
            return await self.get_by_id(
                organization_id=organization_id,
                project_id=project_id,
                category_id=category_id,
            )

        set_parts: list[str] = []
        values: list[Any] = [organization_id, project_id, category_id]
        idx = 4
        casts = {"status": "::daily_help_category_status"}
        for key, value in fields.items():
            cast = casts.get(key, "")
            set_parts.append(f"{key} = ${idx}{cast}")
            values.append(value)
            idx += 1
        if updated_by_user_id is not None:
            set_parts.append(f"updated_by_user_id = ${idx}::uuid")
            values.append(updated_by_user_id)
            idx += 1
        set_parts.append("updated_at = now()")

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE daily_help_categories c
            SET {", ".join(set_parts)}
            WHERE c.organization_id = $1::uuid
              AND c.project_id = $2::uuid
              AND c.id = $3::uuid
            RETURNING
            {_CATEGORY_SELECT_COLUMNS}
            """,
            *values,
        )
        return dict(row) if row else None

    async def count_profiles_using_category(
        self,
        *,
        organization_id: str,
        project_id: str,
        category_id: str,
    ) -> int:
        """Count profiles referencing a category (any status except deleted)."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM daily_help_profiles p
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
              AND p.category_id = $3::uuid
              AND p.status <> 'deleted'::daily_help_status
            """,
            organization_id,
            project_id,
            category_id,
        )
        return int(count or 0)
