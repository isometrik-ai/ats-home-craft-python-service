"""Session Database Repository Module - AsyncPG Implementation

This module contains all session-related database operations using asyncpg.
All SQL queries for session management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.schemas.enums import OrganizationMemberStatus
from libs.shared_utils.logger import get_logger

logger = get_logger("session_repository")

# Common field list for session queries
SESSION_FIELDS = (
    "id, user_id, organization_id, ip_address, user_agent, "
    "device_fingerprint, risk_score, login_timestamp, "
    "logout_timestamp, session_status, login_method, "
    "accessed_phi, phi_access_purpose"
)


class SessionRepository:
    """Database operations class for session management using asyncpg.
    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    # LISTING AND SEARCH OPERATIONS
    def _build_session_filters(
        self,
        organization_id: str | None,
        user_id: str,
        filters: SessionFilter,
        include_search: bool = False,
    ) -> tuple[str, list[object]]:
        """Build WHERE clause and parameters for session queries.

        Args:
            organization_id: Optional organization ID (can be None)
            user_id: User ID
            filters: Session filters
            include_search: Whether to include search conditions with join (uses table aliases)

        Returns:
            Tuple containing (where_clause, params) for use in SQL query
        """
        # Use table alias prefix when building search queries with joins
        table_prefix = "us." if include_search else ""

        conditions = [f"{table_prefix}user_id = $1"]
        params = [user_id]
        param_index = 2

        # Apply organization filter
        # Temporarily commented out - organization_id check disabled
        # if organization_id is None:
        #     conditions.append(f"{table_prefix}organization_id IS NULL")
        # else:
        #     conditions.append(f"{table_prefix}organization_id = ${param_index}")
        #     params.append(organization_id)
        #     param_index += 1

        # Apply session status filter
        if filters.session_status:
            conditions.append(f"{table_prefix}session_status = ${param_index}")
            params.append(filters.session_status)
            param_index += 1

        # Apply login method filter
        if filters.login_method:
            conditions.append(f"{table_prefix}login_method = ${param_index}")
            params.append(filters.login_method)
            param_index += 1

        # Handle search with organization_members join if search and organization_id are provided
        if include_search and filters.search and organization_id:
            search_term = f"%{filters.search}%"
            conditions.append(
                (
                    f"(LOWER(om.email) LIKE LOWER(${param_index}) OR "
                    f"LOWER(om.first_name || ' ' || COALESCE(om.last_name, '')) "
                    f"LIKE LOWER(${param_index}))"
                )
            )
            params.append(search_term)
            param_index += 1

        where_clause = " AND ".join(conditions)
        return where_clause, params

    async def get_sessions_with_count(
        self,
        organization_id: str | None,
        user_id: str,
        filters: SessionFilter,
    ) -> dict[str, Any]:
        """Get paginated list of sessions with total count in a single database call.

        Args:
            organization_id: Optional organization ID (can be None)
            user_id: User ID
            filters: Filters

        Returns:
            dict containing the paginated list of sessions and total count
        """
        # Determine if we need search join
        needs_search_join = bool(filters.search and organization_id)

        # Build filters
        where_clause, params = self._build_session_filters(
            organization_id=organization_id,
            user_id=user_id,
            filters=filters,
            include_search=needs_search_join,
        )

        # Build query parameters
        deleted_status_param = len(params) + 1
        limit_param = len(params) + 2
        offset_param = len(params) + 3
        deleted_status = OrganizationMemberStatus.DELETED.value

        # Build main query dynamically
        if needs_search_join:
            field_list = ", ".join([f"us.{field.strip()}" for field in SESSION_FIELDS.split(",")])
            query_params = params + [deleted_status, filters.limit, filters.offset]
            query = f"""
                SELECT DISTINCT {field_list}
                FROM user_sessions us
                INNER JOIN organization_members om
                    ON us.user_id = om.user_id
                    AND om.status != ${deleted_status_param}
                WHERE {where_clause}
                ORDER BY us.login_timestamp DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
            """
            count_query_params = params + [deleted_status]
            count_query = f"""
                SELECT COUNT(DISTINCT us.id)
                FROM user_sessions us
                INNER JOIN organization_members om
                    ON us.user_id = om.user_id
                    AND om.status != ${deleted_status_param}
                WHERE {where_clause}
            """
        else:
            limit_param = len(params) + 1
            offset_param = len(params) + 2
            query_params = params + [filters.limit, filters.offset]
            query = f"""
                SELECT {SESSION_FIELDS}
                FROM user_sessions
                WHERE {where_clause}
                ORDER BY login_timestamp DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
            """
            count_query_params = params
            count_query = f"""
                SELECT COUNT(*)
                FROM user_sessions
                WHERE {where_clause}
            """

        # Execute queries
        rows = await self.db_connection.fetch(query, *query_params)
        total_count = await self.db_connection.fetchval(count_query, *count_query_params) or 0
        data = [dict(row) for row in rows]

        return {
            "data": data,
            "total_count": total_count,
        }

    def _build_org_session_filters(
        self,
        organization_id: str,
        filters: SessionFilter,
        include_search: bool = False,
    ) -> tuple[str, list[object]]:
        """Build WHERE clause and parameters for organization-wide session queries.

        Args:
            organization_id: Organization ID
            filters: Session filters
            include_search: Whether to include search conditions with join

        Returns:
            Tuple containing (where_clause, params) for use in SQL query
        """
        conditions = ["us.organization_id = $1"]
        params = [organization_id]
        param_index = 2

        # Apply session status filter
        if filters.session_status:
            conditions.append(f"us.session_status = ${param_index}")
            params.append(filters.session_status)
            param_index += 1

        # Apply login method filter
        if filters.login_method:
            conditions.append(f"us.login_method = ${param_index}")
            params.append(filters.login_method)
            param_index += 1

        # Handle search with organization_members join if search is provided
        if include_search and filters.search:
            search_term = f"%{filters.search}%"
            conditions.append(
                (
                    f"(LOWER(om.email) LIKE LOWER(${param_index}) OR "
                    f"LOWER(om.first_name || ' ' || COALESCE(om.last_name, '')) "
                    f"LIKE LOWER(${param_index}) OR "
                    f"LOWER(us.ip_address::text) LIKE LOWER(${param_index}) OR "
                    f"LOWER(us.user_agent) LIKE LOWER(${param_index}))"
                )
            )

            params.append(search_term)
            param_index += 1

        where_clause = " AND ".join(conditions)
        return where_clause, params

    async def get_org_sessions_with_count(
        self,
        organization_id: str,
        filters: SessionFilter,
    ) -> dict[str, Any]:
        """Get paginated list of sessions for **all users** in an organization
        along with a total count.

        Args:
            organization_id: Organization ID (required for org-wide queries)
            filters: Filters

        Returns:
            dict containing the paginated list of sessions and total count
        """
        if not organization_id:
            return {"data": [], "total_count": 0}

        # Determine if we need search join
        needs_search_join = bool(filters.search)

        # Build filters
        where_clause, params = self._build_org_session_filters(
            organization_id=organization_id,
            filters=filters,
            include_search=needs_search_join,
        )

        # Build query parameters
        deleted_status_param = len(params) + 1
        limit_param = len(params) + 2
        offset_param = len(params) + 3
        deleted_status = OrganizationMemberStatus.DELETED.value

        # Build main query dynamically
        if needs_search_join:
            field_list = ", ".join([f"us.{field.strip()}" for field in SESSION_FIELDS.split(",")])
            query_params = params + [deleted_status, filters.limit, filters.offset]
            query = f"""
                SELECT DISTINCT {field_list}
                FROM user_sessions us
                INNER JOIN organization_members om ON us.user_id = om.user_id
                    AND om.organization_id = $1
                    AND om.status != ${deleted_status_param}
                WHERE {where_clause}
                ORDER BY us.login_timestamp DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
            """
            count_query_params = params + [deleted_status]
            count_query = f"""
                SELECT COUNT(DISTINCT us.id)
                FROM user_sessions us
                INNER JOIN organization_members om ON us.user_id = om.user_id
                    AND om.organization_id = $1
                    AND om.status != ${deleted_status_param}
                WHERE {where_clause}
            """
        else:
            limit_param = len(params) + 1
            offset_param = len(params) + 2
            query_params = params + [filters.limit, filters.offset]
            query = f"""
                SELECT {SESSION_FIELDS}
                FROM user_sessions us
                WHERE {where_clause}
                ORDER BY us.login_timestamp DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
            """
            count_query_params = params
            count_query = f"""
                SELECT COUNT(*)
                FROM user_sessions us
                WHERE {where_clause}
            """

        # Execute queries
        rows = await self.db_connection.fetch(query, *query_params)
        total_count = await self.db_connection.fetchval(count_query, *count_query_params) or 0
        data = [dict(row) for row in rows]

        return {"data": data, "total_count": total_count}
