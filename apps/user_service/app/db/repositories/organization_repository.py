"""Organization Database Repository Module - AsyncPG Implementation

This module contains organization-related database operations using asyncpg.
All SQL queries for organization management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import OrganizationMemberStatus
from apps.user_service.app.schemas.organizations import (
    OrganizationStatus,
)
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

        deleted_status = OrganizationStatus.DELETED.value
        conditions: list[str] = ["o.status != $1"]
        params: list[Any] = [deleted_status]
        idx = 2

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

        where_clause, where_params = self._build_organization_conditions(search, status)

        # Build params in order: where conditions, then enum values for subquery, then pagination
        active_status = OrganizationMemberStatus.ACTIVE.value
        deleted_status = OrganizationMemberStatus.DELETED.value

        # Calculate parameter indices based on where_params length
        base_idx = len(where_params)
        active_idx = base_idx + 1
        deleted_idx = base_idx + 2
        limit_idx = base_idx + 3
        offset_idx = base_idx + 4

        params = [
            *where_params,
            active_status,
            deleted_status,
            limit,
            offset,
        ]

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
                    COUNT(*) FILTER (WHERE status = ${active_idx})::int AS member_count
                FROM organization_members
                WHERE status != ${deleted_idx}
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

        active_status = OrganizationMemberStatus.ACTIVE.value
        deleted_member_status = OrganizationMemberStatus.DELETED.value
        deleted_org_status = OrganizationStatus.DELETED.value

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
                    COUNT(*) FILTER (WHERE status = $2)::int AS member_count
                FROM organization_members
                WHERE status != $3
                GROUP BY organization_id
            ) om ON om.organization_id = o.id
            WHERE o.id = $1
            AND o.status != $4
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(
            query, organization_id, active_status, deleted_member_status, deleted_org_status
        )
        return dict(row) if row else None

    async def get_organization_for_update(
        self,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get minimal organization fields needed for update operations.

        Returns id, name, slug, and settings.
        """
        deleted_status = OrganizationStatus.DELETED.value
        query = """
            SELECT
                o.id,
                o.name,
                o.slug,
                o.settings
            FROM organizations o
            WHERE o.id = $1
            AND o.status != $2
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, organization_id, deleted_status)
        return dict(row) if row else None

    # VALIDATION OPERATIONS
    async def check_organization_exists(self, organization_id: str) -> bool:
        """Check if organization exists and is not deleted."""
        deleted_status = OrganizationStatus.DELETED.value
        query = """
            SELECT EXISTS(
                SELECT 1 FROM organizations
                WHERE id = $1 AND status != $2
            )
        """
        return await self.db_connection.fetchval(query, organization_id, deleted_status)

    async def check_slug_unique(self, slug: str, exclude_id: str | None = None) -> bool:
        """Check if organization slug is unique, optionally excluding an ID."""
        deleted_status = OrganizationStatus.DELETED.value
        params = [slug, deleted_status]

        if exclude_id:
            exclude_clause = "AND id != $3"
            params.append(exclude_id)
        else:
            exclude_clause = ""

        query = f"""
            SELECT NOT EXISTS(
                SELECT 1
                FROM organizations
                WHERE slug = $1
                  AND status != $2
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

        deleted_status = OrganizationStatus.DELETED.value
        org_id_param = len(params) + 1
        deleted_status_param = len(params) + 2
        params.extend([organization_id, deleted_status])

        query = f"""
            UPDATE organizations
            SET {", ".join(set_clauses)}, updated_at = NOW()
            WHERE id = ${org_id_param} AND status != ${deleted_status_param}
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, *params)
        if not row:
            raise NotFoundException(
                message_key="organizations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(row)

    async def is_user_organization_owner(
        self,
        organization_id: str,
        user_id: str,
    ) -> bool:
        """Check whether the given user is the owner (creator) of the organization."""

        query = """
            SELECT 1
            FROM organizations
            WHERE id = $1
            AND created_by_id = $2
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(
            query,
            organization_id,
            user_id,
        )

        return row is not None

    # DELETE OPERATIONS
    async def delete_organization(self, organization_id: str) -> None:
        """Soft delete organization by setting status to 'deleted'."""
        deleted_status = OrganizationStatus.DELETED.value
        query = """
            UPDATE organizations
            SET status = $2, updated_at = NOW()
            WHERE id = $1
            AND status != $2
            RETURNING id
        """
        row = await self.db_connection.fetchrow(query, organization_id, deleted_status)
        if not row:
            raise NotFoundException(
                message_key="organizations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
