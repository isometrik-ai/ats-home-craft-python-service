"""Session Database Repository Module - AsyncPG Implementation

This module contains all session-related database operations using asyncpg.
All SQL queries for session management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.schemas.enums import (
    OrganizationMemberStatus,
    SessionStatus,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("session_repository")

# Common field list for session queries
SESSION_FIELDS = (
    "id, user_id, organization_id, ip_address, user_agent, "
    "device_fingerprint, risk_score, login_timestamp, "
    "logout_timestamp, session_status, login_method, "
    "accessed_phi, phi_access_purpose"
)

# - org sessions: prefer organization_members (email + first/last)
SESSION_USER_FIELDS = """
    COALESCE(om.email, au.email) AS user_email,
    COALESCE(
        NULLIF(TRIM(COALESCE(om.first_name, '') || ' ' || COALESCE(om.last_name, '')), ''),
        au.raw_user_meta_data->>'full_name',
        au.raw_user_meta_data->>'name'
    ) AS user_name
""".strip()


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
        if organization_id is None:
            conditions.append(f"{table_prefix}organization_id IS NULL")
        else:
            conditions.append(f"{table_prefix}organization_id = ${param_index}")
            params.append(organization_id)
            param_index += 1

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

        # Handle search (email/name) across organization_members + auth.users
        if include_search and filters.search:
            search_term = f"%{filters.search}%"
            param_placeholder = f"${param_index}"
            conditions.append(
                (
                    f"""
                    (
                        LOWER(COALESCE(om.email, au.email)) LIKE LOWER({param_placeholder})
                        OR LOWER(
                            NULLIF(
                                TRIM(
                                    COALESCE(om.first_name, '')
                                    || ' '
                                    || COALESCE(om.last_name, '')
                                ),
                                ''
                            )
                        ) LIKE LOWER({param_placeholder})
                        OR LOWER(
                            COALESCE(
                                au.raw_user_meta_data->>'full_name',
                                au.raw_user_meta_data->>'name',
                                ''
                            )
                        ) LIKE LOWER({param_placeholder})
                    )
                    """
                ).strip()
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
        needs_search_join = bool(filters.search and organization_id)
        where_clause, params = self._build_session_filters(
            organization_id=organization_id,
            user_id=user_id,
            filters=filters,
            include_search=True,
        )

        limit_param = len(params) + 1
        offset_param = len(params) + 2
        query_params = [*params, filters.limit, filters.offset]

        field_list = ", ".join(f"us.{field.strip()}" for field in SESSION_FIELDS.split(","))
        query = f"""
            SELECT DISTINCT {field_list}, {SESSION_USER_FIELDS}
            FROM user_sessions us
            LEFT JOIN organization_members om
                ON us.user_id = om.user_id
                AND om.organization_id = us.organization_id
                AND om.status != '{OrganizationMemberStatus.DELETED.value}'
            LEFT JOIN auth.users au
                ON au.id = us.user_id
            WHERE {where_clause}
            ORDER BY us.login_timestamp DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
        """

        # Count query must include join only when search uses om.* fields.
        if needs_search_join:
            deleted_status_param = len(params) + 1
            count_query_params = [*params, OrganizationMemberStatus.DELETED.value]
            count_query = f"""
                SELECT COUNT(DISTINCT us.id)
                FROM user_sessions us
                INNER JOIN organization_members om
                    ON us.user_id = om.user_id
                    AND om.status != ${deleted_status_param}
                LEFT JOIN auth.users au
                    ON au.id = us.user_id
                WHERE {where_clause}
            """
        else:
            count_query_params = params
            if filters.search:
                count_query = f"""
                    SELECT COUNT(*)
                    FROM user_sessions us
                    LEFT JOIN auth.users au
                        ON au.id = us.user_id
                    WHERE {where_clause}
                """
            else:
                count_query = f"""
                    SELECT COUNT(*)
                    FROM user_sessions us
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
            param_placeholder = f"${param_index}"
            conditions.append(
                (
                    f"""
                    (
                        LOWER(COALESCE(om.email, au.email)) LIKE LOWER({param_placeholder})
                        OR LOWER(
                            NULLIF(
                                TRIM(
                                    COALESCE(om.first_name, '')
                                    || ' '
                                    || COALESCE(om.last_name, '')
                                ),
                                ''
                            )
                        ) LIKE LOWER({param_placeholder})
                        OR LOWER(
                            COALESCE(
                                au.raw_user_meta_data->>'full_name',
                                au.raw_user_meta_data->>'name',
                                ''
                            )
                        ) LIKE LOWER({param_placeholder})
                        OR LOWER(us.ip_address::text) LIKE LOWER({param_placeholder})
                        OR LOWER(us.user_agent) LIKE LOWER({param_placeholder})
                    )
                    """
                ).strip()
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
                SELECT DISTINCT {field_list}, {SESSION_USER_FIELDS}
                FROM user_sessions us
                INNER JOIN organization_members om ON us.user_id = om.user_id
                    AND om.organization_id = $1
                    AND om.status != ${deleted_status_param}
                LEFT JOIN auth.users au
                    ON au.id = us.user_id
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
                LEFT JOIN auth.users au
                    ON au.id = us.user_id
                WHERE {where_clause}
            """
        else:
            limit_param = len(params) + 1
            offset_param = len(params) + 2
            query_params = params + [filters.limit, filters.offset]
            field_list = ", ".join([f"us.{field.strip()}" for field in SESSION_FIELDS.split(",")])
            query = f"""
                SELECT {field_list}, {SESSION_USER_FIELDS}
                FROM user_sessions us
                LEFT JOIN organization_members om
                    ON us.user_id = om.user_id
                    AND om.organization_id = us.organization_id
                    AND om.status != '{OrganizationMemberStatus.DELETED.value}'
                LEFT JOIN auth.users au
                    ON au.id = us.user_id
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

    # SESSION LIFECYCLE OPERATIONS
    async def get_session_organization_id(self, session_id: str) -> str | None:
        """Get organization_id for a session.

        Args:
            session_id: Session ID

        Returns:
            Organization ID or None if not found or session is inactive
        """
        query = """
            SELECT organization_id
            FROM user_sessions
            WHERE id = $1 AND session_status = $2
        """
        result = await self.db_connection.fetchval(query, session_id, SessionStatus.ACTIVE.value)
        return str(result) if result else None

    async def get_valid_session_context(self, session_id: str) -> dict[str, Any] | None:
        """Validate session (single DB call) and return session context.

        This validates **session existence only** (not organization existence):
        - session exists in our `user_sessions` table
        - session is ACTIVE
        - session still exists in Supabase Auth (`auth.sessions`) (revocation support)

        Returns a dict containing `organization_id` (can be None) or None if invalid.
        """
        if not session_id or not session_id.strip():
            return None

        query = """
            SELECT us.organization_id
            FROM user_sessions us
            WHERE us.id = $1
              AND us.session_status = $2
              AND EXISTS (
                SELECT 1
                FROM auth.sessions s
                WHERE s.id = us.id
              )
        """
        row = await self.db_connection.fetchrow(query, session_id, SessionStatus.ACTIVE.value)
        if not row:
            return None
        org_id = row.get("organization_id") if hasattr(row, "get") else row["organization_id"]
        return {"organization_id": str(org_id) if org_id is not None else None}

    async def check_session_has_organization(self, session_id: str) -> bool:
        """Check if session already has an organization_id linked.

        Args:
            session_id: Session ID

        Returns:
            dict: Session data if session has organization_id, None otherwise
        """
        query = """
            SELECT organization_id
            FROM user_sessions
            WHERE id = $1
                AND session_status = $2
        """
        result = await self.db_connection.fetchrow(query, session_id, SessionStatus.ACTIVE.value)
        return dict(result) if result else None

    async def update_session_organization_context(
        self,
        session_id: str,
        user_id: str,
        organization_id: str,
    ) -> None:
        """Update session organization context.

        Args:
            session_id: Session ID to update
            user_id: User ID (for validation)
            organization_id: Organization ID to set as active context
        """
        query = """
            UPDATE user_sessions
            SET organization_id = $1
            WHERE id = $2
                AND user_id = $3
                AND session_status = $4
        """
        await self.db_connection.execute(
            query, organization_id, session_id, user_id, SessionStatus.ACTIVE.value
        )

    async def delete_auth_session_by_id(self, session_id: str) -> None:
        """Delete a Supabase Auth session row by session id.

        This revokes the session at Supabase level by deleting from `auth.sessions`.
        Requires a privileged Postgres connection.
        """
        await self.db_connection.execute("DELETE FROM auth.sessions WHERE id = $1", session_id)

    async def delete_auth_session(self, session_id: str) -> str | None:
        """Delete a row from ``auth.sessions`` by id (Supabase session revocation).

        ``session_id`` should come from a JWT already validated by auth middleware.

        Returns:
            Deleted session id as string, or ``None`` if no row was deleted.
        """
        query = """
            DELETE FROM auth.sessions s
            WHERE s.id = $1
            RETURNING s.id::text
        """
        row = await self.db_connection.fetchrow(query, session_id)
        return str(row["id"]) if row else None

    async def revoke_org_sessions_for_user(self, user_id: str, organization_id: str) -> None:
        """Revoke sessions for a user **scoped to one organization**.

        Implementation detail:
        - We revoke at Supabase level by deleting rows from `auth.sessions`
          for session ids that exist in `public.user_sessions` for the given
          (user_id, organization_id).
        - We intentionally do **not** update rows in `public.user_sessions` here,
          because lifecycle bookkeeping is handled by database triggers.

        """
        if not user_id or not user_id.strip():
            return
        if not organization_id or not organization_id.strip():
            return

        query = """
            DELETE FROM auth.sessions s
            USING public.user_sessions us
            WHERE us.id = s.id
              AND us.user_id = $1
              AND us.organization_id = $2
        """
        await self.db_connection.execute(query, user_id, organization_id)
