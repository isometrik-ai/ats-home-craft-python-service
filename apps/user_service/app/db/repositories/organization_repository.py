"""Organization Database Repository Module - AsyncPG Implementation

This module contains organization-related database operations using asyncpg.
All SQL queries for organization management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import (
    DeleteRequestStatus,
    OrganizationMemberRole,
    OrganizationMemberStatus,
    OrganizationStatus,
    PlanType,
    SuperadminOrganizationListStatus,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("organization_repository")


_SUPERADMIN_ORG_SORT_SQL = {
    "created_at": "o.created_at",
    "name": "o.name",
    "member_count": "COALESCE(mc.member_count, 0)",
}


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

    def _build_superadmin_organization_list_where(
        self,
        search: str | None,
        plan_type: str | None,
        list_status: str | None,
    ) -> tuple[str, list[Any]]:
        """WHERE clause for superadmin org list (excludes deleted orgs). Uses `ow` lateral alias."""
        deleted_org = OrganizationStatus.DELETED.value
        conditions: list[str] = ["o.status <> $1"]
        params: list[Any] = [deleted_org]
        idx = 2

        if search and search.strip():
            term = f"%{search.strip()}%"
            owner_display_ilike = (
                "TRIM(CONCAT(COALESCE(ow.first_name, ''), ' ', "
                "COALESCE(ow.last_name, ''))) ILIKE "
                f"${idx}"
            )
            conditions.append(
                f"""(
                    o.name ILIKE ${idx}
                    OR ow.email ILIKE ${idx}
                    OR {owner_display_ilike}
                )"""
            )
            params.append(term)
            idx += 1

        if plan_type:
            conditions.append(f"COALESCE(o.subscription::jsonb->>'plan_type', '') = ${idx}")
            params.append(plan_type)
            idx += 1

        pending_literal = DeleteRequestStatus.PENDING.value
        pending_exists = (
            "EXISTS (SELECT 1 FROM organization_delete_requests odr "
            "WHERE odr.organization_id = o.id AND odr.status = "
            f"'{pending_literal}')"
        )
        not_pending = (
            "NOT EXISTS (SELECT 1 FROM organization_delete_requests odr "
            "WHERE odr.organization_id = o.id AND odr.status = "
            f"'{pending_literal}')"
        )

        if list_status == SuperadminOrganizationListStatus.PENDING_DELETION.value:
            conditions.append(pending_exists)
        elif list_status == SuperadminOrganizationListStatus.SUSPENDED.value:
            conditions.append(not_pending)
            conditions.append(f"o.status = ${idx}")
            params.append(OrganizationStatus.SUSPENDED.value)
            idx += 1
        elif list_status == SuperadminOrganizationListStatus.ACTIVE.value:
            conditions.append(not_pending)
            conditions.append(f"o.status IN (${idx}, ${idx + 1})")
            params.append(OrganizationStatus.ACTIVE.value)
            params.append(OrganizationStatus.TRIAL.value)
            idx += 2

        return " AND ".join(conditions), params

    async def get_superadmin_organizations_list(
        self,
        *,
        search: str | None = None,
        plan_type: str | None = None,
        list_status: str | None = None,
        sort_field: str = "created_at",
        sort_order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated superadmin org list + total count in one round-trip.

        Uses ROW_NUMBER over the filtered set and a total CTE so an out-of-range page
        still returns the correct total (one sentinel row with null org columns).
        """
        where_sql, where_params = self._build_superadmin_organization_list_where(
            search, plan_type, list_status
        )
        owner_role = OrganizationMemberRole.OWNER.value
        deleted_member = OrganizationMemberStatus.DELETED.value

        order_sql = _SUPERADMIN_ORG_SORT_SQL.get(sort_field, "o.created_at")
        direction = "ASC" if (sort_order or "").lower() == "asc" else "DESC"

        base_idx = len(where_params)
        owner_role_idx = base_idx + 1
        deleted_mem_idx = base_idx + 2
        offset_idx = base_idx + 3
        end_rn_idx = base_idx + 4
        end_rn = offset + limit

        params = [
            *where_params,
            owner_role,
            deleted_member,
            offset,
            end_rn,
        ]

        query = f"""
            WITH base AS (
                SELECT
                    o.id,
                    o.name,
                    o.created_at,
                    COALESCE(mc.member_count, 0)::int AS member_count,
                    ow.user_id::text AS owner_user_id,
                    ow.email AS owner_email,
                    ow.first_name AS owner_first_name,
                    ow.last_name AS owner_last_name,
                    COALESCE(
                        o.subscription::jsonb->>'plan_type',
                        '{PlanType.TRIAL.value}'
                    ) AS plan_type,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM organization_delete_requests odr
                            WHERE odr.organization_id = o.id
                              AND odr.status = '{DeleteRequestStatus.PENDING.value}'
                        ) THEN '{SuperadminOrganizationListStatus.PENDING_DELETION.value}'
                        WHEN o.status = '{OrganizationStatus.SUSPENDED.value}'
                        THEN '{SuperadminOrganizationListStatus.SUSPENDED.value}'
                        ELSE '{SuperadminOrganizationListStatus.ACTIVE.value}'
                    END AS list_status,
                    ROW_NUMBER() OVER (
                        ORDER BY {order_sql} {direction}, o.id ASC
                    ) AS rn
                FROM organizations o
                LEFT JOIN LATERAL (
                    SELECT om.user_id, om.email, om.first_name, om.last_name
                    FROM organization_members om
                    WHERE om.organization_id = o.id
                      AND om.member_role = ${owner_role_idx}
                      AND om.status <> ${deleted_mem_idx}
                    ORDER BY om.created_at ASC
                    LIMIT 1
                ) ow ON true
                LEFT JOIN (
                    SELECT organization_id, COUNT(*)::int AS member_count
                    FROM organization_members
                    WHERE status <> ${deleted_mem_idx}
                    GROUP BY organization_id
                ) mc ON mc.organization_id = o.id
                WHERE {where_sql}
            ),
            tot AS (SELECT COUNT(*)::int AS c FROM base)
            SELECT
                t.c::int AS _total_count,
                b.id,
                b.name,
                b.created_at,
                b.member_count,
                b.owner_user_id,
                b.owner_email,
                b.owner_first_name,
                b.owner_last_name,
                b.plan_type,
                b.list_status
            FROM tot t
            LEFT JOIN base b ON b.rn > ${offset_idx} AND b.rn <= ${end_rn_idx}
        """

        rows = await self.db_connection.fetch(query, *params)
        total = int(rows[0]["_total_count"])
        items: list[dict[str, Any]] = []
        for row in rows:
            if row["id"] is not None:
                item = dict(row)
                item.pop("_total_count", None)
                items.append(item)
        return items, total

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

    async def get_organization_details(
        self,
        organization_id: str,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
    ) -> dict[str, Any] | None:
        """Get organization basic details."""

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
                o.updated_at
            FROM organizations o
            WHERE o.id = $1
            AND o.status = $2
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, organization_id, status.value)
        return dict(row) if row else None

    async def get_organization_context_by_isometrik_project_id(
        self,
        project_id: str,
    ) -> tuple[str, str] | None:
        """Resolve (organization_id, organization_name) by Isometrik project id."""
        deleted_org_status = OrganizationStatus.DELETED.value
        query = """
            SELECT
                o.id::text,
                o.name
            FROM organizations o
            WHERE o.status != $2
              AND o.isometrik_project_id = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, project_id, deleted_org_status)
        if not row:
            return None
        org_id = str(row["id"])
        org_name = str(row.get("name") or "")
        return org_id, org_name

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

    async def update_subscription_users(
        self,
        organization_id: str,
        increment_by: int = 1,
    ) -> None:
        """Increment users in subscription via a single in-DB UPDATE (no read into memory)."""
        deleted_status = OrganizationStatus.DELETED.value
        query = """
            UPDATE organizations
            SET
                subscription = jsonb_set(
                    COALESCE(subscription::jsonb, '{}'::jsonb),
                    '{users}',
                    to_jsonb(GREATEST(COALESCE((subscription::jsonb->>'users')::int, 0) + $2, 0)::int)
                ),
                updated_at = NOW()
            WHERE id = $1 AND status != $3
            RETURNING id
        """
        row = await self.db_connection.fetchrow(
            query, organization_id, increment_by, deleted_status
        )
        if not row:
            raise NotFoundException(
                message_key="organizations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def get_user_active_organizations(self, user_id: str) -> list[dict[str, Any]]:
        """Get user's active organizations with basic details.

        Args:
            user_id: User ID

        Returns:
            list[dict[str, Any]]: List of organizations with id, name, domain, logo_url, description
        """
        active_member_status = OrganizationMemberStatus.ACTIVE.value
        active_org_status = OrganizationStatus.ACTIVE.value

        query = """
            SELECT
                o.id,
                o.name,
                o.domain,
                o.logo_url,
                o.description
            FROM organizations o
            INNER JOIN organization_members om ON om.organization_id = o.id
            WHERE om.user_id = $1
                AND om.status = $2
                AND o.status = $3
            ORDER BY o.created_at DESC
        """

        rows = await self.db_connection.fetch(
            query,
            user_id,
            active_member_status,
            active_org_status,
        )
        return [dict(row) for row in rows]

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
