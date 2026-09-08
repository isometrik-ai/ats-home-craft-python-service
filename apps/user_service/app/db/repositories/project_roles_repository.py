"""Repository for per-project role templates and permissions."""

from __future__ import annotations

from typing import Any

import asyncpg

from libs.shared_utils.project_role_defaults import (
    DEFAULT_PROJECT_ROLE_DEFINITIONS,
    DEFAULT_PROJECT_ROLE_PERMISSIONS,
)


class ProjectRolesRepository:
    """SQL access for project_roles and project_role_permissions."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def list_roles_for_project(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Return all role templates for a project ordered by slug."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              slug,
              name,
              description,
              is_system,
              created_at,
              updated_at
            FROM project_roles
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            ORDER BY slug
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def get_role_by_slug(
        self,
        *,
        organization_id: str,
        project_id: str,
        slug: str,
    ) -> dict[str, Any] | None:
        """Return one project role row by slug."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              slug,
              name,
              description,
              is_system,
              created_at,
              updated_at
            FROM project_roles
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND slug = $3
            """,
            organization_id,
            project_id,
            slug,
        )
        return dict(row) if row else None

    async def get_role_by_id(
        self,
        *,
        organization_id: str,
        project_id: str,
        project_role_id: str,
    ) -> dict[str, Any] | None:
        """Return one project role row by id scoped to project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              slug,
              name,
              description,
              is_system,
              created_at,
              updated_at
            FROM project_roles
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            """,
            organization_id,
            project_id,
            project_role_id,
        )
        return dict(row) if row else None

    async def seed_default_roles_for_project(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, str]:
        """Insert default system roles and permissions; return slug -> role_id map."""
        slug_to_id: dict[str, str] = {}
        for role_def in DEFAULT_PROJECT_ROLE_DEFINITIONS:
            row = await self.db_connection.fetchrow(
                """
                INSERT INTO project_roles (
                    organization_id,
                    project_id,
                    slug,
                    name,
                    description,
                    is_system
                )
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, true)
                ON CONFLICT (project_id, slug) DO UPDATE
                  SET name = EXCLUDED.name,
                      description = EXCLUDED.description,
                      updated_at = now()
                RETURNING id::text AS id, slug
                """,
                organization_id,
                project_id,
                role_def.slug,
                role_def.name,
                role_def.description,
            )
            assert row is not None
            slug_to_id[str(row["slug"])] = str(row["id"])

        permission_codes = {
            code for codes in DEFAULT_PROJECT_ROLE_PERMISSIONS.values() for code in codes
        }
        perm_rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id, code
            FROM permissions
            WHERE organization_id = $1::uuid
              AND code = ANY($2::text[])
            """,
            organization_id,
            list(permission_codes),
        )
        code_to_perm_id = {str(row["code"]): str(row["id"]) for row in perm_rows}

        for slug, codes in DEFAULT_PROJECT_ROLE_PERMISSIONS.items():
            role_id = slug_to_id.get(slug)
            if not role_id:
                continue
            for code in codes:
                permission_id = code_to_perm_id.get(code)
                if not permission_id:
                    continue
                await self.db_connection.execute(
                    """
                    INSERT INTO project_role_permissions (
                        organization_id,
                        project_role_id,
                        permission_id
                    )
                    VALUES ($1::uuid, $2::uuid, $3::uuid)
                    ON CONFLICT (project_role_id, permission_id) DO NOTHING
                    """,
                    organization_id,
                    role_id,
                    permission_id,
                )

        return slug_to_id

    async def get_permission_codes_for_role(self, *, project_role_id: str) -> set[str]:
        """Return permission codes granted by a project role."""
        rows = await self.db_connection.fetch(
            """
            SELECT p.code
            FROM project_role_permissions prp
            INNER JOIN permissions p ON p.id = prp.permission_id
            WHERE prp.project_role_id = $1::uuid
            """,
            project_role_id,
        )
        return {str(row["code"]) for row in rows}

    async def get_permissions_for_role(
        self,
        *,
        organization_id: str,
        project_role_id: str,
    ) -> list[dict[str, Any]]:
        """Return permission rows assigned to a project role."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              p.id::text AS id,
              p.code,
              p.name,
              p.category,
              p.description,
              p.created_at
            FROM project_role_permissions prp
            INNER JOIN permissions p
              ON p.id = prp.permission_id
             AND p.organization_id = prp.organization_id
            WHERE prp.organization_id = $1::uuid
              AND prp.project_role_id = $2::uuid
            ORDER BY p.category, p.code
            """,
            organization_id,
            project_role_id,
        )
        return [dict(row) for row in rows]

    async def replace_role_permissions(
        self,
        *,
        organization_id: str,
        project_role_id: str,
        permission_ids: list[str],
    ) -> None:
        """Replace all permissions on a project role."""
        await self.db_connection.execute(
            """
            DELETE FROM project_role_permissions
            WHERE organization_id = $1::uuid
              AND project_role_id = $2::uuid
            """,
            organization_id,
            project_role_id,
        )
        if not permission_ids:
            return
        await self.db_connection.executemany(
            """
            INSERT INTO project_role_permissions (
                organization_id,
                project_role_id,
                permission_id
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT (project_role_id, permission_id) DO NOTHING
            """,
            [(organization_id, project_role_id, permission_id) for permission_id in permission_ids],
        )

    async def count_members_with_role(self, *, project_role_id: str) -> int:
        """Count project member rows referencing a role."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(1)
            FROM project_members
            WHERE project_role_id = $1::uuid
              AND status = 'active'
            """,
            project_role_id,
        )
        return int(count or 0)

    async def update_role_metadata(
        self,
        *,
        organization_id: str,
        project_id: str,
        project_role_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch display fields on a project role."""
        sets: list[str] = ["updated_at = now()"]
        args: list[Any] = [organization_id, project_id, project_role_id]
        next_param = 4
        if name is not None:
            sets.append(f"name = ${next_param}")
            args.append(name)
            next_param += 1
        if description is not None:
            sets.append(f"description = ${next_param}")
            args.append(description)
            next_param += 1
        if len(sets) == 1:
            return await self.get_role_by_id(
                organization_id=organization_id,
                project_id=project_id,
                project_role_id=project_role_id,
            )
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE project_roles
            SET {", ".join(sets)}
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            RETURNING
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              slug,
              name,
              description,
              is_system,
              created_at,
              updated_at
            """,
            *args,
        )
        return dict(row) if row else None

    async def create_role(
        self,
        *,
        organization_id: str,
        project_id: str,
        slug: str,
        name: str,
        description: str | None,
        is_system: bool = False,
    ) -> dict[str, Any]:
        """Insert a project role template row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_roles (
                organization_id,
                project_id,
                slug,
                name,
                description,
                is_system
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
            RETURNING
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              slug,
              name,
              description,
              is_system,
              created_at,
              updated_at
            """,
            organization_id,
            project_id,
            slug,
            name,
            description,
            is_system,
        )
        assert row is not None
        return dict(row)

    async def delete_role(
        self,
        *,
        organization_id: str,
        project_id: str,
        project_role_id: str,
    ) -> bool:
        """Delete a custom (non-system) project role. Returns True when a row was removed."""
        result = await self.db_connection.execute(
            """
            DELETE FROM project_roles
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
              AND is_system = false
            """,
            organization_id,
            project_id,
            project_role_id,
        )
        return str(result).endswith("1")
