"""Projects, project_media, and project_members persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

# Columns that require an explicit enum/array cast when written.
_PROJECT_COLUMN_CASTS: dict[str, str] = {
    "property_types": "::property_type[]",
    "primary_measurement_unit": "::measurement_unit",
    "status": "::project_status",
    "setup_current_step": "::project_setup_step",
}

_PROJECT_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "code",
    "name",
    "developer_name",
    "community_admin_user_id",
    "gstin",
    "possession_date",
    "address_line_1",
    "address_line_2",
    "pin_code",
    "city",
    "state",
    "country",
    "latitude",
    "longitude",
    "property_types",
    "primary_measurement_unit",
    "units_count",
    "created_by",
    "updated_by",
)


class ProjectsRepository(BaseRepository):
    """Database operations for public.projects and related media/members."""

    async def insert_project(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert one project row and return it."""
        present = [col for col in _PROJECT_INSERT_COLUMNS if col in data]
        col_sql = ", ".join(present)
        placeholders = ", ".join(
            f"${idx + 1}{_PROJECT_COLUMN_CASTS.get(col, '')}" for idx, col in enumerate(present)
        )
        values = [data.get(col) for col in present]
        row = await self.db_connection.fetchrow(
            f"""
            INSERT INTO projects ({col_sql})
            VALUES ({placeholders})
            RETURNING *
            """,
            *values,
        )
        return dict(row)

    async def get_project(self, *, organization_id: str, project_id: str) -> dict[str, Any] | None:
        """Fetch a single project scoped to the organization."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM projects
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            """,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    async def update_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch project columns present in update_data."""
        if not update_data:
            return await self.get_project(organization_id=organization_id, project_id=project_id)
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col in {"id", "organization_id", "created_at", "created_by"}:
                continue
            set_parts.append(f"{col} = ${idx}{_PROJECT_COLUMN_CASTS.get(col, '')}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([project_id, organization_id])
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE projects
            SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid
              AND organization_id = ${idx + 1}::uuid
            RETURNING *
            """,
            *values,
        )
        return dict(row) if row else None

    async def set_setup_current_step(
        self,
        *,
        organization_id: str,
        project_id: str,
        step_key: str,
    ) -> None:
        """Advance the wizard pointer stored on projects.setup_current_step."""
        await self.db_connection.execute(
            """
            UPDATE projects
            SET setup_current_step = $3::project_setup_step,
                updated_at = now()
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            """,
            project_id,
            organization_id,
            step_key,
        )

    async def set_status(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        """Set project status (e.g. onboarding -> active on wizard completion)."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE projects
            SET status = $3::project_status,
                updated_at = now()
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            RETURNING *
            """,
            project_id,
            organization_id,
            status,
        )
        return dict(row) if row else None

    async def recompute_units_count(self, *, organization_id: str, project_id: str) -> int:
        """Recompute projects.units_count from non-parking units."""
        count = await self.db_connection.fetchval(
            """
            UPDATE projects p
            SET units_count = sub.cnt,
                updated_at = now()
            FROM (
                SELECT COUNT(*)::int AS cnt
                FROM units
                WHERE project_id = $1::uuid
                  AND organization_id = $2::uuid
                  AND is_parking = false
            ) AS sub
            WHERE p.id = $1::uuid
              AND p.organization_id = $2::uuid
            RETURNING p.units_count
            """,
            project_id,
            organization_id,
        )
        return int(count or 0)

    async def delete_project(self, *, organization_id: str, project_id: str) -> bool:
        """Hard-delete a project (children cascade)."""
        result = await self.db_connection.execute(
            """
            DELETE FROM projects
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            """,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")

    async def list_projects(
        self,
        *,
        organization_id: str,
        search: str | None,
        status: str | None,
        property_type: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List projects with search and pagination."""
        offset = (page - 1) * page_size
        args: list[Any] = [organization_id]
        where = ["p.organization_id = $1::uuid"]
        next_param = 2

        if status:
            where.append(f"p.status = ${next_param}::project_status")
            args.append(status)
            next_param += 1

        if property_type:
            where.append(f"${next_param}::property_type = ANY(p.property_types)")
            args.append(property_type)
            next_param += 1

        if search:
            where.append(
                f"(p.name ILIKE ${next_param} OR p.code ILIKE ${next_param}"
                f" OR p.developer_name ILIKE ${next_param})"
            )
            args.append(f"%{search.strip()}%")
            next_param += 1

        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"SELECT COUNT(1) FROM projects p WHERE {where_sql}",
            *args,
        )
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              p.id::text AS id,
              p.organization_id::text AS organization_id,
              p.code,
              p.name,
              p.developer_name,
              p.city,
              p.state,
              p.status::text AS status,
              p.property_types,
              p.primary_measurement_unit::text AS primary_measurement_unit,
              p.units_count,
              p.setup_current_step::text AS setup_current_step,
              p.created_at,
              p.updated_at
            FROM projects p
            WHERE {where_sql}
            ORDER BY p.created_at DESC
            OFFSET ${next_param} LIMIT ${next_param + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(row) for row in rows], int(total or 0)

    async def list_projects_for_member(
        self,
        *,
        organization_id: str,
        user_id: str,
        search: str | None,
        status: str | None,
        property_type: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List projects assigned to a user via project_members."""
        offset = (page - 1) * page_size
        args: list[Any] = [organization_id, user_id]
        where = [
            "p.organization_id = $1::uuid",
            "pm.user_id = $2::uuid",
            "pm.status = 'active'",
        ]
        next_param = 3

        if status:
            where.append(f"p.status = ${next_param}::project_status")
            args.append(status)
            next_param += 1

        if property_type:
            where.append(f"${next_param}::property_type = ANY(p.property_types)")
            args.append(property_type)
            next_param += 1

        if search:
            where.append(
                f"(p.name ILIKE ${next_param} OR p.code ILIKE ${next_param}"
                f" OR p.developer_name ILIKE ${next_param})"
            )
            args.append(f"%{search.strip()}%")
            next_param += 1

        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(1)
            FROM projects p
            INNER JOIN project_members pm
              ON pm.project_id = p.id
             AND pm.organization_id = p.organization_id
            WHERE {where_sql}
            """,
            *args,
        )
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              p.id::text AS id,
              p.organization_id::text AS organization_id,
              p.code,
              p.name,
              p.developer_name,
              p.city,
              p.state,
              p.status::text AS status,
              p.property_types,
              p.primary_measurement_unit::text AS primary_measurement_unit,
              p.units_count,
              p.setup_current_step::text AS setup_current_step,
              p.created_at,
              p.updated_at,
              pm.role
            FROM projects p
            INNER JOIN project_members pm
              ON pm.project_id = p.id
             AND pm.organization_id = p.organization_id
            WHERE {where_sql}
            ORDER BY p.created_at DESC
            OFFSET ${next_param} LIMIT ${next_param + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(row) for row in rows], int(total or 0)

    # -- project media ------------------------------------------------------

    async def insert_media(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert one project_media row (stores payload metadata as-is)."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_media (
                organization_id, project_id, kind, path,
                mime, size_bytes, original_name, sort_order, uploaded_by
            )
            VALUES (
                $1::uuid, $2::uuid, $3::project_media_kind, $4,
                $5, $6, $7, $8, $9::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["kind"],
            data["path"],
            data["mime"],
            data["size_bytes"],
            data.get("original_name"),
            data.get("sort_order", 0),
            data.get("uploaded_by"),
        )
        return dict(row)

    async def list_media(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List media for a project ordered by sort_order."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM project_media
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def delete_media(self, *, organization_id: str, project_id: str, media_id: str) -> bool:
        """Delete a single media row."""
        result = await self.db_connection.execute(
            """
            DELETE FROM project_media
            WHERE id = $1::uuid
              AND project_id = $2::uuid
              AND organization_id = $3::uuid
            """,
            media_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")

    async def get_media(
        self, *, organization_id: str, project_id: str, media_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single media row scoped to project + org."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM project_media
            WHERE id = $1::uuid
              AND project_id = $2::uuid
              AND organization_id = $3::uuid
            """,
            media_id,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    # -- project members ----------------------------------------------------

    async def upsert_member(
        self,
        *,
        organization_id: str,
        project_id: str,
        user_id: str,
        role: str = "community_admin",
    ) -> dict[str, Any]:
        """Add (or keep) a project member."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_members (organization_id, project_id, user_id, role)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
            ON CONFLICT (project_id, user_id) DO UPDATE
              SET role = EXCLUDED.role,
                  updated_at = now()
            RETURNING *
            """,
            organization_id,
            project_id,
            user_id,
            role,
        )
        return dict(row)

    async def list_members(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """List members for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM project_members
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            ORDER BY created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]
