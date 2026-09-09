"""Repository for the project_permissions catalog (project-role assignable permissions)."""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from libs.shared_utils.common_query import DEFAULT_PROJECT_PERMISSIONS


class ProjectPermissionsRepository:
    """SQL access for project_permissions rows."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def get_all_permissions(self, organization_id: str) -> list[dict]:
        """Return the project permission catalog for an organization."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                id,
                name,
                code,
                category,
                description,
                created_at
            FROM project_permissions
            WHERE organization_id = $1::uuid
            ORDER BY category ASC, name ASC
            """,
            organization_id,
        )
        return [dict(row) for row in rows]

    async def get_permission_by_id(
        self, project_permission_id: str, organization_id: str
    ) -> dict | None:
        """Return one project permission row by id."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                id,
                name,
                code,
                category,
                description,
                created_at
            FROM project_permissions
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            """,
            project_permission_id,
            organization_id,
        )
        return dict(row) if row else None

    async def create_default_permissions(self, organization_id: str) -> list[str]:
        """Insert default project permission catalog rows for a new organization."""
        if not DEFAULT_PROJECT_PERMISSIONS:
            return []

        columns = ["organization_id", "code", "name", "description", "category", "created_at"]
        now = datetime.now(timezone.utc)
        values: list[object] = []
        placeholders: list[str] = []

        for idx, (code, name, description, category) in enumerate(DEFAULT_PROJECT_PERMISSIONS):
            base_idx = idx * len(columns)
            values.extend([organization_id, code, name, description, category, now])
            placeholders.append(
                f"({', '.join(f'${base_idx + i + 1}' for i in range(len(columns)))})"
            )

        query = f"""
            INSERT INTO project_permissions ({", ".join(columns)})
            VALUES {", ".join(placeholders)}
            ON CONFLICT (organization_id, code) DO NOTHING
            RETURNING id
        """
        rows = await self.db_connection.fetch(query, *values)
        return [str(row["id"]) for row in rows]
