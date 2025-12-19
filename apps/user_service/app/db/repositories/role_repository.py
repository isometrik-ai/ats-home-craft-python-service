"""Role Database Repository Module - AsyncPG Implementation

This module contains all role-related database operations using asyncpg.
All SQL queries for role management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("role_repository")


class RoleRepository:
    """Database operations class for role management using asyncpg.

    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection
        """
        self.db_connection = db_connection

    # CREATE OPERATIONS
    async def create_role(
        self, name: str, description: str, organization_id: str, is_default: bool = False
    ) -> dict[str, Any]:
        """Create a new role.

        Args:
            name: Role name
            description: Role description
            organization_id: Organization ID
            is_default: Whether the role is the default role

        Returns:
            dict containing the new role
        """
        query = """
            INSERT INTO roles (name, description, organization_id, is_default)
            VALUES ($1, $2, $3, $4)
            RETURNING *
        """
        return await self.db_connection.fetchrow(
            query, name, description, organization_id, is_default
        )

    async def assign_permissions_to_role(
        self, role_id: str, organization_id: str, permission_ids: list[str]
    ) -> None:
        """Assign permissions to a role.

        Args:
            role_id: Role ID
            organization_id: Organization ID
            permission_ids: List of permission IDs
        """
        # First verify the role exists in the organization
        role = await self.get_role_by_id(role_id, organization_id)
        if not role:
            raise NotFoundException(
                message_key="errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Insert role-permission mappings
        query = """
            INSERT INTO role_permissions (role_id, permission_id, organization_id)
            SELECT $1, unnest($2::uuid[]), $3
            ON CONFLICT DO NOTHING
        """
        await self.db_connection.execute(query, role_id, permission_ids, organization_id)

    # READ OPERATIONS
    async def get_role_by_id(self, role_id: str, organization_id: str) -> dict[str, Any] | None:
        """Get role by ID and organization ID.

        Args:
            role_id: Role ID
            organization_id: Organization ID

        Returns:
            dict containing the role or None if not found
        """
        query = """
            SELECT
                id,
                name,
                description,
                is_default,
                updated_at,
                created_at
            FROM roles
            WHERE id = $1
            AND organization_id = $2
        """
        return await self.db_connection.fetchrow(query, role_id, organization_id)

    def _get_base_roles_query(self) -> str:
        """Get the base query for role data with pagination."""
        return """
            SELECT
                r.id,
                r.name,
                r.description,
                r.is_default,
                r.created_at,
                r.updated_at
            FROM roles r
            WHERE r.organization_id = $1
            AND ($2::text IS NULL OR r.name ILIKE '%' || $2 || '%')
            ORDER BY r.updated_at DESC
            LIMIT COALESCE($3, 20)
            OFFSET COALESCE($4, 0)
        """

    def _get_member_counts_query(self) -> str:
        """Get the query for counting active members per role."""
        return """
            SELECT
                om.role_id,
                COUNT(*)::int AS user_count
            FROM organization_members om
            WHERE om.organization_id = $1
            AND om.status = 'active'
            AND om.role_id IN (SELECT id FROM base_roles)
            GROUP BY om.role_id
        """

    def _get_permission_flat_query(self) -> str:
        """Get the query for flattened role permissions."""
        return """
            SELECT
                rp.role_id,
                p.category
            FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.organization_id = $1
            AND rp.role_id IN (SELECT id FROM base_roles)
        """

    def _get_permission_agg_query(self) -> str:
        """Get the query for aggregated permission counts and categories."""
        return """
            SELECT
                role_id,
                COUNT(*)::int AS permission_count,
                COALESCE(
                    jsonb_object_agg(category, cnt)
                        FILTER (WHERE category IS NOT NULL),
                    '{}'::jsonb
                ) AS permission_categories
            FROM (
                SELECT
                    role_id,
                    category,
                    COUNT(*) AS cnt
                FROM perm_flat
                GROUP BY role_id, category
            ) s
            GROUP BY role_id
        """

    async def get_roles_list_enriched(
        self,
        organization_id: str,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get paginated list of roles with user counts and permission information.

        Args:
            organization_id: Organization ID to filter roles
            search: Optional search term to filter roles by name (case-insensitive)
            limit: Maximum number of roles to return (default: 20, max: 100)
            offset: Number of roles to skip for pagination (default: 0)

        Returns:
            List of role dictionaries with detailed information
        """
        query = f"""
        WITH base_roles AS ({self._get_base_roles_query()}),
        member_counts AS ({self._get_member_counts_query()}),
        perm_flat AS ({self._get_permission_flat_query()}),
        perm_agg AS ({self._get_permission_agg_query()})
        SELECT
            br.id,
            br.name,
            br.description,
            br.is_default,
            br.created_at,
            br.updated_at,
            COALESCE(mc.user_count, 0) AS user_count,
            COALESCE(pa.permission_count, 0) AS permission_count,
            COALESCE(pa.permission_categories, '{{}}'::jsonb) AS permission_categories
        FROM base_roles br
        LEFT JOIN member_counts mc ON mc.role_id = br.id
        LEFT JOIN perm_agg pa ON pa.role_id = br.id
        ORDER BY br.updated_at DESC
        """
        return await self.db_connection.fetch(query, organization_id, search, limit, offset)

    async def get_roles_count(self, organization_id: str, search: str | None = None) -> int:
        """Get total count of roles matching search criteria.

        Args:
            organization_id: Organization ID
            search: Search query

        Returns:
            int: Total count of roles
        """
        query = "SELECT COUNT(*) FROM roles WHERE organization_id = $1 AND name != 'admin'"
        params = [organization_id]

        if search:
            query += " AND name ILIKE $2"
            params.append(f"%{search}%")

        return await self.db_connection.fetchval(query, *params)

    async def get_role_permissions(
        self, role_id: str, organization_id: str
    ) -> list[dict[str, Any]]:
        """Get all permissions assigned to a role.

        Args:
            role_id: Role ID
            organization_id: Organization ID

        Returns:
            list of permissions
        """
        query = """
            SELECT p.*
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            JOIN roles r ON rp.role_id = r.id
            WHERE r.id = $1 AND r.organization_id = $2
        """
        return await self.db_connection.fetch(query, role_id, organization_id)

    async def get_role_permission_ids(self, role_id: str, organization_id: str) -> list[str]:
        """Get all permission IDs assigned to a role.

        Args:
            role_id: Role ID
            organization_id: Organization ID

        Returns:
            list of permission IDs as strings
        """
        query = """
            SELECT rp.permission_id
            FROM role_permissions rp
            JOIN roles r ON rp.role_id = r.id
            WHERE rp.role_id = $1 AND r.organization_id = $2
        """
        rows = await self.db_connection.fetch(query, role_id, organization_id)
        return [str(row["permission_id"]) for row in rows]

    # UPDATE OPERATIONS
    async def update_role(
        self, role_id: str, organization_id: str, update_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update role information.

        Args:
            role_id: Role ID
            organization_id: Organization ID
            update_data: Dictionary of fields to update

        Returns:
            Updated role or None if not found
        """
        if not update_data:
            return None

        set_clause = ", ".join(
            [f"{field} = ${i + 2}" for i, field in enumerate(update_data.keys())]
        )
        query = f"""
            UPDATE roles
            SET {set_clause}, updated_at = NOW()
            WHERE id = $1 AND organization_id = ${len(update_data) + 2}
            RETURNING *
        """
        params = [role_id] + list(update_data.values()) + [organization_id]

        return await self.db_connection.fetchrow(query, *params)

    # DELETE OPERATIONS
    async def delete_role(self, role_id: str, organization_id: str) -> bool:
        """Delete a role from the organization.

        Args:
            role_id: Role ID to delete
            organization_id: Organization ID
        """
        query = """
            DELETE FROM roles
            WHERE id = $1 AND organization_id = $2
        """
        await self.db_connection.fetchval(query, role_id, organization_id)

    # VALIDATION OPERATIONS
    async def check_role_exists(self, role_id: str, organization_id: str) -> bool:
        """Check if role exists in organization.

        Args:
            role_id: Role ID
            organization_id: Organization ID

        Returns:
            bool: True if role exists, False otherwise
        """
        query = """
            SELECT EXISTS(
                SELECT 1 FROM roles
                WHERE id = $1 AND organization_id = $2
            )
        """
        return await self.db_connection.fetchval(query, role_id, organization_id)

    async def check_permissions_exist(
        self, permission_ids: list[str], organization_id: str
    ) -> bool:
        """Check if all permission IDs exist in the organization.

        Args:
            permission_ids: List of permission IDs
            organization_id: Organization ID

        Returns:
            bool: True if all permissions exist, False otherwise
        """
        if not permission_ids:
            return True

        query = """
            SELECT COUNT(*) = $1
            FROM permissions
            WHERE id = ANY($2::uuid[]) AND organization_id = $3
        """
        return await self.db_connection.fetchval(
            query, len(permission_ids), permission_ids, organization_id
        )

    async def check_role_name_unique(
        self, name: str, organization_id: str, exclude_role_id: str = None
    ) -> bool:
        """Check if role name is unique in the organization.

        Args:
            name: Role name to check
            organization_id: Organization ID
            exclude_role_id: Role ID to exclude from the check

        Returns:
            bool: True if name is unique, False otherwise
        """
        query = """
            SELECT COUNT(*) = 0
            FROM roles
            WHERE name = $1
            AND organization_id = $2
        """
        params = [name, organization_id]
        if exclude_role_id:
            query += " AND id != $3"
            params.append(exclude_role_id)

        return await self.db_connection.fetchval(query, *params)

    async def check_role_usage(self, role_id: str, organization_id: str) -> int:
        """Check how many users are using this role.

        Args:
            role_id: Role ID to check
            organization_id: Organization ID

        Returns:
            int: Number of users using this role
        """
        query = """
            SELECT COUNT(*)::int AS user_count
            FROM organization_members
            WHERE role_id = $1
              AND organization_id = $2
        """
        return await self.db_connection.fetchval(query, role_id, organization_id)

    async def remove_permissions_from_role(
        self, role_id: str, organization_id: str, permission_ids: list[str]
    ) -> bool:
        """Remove specific permissions from a role.

        Args:
            role_id: Role ID
            organization_id: Organization ID
            permission_ids: List of permission IDs to remove

        Returns:
            bool: True if permissions were removed successfully
        """
        query = """
            DELETE FROM role_permissions rp
            USING roles r
            WHERE rp.role_id = r.id
            AND rp.role_id = $1
            AND r.organization_id = $2
            AND rp.permission_id = ANY($3::uuid[])
            RETURNING 1
        """
        result = await self.db_connection.fetch(query, role_id, organization_id, permission_ids)
        return len(result) > 0

    async def remove_all_permissions_from_role(self, role_id: str, organization_id: str) -> bool:
        """Remove all permissions from a role.

        Args:
            role_id: Role ID
            organization_id: Organization ID

        Returns:
            bool: True if permissions were removed successfully
        """
        query = """
            DELETE FROM role_permissions rp
            USING roles r
            WHERE rp.role_id = r.id
            AND rp.role_id = $1
            AND r.organization_id = $2
            RETURNING 1
        """
        result = await self.db_connection.fetch(query, role_id, organization_id)
        return len(result) > 0

    async def get_permissions_for_roles(
        self, role_ids: list[str], organization_id: str
    ) -> list[dict]:
        """Fetch all permission mappings for the specified roles within an organization.
        This method retrieves the permission mappings for multiple roles in a single query,
        which is more efficient than querying permissions for each role individually.

        Args:
            role_ids: List of role UUIDs to fetch permissions for. Can be empty.
            organization_id: UUID of the organization to scope the permissions.

        Returns:
            A list of dictionaries where each dictionary contains:
                - role_id: UUID of the role
                - permission_id: UUID of the permission assigned to the role
        """
        query = """
            SELECT role_id, permission_id
            FROM role_permissions
            WHERE role_id = ANY($1::uuid[])
            AND organization_id = $2
        """
        return await self.db_connection.fetch(query, role_ids, organization_id)
