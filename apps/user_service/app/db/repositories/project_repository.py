"""Project Database Repository Module - AsyncPG Implementation

This module contains all project-related database operations using asyncpg.
All SQL queries for project management are centralized here with proper
transaction handling.
"""

import json
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ClientUserStatus,
    TeamRoles,
)
from apps.user_service.app.schemas.projects import ProjectListQueryParams

# JSONB columns in projects table
PROJECT_JSONB_COLUMNS = frozenset({"billing_info", "tech_stack", "custom_fields"})


class ProjectRepository:
    """Database operations class for project management using asyncpg.

    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    @staticmethod
    def _serialize_jsonb_param(key: str, value: Any) -> Any:
        """Serialize JSONB column values to JSON string for asyncpg; pass others through."""
        if key in PROJECT_JSONB_COLUMNS and isinstance(value, (list, dict)):
            return json.dumps(value)
        return value

    async def create_project(self, project_data: dict) -> dict:
        """Create a new project record.

        Args:
            project_data: Dictionary containing project fields

        Returns:
            dict: Created project record
        """
        # Serialize JSONB fields
        for key in PROJECT_JSONB_COLUMNS:
            if key in project_data and project_data[key] is not None:
                project_data[key] = self._serialize_jsonb_param(key, project_data[key])

        # Build dynamic query
        fields = []
        placeholders = []
        values = []
        param_index = 1

        # Required fields
        required_fields = [
            "organization_id",
            "project_id",
            "project_title",
            "client_id",
            "status",
        ]
        for field in required_fields:
            if field not in project_data:
                raise ValueError(f"Required field {field} is missing")
            fields.append(field)
            placeholders.append(f"${param_index}")
            values.append(project_data[field])
            param_index += 1

        # Optional fields
        optional_fields = [
            "project_description",
            "priority",
            "project_category",
            "practice_areas",
            "team_id",
            "start_date",
            "target_end_date",
            "actual_end_date",
            "billing_info",
            "tech_stack",
            "project_goals",
            "success_criteria",
            "additional_ai_context",
            "primary_pm_tool",
            "primary_repo_url",
            "tags",
            "custom_fields",
            "is_billable",
            "is_internal",
            "created_by",
            "updated_by",
        ]

        for field in optional_fields:
            if field in project_data and project_data[field] is not None:
                fields.append(field)
                placeholders.append(f"${param_index}")
                values.append(project_data[field])
                param_index += 1

        query = f"""
            INSERT INTO projects ({", ".join(fields)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)

    async def create_project_repositories(
        self,
        project_id: str,
        organization_id: str,
        repositories: list[dict[str, Any]],
        created_by: str,
    ) -> None:
        """Batch insert project repositories.

        Args:
            project_id: Project UUID
            organization_id: Organization UUID
            repositories: List of repository dictionaries
            created_by: User ID creating the repositories
        """
        if not repositories:
            return

        # Prepare data for batch insert
        platforms = []
        repository_names = []
        repository_owners = []
        repository_urls = []
        purposes = []
        primary_branches = []
        is_private_flags = []
        is_primary_flags = []

        for repo in repositories:
            platforms.append(repo.get("platform"))
            repository_names.append(repo.get("repository_name"))
            repository_owners.append(repo.get("repository_owner"))
            repository_urls.append(repo.get("repository_url"))
            purposes.append(repo.get("purpose"))
            primary_branches.append(repo.get("primary_branch", "main"))
            is_private_flags.append(repo.get("is_private", True))
            is_primary_flags.append(repo.get("is_primary", False))

        count = len(repositories)
        insert_query = """
            INSERT INTO project_repositories (
                organization_id, project_id, platform, repository_name,
                repository_owner, repository_url, purpose, primary_branch,
                is_private, is_primary, created_by, created_at, updated_at
            )
            SELECT
                t.organization_id,
                t.project_id,
                t.platform,
                t.repository_name,
                t.repository_owner,
                t.repository_url,
                t.purpose,
                t.primary_branch,
                t.is_private,
                t.is_primary,
                t.created_by,
                NOW(),
                NOW()
            FROM UNNEST(
                $1::uuid[],
                $2::uuid[],
                $3::text[],
                $4::text[],
                $5::text[],
                $6::text[],
                $7::text[],
                $8::text[],
                $9::boolean[],
                $10::boolean[],
                $11::uuid[]
            ) AS t(
                organization_id, project_id, platform, repository_name,
                repository_owner, repository_url, purpose, primary_branch,
                is_private, is_primary, created_by
            )
        """

        await self.db_connection.execute(
            insert_query,
            [organization_id] * count,
            [project_id] * count,
            platforms,
            repository_names,
            repository_owners,
            repository_urls,
            purposes,
            primary_branches,
            is_private_flags,
            is_primary_flags,
            [created_by] * count,
        )

    async def create_project_integrations(
        self,
        project_id: str,
        organization_id: str,
        integrations: list[dict[str, Any]],
        connected_by: str,
    ) -> None:
        """Batch insert project integrations.

        Args:
            project_id: Project UUID
            organization_id: Organization UUID
            integrations: List of integration dictionaries
            connected_by: User ID connecting the integrations
        """
        if not integrations:
            return

        # Prepare data for batch insert
        integration_types = []
        integration_names = []
        external_project_ids = []
        external_project_keys = []
        external_workspace_ids = []
        external_board_ids = []
        sync_enabled_flags = []
        sync_directions = []
        auto_sync_flags = []
        sync_interval_minutes_list = []
        integration_purposes = []
        integration_configs = []

        for integration in integrations:
            integration_types.append(integration.get("integration_type"))
            integration_names.append(integration.get("integration_name"))
            external_project_ids.append(integration.get("external_project_id"))
            external_project_keys.append(integration.get("external_project_key"))
            external_workspace_ids.append(integration.get("external_workspace_id"))
            external_board_ids.append(integration.get("external_board_id"))
            sync_enabled_flags.append(integration.get("sync_enabled", True))
            sync_directions.append(integration.get("sync_direction", "bidirectional"))
            auto_sync_flags.append(integration.get("auto_sync", True))
            sync_interval_minutes_list.append(integration.get("sync_interval_minutes", 15))
            integration_purposes.append(integration.get("integration_purpose"))
            integration_configs.append(json.dumps(integration.get("integration_config", {})))

        count = len(integrations)
        insert_query = """
            INSERT INTO project_integrations (
                organization_id, project_id, integration_type, integration_name,
                external_project_id, external_project_key, external_workspace_id,
                external_board_id, sync_enabled, sync_direction, auto_sync,
                sync_interval_minutes, integration_purpose, integration_config,
                connected_by, created_at, updated_at
            )
            SELECT
                t.organization_id,
                t.project_id,
                t.integration_type,
                t.integration_name,
                t.external_project_id,
                t.external_project_key,
                t.external_workspace_id,
                t.external_board_id,
                t.sync_enabled,
                t.sync_direction,
                t.auto_sync,
                t.sync_interval_minutes,
                t.integration_purpose,
                t.integration_config::jsonb,
                t.connected_by,
                NOW(),
                NOW()
            FROM UNNEST(
                $1::uuid[],
                $2::uuid[],
                $3::text[],
                $4::text[],
                $5::text[],
                $6::text[],
                $7::text[],
                $8::text[],
                $9::boolean[],
                $10::text[],
                $11::boolean[],
                $12::integer[],
                $13::text[],
                $14::text[],
                $15::uuid[]
            ) AS t(
                organization_id, project_id, integration_type, integration_name,
                external_project_id, external_project_key, external_workspace_id,
                external_board_id, sync_enabled, sync_direction, auto_sync,
                sync_interval_minutes, integration_purpose, integration_config, connected_by
            )
        """

        await self.db_connection.execute(
            insert_query,
            [organization_id] * count,
            [project_id] * count,
            integration_types,
            integration_names,
            external_project_ids,
            external_project_keys,
            external_workspace_ids,
            external_board_ids,
            sync_enabled_flags,
            sync_directions,
            auto_sync_flags,
            sync_interval_minutes_list,
            integration_purposes,
            integration_configs,
            [connected_by] * count,
        )

    async def check_project_id_unique(
        self, project_id: str, organization_id: str, exclude_id: str | None = None
    ) -> bool:
        """Check if project_id is unique within organization, optionally excluding an ID.

        Args:
            project_id: Project ID to check
            organization_id: Organization UUID
            exclude_id: Optional project UUID to exclude from check (for updates)

        Returns:
            bool: True if project_id is unique, False otherwise
        """
        params = [project_id, organization_id]

        if exclude_id:
            exclude_clause = "AND id != $3"
            params.append(exclude_id)
        else:
            exclude_clause = ""

        query = f"""
            SELECT NOT EXISTS(
                SELECT 1
                FROM projects
                WHERE project_id = $1
                  AND organization_id = $2
                  AND status != 'archived'
                  {exclude_clause}
            )
        """
        return await self.db_connection.fetchval(query, *params)

    def _build_project_filters(
        self,
        organization_id: str,
        filters: ProjectListQueryParams,
    ) -> tuple[str, list[Any]]:
        """Build WHERE clause and parameters for project queries.

        Args:
            organization_id: Organization UUID
            filters: ProjectListQueryParams

        Returns:
            Tuple containing (where_clause, params)
        """
        conditions = ["p.organization_id = $1", "p.status != 'archived'"]
        params: list[Any] = [organization_id]

        param_index = 2

        if filters.search and filters.search.strip():
            tsq = f"plainto_tsquery('english', ${param_index})"
            conditions.append(
                f"to_tsvector('english', "
                f"COALESCE(p.project_title, '') || ' ' || "
                f"COALESCE(p.project_description, '') || ' ' || "
                f"COALESCE(array_to_string(p.tags, ' '), '')) @@ {tsq}"
            )
            params.append(filters.search.strip())
            param_index += 1

        if filters.client_id:
            conditions.append(f"p.client_id = ${param_index}")
            params.append(filters.client_id)
            param_index += 1

        if filters.status:
            conditions.append(f"p.status = ${param_index}")
            params.append(filters.status.value)
            param_index += 1

        if filters.priority:
            conditions.append(f"p.priority = ${param_index}")
            params.append(filters.priority.value)
            param_index += 1

        if filters.tags:
            tag_list = [tag.strip() for tag in filters.tags.split(",") if tag.strip()]
            if tag_list:
                conditions.append(f"p.tags && ${param_index}::text[]")
                params.append(tag_list)
                param_index += 1

        where_clause = "WHERE " + " AND ".join(conditions)
        return where_clause, params

    async def get_projects_list(
        self,
        organization_id: str,
        filters: ProjectListQueryParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Retrieve paginated list of projects with filters.

        Paginate in base_projects, then enrich with client, team_agg (size +
        project_lead via MIN FILTER), and lead name. Total from separate
        count query.

        Returns:
            Tuple containing (list of projects, total count)
        """
        offset = (filters.page - 1) * filters.page_size
        where_clause, params = self._build_project_filters(
            organization_id=organization_id,
            filters=filters,
        )

        project_lead_role = TeamRoles.PROJECT_LEAD.value
        pl_role_param = len(params) + 1
        limit_param = len(params) + 2
        offset_param = len(params) + 3
        list_params = params + [project_lead_role, filters.page_size, offset]

        list_query = f"""
            WITH base_projects AS (
                SELECT p.*
                FROM projects p
                LEFT JOIN clients c
                    ON c.id = p.client_id
                    AND c.status != 'deleted'
                {where_clause}
                ORDER BY p.created_at DESC
                LIMIT ${limit_param}
                OFFSET ${offset_param}
            ),
            team_agg AS (
                SELECT
                    tm.team_id,
                    COUNT(*)::int AS team_size,
                    (
                        MIN(tm.user_id::text)
                        FILTER (WHERE tm.role = ${pl_role_param})
                    )::uuid AS project_lead_id
                FROM team_members tm
                GROUP BY tm.team_id
            )
            SELECT
                p.id,
                p.project_id,
                p.project_title,
                p.status,
                p.priority,
                p.project_category,
                p.practice_areas,
                p.start_date,
                p.tags,
                p.tech_stack,
                p.client_id,
                p.team_id,
                c.name AS client_name,
                c.client_type AS client_type,
                COALESCE(t.team_size, 0)::int AS team_size,
                t.project_lead_id,
                TRIM(CONCAT_WS(' ', om.first_name, om.last_name)) AS project_lead_name
            FROM base_projects p
            LEFT JOIN clients c ON c.id = p.client_id
            LEFT JOIN team_agg t ON t.team_id = p.team_id
            LEFT JOIN organization_members om
                ON om.user_id = t.project_lead_id
                AND om.organization_id = $1
                AND om.status != 'deleted'
            ORDER BY p.created_at DESC
        """

        count_query = f"""
            SELECT COUNT(*)::int FROM projects p {where_clause}
        """
        rows = await self.db_connection.fetch(list_query, *list_params)
        total_count = await self.db_connection.fetchval(count_query, *params)
        return [dict(row) for row in rows], int(total_count) if total_count is not None else 0

    async def get_project_with_client(
        self, project_id: str, organization_id: str
    ) -> dict[str, Any] | None:
        """Get project with client details using a single JOIN query.

        Joins projects with clients and primary contact for project detail use case.
        Returns None if project not found or client is deleted.

        Args:
            project_id: Project UUID or human-readable ID
            organization_id: Organization UUID

        Returns:
            Project dict with embedded client fields (client_name, client_type,
            client_email, client_phone_isd_code, client_phone_number) or None
        """
        query = """
            SELECT
                p.id, p.organization_id, p.project_id, p.project_title, p.project_description,
                p.client_id, p.status, p.priority, p.project_category, p.practice_areas,
                p.team_id, p.start_date, p.target_end_date, p.actual_end_date, p.billing_info,
                p.total_billed, p.total_hours, p.tech_stack, p.project_goals, p.success_criteria,
                p.additional_ai_context, p.primary_pm_tool, p.primary_repo_url, p.tags,
                p.custom_fields, p.is_billable, p.is_internal, p.created_at, p.updated_at,
                p.created_by, p.updated_by,
                c.id AS client_uuid,
                c.name AS client_name,
                c.client_type AS client_type,
                cu.title AS client_title,
                cu.first_name AS client_first_name,
                cu.last_name AS client_last_name,
                au.email AS client_email,
                au.raw_user_meta_data->>'phone_isd_code' AS client_phone_isd_code,
                au.raw_user_meta_data->>'phone_number' AS client_phone_number
            FROM projects p
            INNER JOIN clients c
                ON p.client_id = c.id
                AND c.organization_id = p.organization_id
                AND c.status != $3
            LEFT JOIN client_users cu
                ON cu.client_id = c.id
                AND cu.is_primary_contact = true
                AND cu.status != $4
            LEFT JOIN auth.users au ON au.id = cu.user_id
            WHERE (p.id::text = $1 OR p.project_id = $1)
              AND p.organization_id = $2::uuid
              AND p.status != 'archived'
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(
            query,
            project_id,
            organization_id,
            ClientStatus.DELETED.value,
            ClientUserStatus.DELETED.value,
        )
        return dict(row) if row else None

    async def get_project_repositories(
        self,
        project_id: str,
        organization_id: str,
        *,
        primary_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get repositories for a project.

        Args:
            project_id: Project UUID
            organization_id: Organization UUID
            primary_only: If True, return only the primary repository (at most one).

        Returns:
            List of repository dictionaries
        """
        base = """
            SELECT *
            FROM project_repositories
            WHERE project_id = $1::uuid AND organization_id = $2::uuid
        """
        order = " ORDER BY is_primary DESC, created_at ASC"
        limit = " LIMIT 1" if primary_only else ""
        where_primary = " AND is_primary = true" if primary_only else ""
        query = base + where_primary + order + limit
        rows = await self.db_connection.fetch(query, project_id, organization_id)
        return [dict(row) for row in rows]

    async def get_project_integrations(
        self,
        project_id: str,
        organization_id: str,
        *,
        primary_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get integrations for a project.

        Args:
            project_id: Project UUID
            organization_id: Organization UUID
            primary_only: If True, return first integration by created_at (at most one).

        Returns:
            List of integration dictionaries
        """
        base = """
            SELECT *
            FROM project_integrations
            WHERE project_id = $1::uuid AND organization_id = $2::uuid
        """
        order = " ORDER BY created_at ASC"
        limit = " LIMIT 1" if primary_only else ""
        query = base + order + limit
        rows = await self.db_connection.fetch(query, project_id, organization_id)
        return [dict(row) for row in rows]

    async def update_project(
        self, project_id: str, organization_id: str, data: dict[str, Any]
    ) -> None:
        """Partially update a project. Only keys present in data are updated.

        Args:
            project_id: Project UUID
            organization_id: Organization UUID
            data: Field names and values to update (must not include client_id or project_id)
        """
        if not data:
            return

        set_parts = []
        values: list[Any] = []
        idx = 1
        for key, value in data.items():
            if key in ("client_id", "project_id", "id", "organization_id"):
                continue
            if key in PROJECT_JSONB_COLUMNS and isinstance(value, (list, dict)):
                value = json.dumps(value)
            set_parts.append(f"{key} = ${idx}")
            values.append(value)
            idx += 1
        if not set_parts:
            return
        set_parts.append("updated_at = NOW()")
        values.extend([project_id, organization_id])
        where = f"WHERE id = ${idx} AND organization_id = ${idx + 1} AND status != 'archived'"
        query = f"UPDATE projects SET {', '.join(set_parts)} {where}"
        await self.db_connection.execute(query, *values)

    async def get_project_repository_ids_existing(
        self, project_id: str, organization_id: str, repository_ids: list[str]
    ) -> set[str]:
        """Return set of repository ids that exist for this project (single query)."""
        if not repository_ids:
            return set()
        query = """
            SELECT id::text FROM project_repositories
            WHERE project_id = $1::uuid AND organization_id = $2::uuid AND id = ANY($3::uuid[])
        """
        rows = await self.db_connection.fetch(
            query, project_id, organization_id, list(set(repository_ids))
        )
        return {row["id"] for row in rows}

    async def delete_project_repositories_by_ids(
        self, project_id: str, organization_id: str, repository_ids: list[str]
    ) -> None:
        """Delete project repositories by ids, scoped to project and organization."""
        if not repository_ids:
            return
        query = """
            DELETE FROM project_repositories
            WHERE project_id = $1::uuid AND organization_id = $2::uuid AND id = ANY($3::uuid[])
        """
        await self.db_connection.execute(query, project_id, organization_id, repository_ids)

    async def update_project_repository(
        self,
        project_id: str,
        organization_id: str,
        repository_id: str,
        data: dict[str, Any],
    ) -> None:
        """Partially update a single project repository. Only keys present in data are updated."""
        if not data:
            return
        forbidden = {
            "id",
            "project_id",
            "organization_id",
            "created_by",
            "created_at",
        }
        set_parts = []
        values: list[Any] = []
        idx = 1
        for key, value in data.items():
            if key in forbidden:
                continue
            set_parts.append(f"{key} = ${idx}")
            values.append(value)
            idx += 1
        if not set_parts:
            return
        set_parts.append("updated_at = NOW()")
        values.extend([repository_id, project_id, organization_id])
        where_clause = (
            f"WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid "
            f"AND organization_id = ${idx + 2}::uuid"
        )
        query = f"UPDATE project_repositories SET {', '.join(set_parts)} {where_clause}"
        await self.db_connection.execute(query, *values)

    async def get_project_integration_ids_existing(
        self, project_id: str, organization_id: str, integration_ids: list[str]
    ) -> set[str]:
        """Return set of integration ids that exist for this project (single query)."""
        if not integration_ids:
            return set()
        query = """
            SELECT id::text FROM project_integrations
            WHERE project_id = $1::uuid AND organization_id = $2::uuid AND id = ANY($3::uuid[])
        """
        rows = await self.db_connection.fetch(
            query, project_id, organization_id, list(set(integration_ids))
        )
        return {row["id"] for row in rows}

    async def delete_project_integrations_by_ids(
        self, project_id: str, organization_id: str, integration_ids: list[str]
    ) -> None:
        """Delete project integrations by ids, scoped to project and organization."""
        if not integration_ids:
            return
        query = """
            DELETE FROM project_integrations
            WHERE project_id = $1::uuid AND organization_id = $2::uuid AND id = ANY($3::uuid[])
        """
        await self.db_connection.execute(query, project_id, organization_id, integration_ids)

    async def update_project_integration(
        self,
        project_id: str,
        organization_id: str,
        integration_id: str,
        data: dict[str, Any],
    ) -> None:
        """Partially update a single project integration. Only keys present in data are updated."""
        if not data:
            return
        forbidden = {
            "id",
            "project_id",
            "organization_id",
            "connected_by",
            "created_at",
        }
        set_parts = []
        values: list[Any] = []
        idx = 1
        for key, value in data.items():
            if key in forbidden:
                continue
            if key == "integration_config" and isinstance(value, dict):
                value = json.dumps(value)
            set_parts.append(f"{key} = ${idx}")
            values.append(value)
            idx += 1
        if not set_parts:
            return
        set_parts.append("updated_at = NOW()")
        values.extend([integration_id, project_id, organization_id])
        where_clause = (
            f"WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid "
            f"AND organization_id = ${idx + 2}::uuid"
        )
        query = f"UPDATE project_integrations SET {', '.join(set_parts)} {where_clause}"
        await self.db_connection.execute(query, *values)
