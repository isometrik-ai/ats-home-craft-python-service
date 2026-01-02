"""Organization Database Repository Module - AsyncPG Implementation

This module contains organization-related database operations using asyncpg.
All SQL queries for organization management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("organization_repository")


class OrganizationRepository:
    """Database operations class for organization management using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    # CREATE OPERATIONS
    async def create_organization(self, organization_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new organization record."""
        columns = list(organization_data.keys())
        values = [organization_data[col] for col in columns]
        placeholders = [f"${idx}" for idx in range(1, len(columns) + 1)]

        query = f"""
            INSERT INTO organizations ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)

    # READ OPERATIONS
    def _build_organization_conditions(
        self,
        search: str | None = None,
        status: str | None = None,
    ) -> tuple[str, list[Any]]:
        """Build WHERE conditions for organization queries."""

        conditions: list[str] = ["o.status != 'archived'"]
        params: list[Any] = []
        idx = 1

        if search:
            search_term = f"%{search.strip()}%"
            conditions.append(
                f"""(
                    o.name ILIKE ${idx}
                    OR o.slug ILIKE ${idx + 1}
                    OR o.domain ILIKE ${idx + 2}
                )"""
            )
            params.extend([search_term, search_term, search_term])
            idx += 3

        if status:
            conditions.append(f"o.status = ${idx}")
            params.append(status)
            idx += 1

        return " AND ".join(conditions), params

    async def get_organizations_list(
        self,
        search: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Retrieve paginated list of organizations with active member counts."""

        where_clause, params = self._build_organization_conditions(search, status)

        params.extend([limit, offset])
        limit_idx = len(params) - 1
        offset_idx = len(params)

        query = f"""
            SELECT
                o.id,
                o.name,
                o.slug,
                o.domain,
                o.logo_url,
                o.status,
                o.timezone,
                o.settings,
                o.subscription,
                o.description,
                o.company_size,
                o.created_at,
                o.updated_at,
                COALESCE(om.member_count, 0)::int AS member_count
            FROM organizations o
            LEFT JOIN (
                SELECT
                    organization_id,
                    COUNT(*) FILTER (WHERE status = 'active')::int AS member_count
                FROM organization_members
                GROUP BY organization_id
            ) om ON om.organization_id = o.id
            WHERE {where_clause}
            ORDER BY o.created_at DESC
            LIMIT ${limit_idx}
            OFFSET ${offset_idx}
        """

        rows = await self.db_connection.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_organizations_count(
        self,
        search: str | None = None,
        status: str | None = None,
    ) -> int:
        """Get total count of organizations matching search criteria."""

        where_clause, params = self._build_organization_conditions(search, status)

        query = f"""
            SELECT COUNT(*)::int
            FROM organizations o
            WHERE {where_clause}
        """

        return await self.db_connection.fetchval(query, *params) or 0

    async def get_organization_by_id(
        self,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get organization by ID with active member count."""

        query = """
            SELECT
                o.id,
                o.name,
                o.slug,
                o.domain,
                o.logo_url,
                o.status,
                o.timezone,
                o.settings,
                o.subscription,
                o.description,
                o.company_size,
                o.created_at,
                o.updated_at,
                COALESCE(om.member_count, 0)::int AS member_count
            FROM organizations o
            LEFT JOIN (
                SELECT
                    organization_id,
                    COUNT(*) FILTER (WHERE status = 'active')::int AS member_count
                FROM organization_members
                GROUP BY organization_id
            ) om ON om.organization_id = o.id
            WHERE o.id = $1
            AND o.status != 'archived'
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(query, organization_id)
        return dict(row) if row else None

    async def get_organization_for_update(
        self,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get minimal organization fields needed for update operations.

        Returns id, name, slug, and settings.
        """
        query = """
            SELECT
                o.id,
                o.name,
                o.slug,
                o.settings
            FROM organizations o
            WHERE o.id = $1
            AND o.status != 'archived'
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, organization_id)
        return dict(row) if row else None

    # VALIDATION OPERATIONS
    async def check_organization_exists(self, organization_id: str) -> bool:
        """Check if organization exists and is not archived."""
        query = """
            SELECT EXISTS(
                SELECT 1 FROM organizations
                WHERE id = $1 AND status != 'archived'
            )
        """
        return await self.db_connection.fetchval(query, organization_id)

    async def check_slug_unique(self, slug: str, exclude_id: str | None = None) -> bool:
        """Check if organization slug is unique, optionally excluding an ID."""
        exclude_clause = ""
        params = [slug]

        if exclude_id:
            exclude_clause = "AND id != $2"
            params.append(exclude_id)

        query = f"""
            SELECT NOT EXISTS(
                SELECT 1
                FROM organizations
                WHERE slug = $1
                  AND status != 'archived'
                  {exclude_clause}
            )
        """
        return await self.db_connection.fetchval(query, *params)

    # UPDATE OPERATIONS
    async def update_organization(
        self,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update organization fields."""
        if not update_data:
            return {}

        set_clauses = []
        params: list[Any] = []
        for idx, (field, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{field} = ${idx}")
            params.append(value)

        params.extend([organization_id])

        query = f"""
            UPDATE organizations
            SET {", ".join(set_clauses)}, updated_at = NOW()
            WHERE id = ${len(params)} AND status != 'archived'
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, *params)
        if not row:
            raise NotFoundException(
                message_key="organizations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(row)

    # DELETE OPERATIONS
    async def delete_organization(self, organization_id: str) -> None:
        """Delete organization (hard delete)."""
        query = """
            DELETE FROM organizations
            WHERE id = $1
            RETURNING id
        """
        result = await self.db_connection.fetchval(query, organization_id)
        if result is None:
            raise NotFoundException(
                message_key="organizations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
