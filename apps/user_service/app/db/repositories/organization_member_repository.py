"""Organization Member Repository Module - AsyncPG Implementation.

This repository encapsulates all DB operations for organization_members.
"""

from typing import Any

import asyncpg

from libs.shared_utils.logger import get_logger

logger = get_logger("organization_member_repository")


class OrganizationMemberRepository:
    """Database operations class for organization members using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def add_member(
        self,
        organization_id: str,
        member_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Add a member to an organization."""
        query = """
            INSERT INTO organization_members (
                user_id,
                isometrik_user_id,
                email,
                role_id,
                role,
                status,
                organization_id,
                created_at,
                updated_at,
                joined_at,
                first_name,
                last_name,
                phone,
                timezone,
                salutation,
                invited_by
            )
            VALUES (
                $1, $2, $3, $4, $5, COALESCE($6, 'active'),
                $7, NOW(), NOW(), NOW(),
                $8, $9, $10, COALESCE($11, 'UTC'), $12, $13
            )
            RETURNING *
        """
        row = await self.db_connection.fetchrow(
            query,
            member_data.get("user_id"),
            member_data.get("isometrik_user_id"),
            member_data.get("email"),
            member_data.get("role_id"),
            member_data.get("role"),
            member_data.get("status"),
            organization_id,
            member_data.get("first_name"),
            member_data.get("last_name"),
            member_data.get("phone"),
            member_data.get("timezone"),
            member_data.get("salutation"),
            member_data.get("invited_by"),
        )
        return dict(row) if row else {}

    # READ OPERATIONS
    async def get_user_profile_by_id(
        self, user_id: str, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        """Get user profile by user ID and optionally organization ID.

        Only queries organization_members table. Role details should be fetched separately.

        Args:
            user_id: User ID
            organization_id: Optional organization ID

        Returns:
            dict containing the user profile or None if not found
        """
        where_clause = "WHERE user_id = $1"
        params: list[Any] = [user_id]

        if organization_id:
            where_clause += " AND organization_id = $2"
            params.append(organization_id)

        query = f"""
            SELECT
                id,
                user_id,
                email,
                full_name,
                first_name,
                last_name,
                avatar_url,
                salutation,
                phone,
                timezone,
                role_id,
                status,
                created_at,
                updated_at,
                last_active_at,
                joined_at,
                organization_id
            FROM organization_members
            {where_clause}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *params)

        return dict(row) if row else None

    async def get_user_role_id(
        self, user_id: str, organization_id: str | None = None
    ) -> str | None:
        """Get user's role ID.

        Args:
            user_id: User ID
            organization_id: Optional organization ID

        Returns:
            Role ID as string or None if not found
        """
        if not user_id:
            return None

        where_clause = "WHERE user_id = $1"
        params: list[Any] = [user_id]

        if organization_id:
            where_clause += " AND organization_id = $2"
            params.append(organization_id)

        query = f"""
            SELECT role_id
            FROM organization_members
            {where_clause}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *params)

        return str(row["role_id"]) if row and row.get("role_id") else None

    async def check_user_exists(self, email: str, organization_id: str) -> bool:
        """Check if user exists in organization.

        Args:
            email: Email address
            organization_id: Organization ID

        Returns:
            bool: True if user exists, False otherwise
        """
        query = """
            SELECT id
            FROM organization_members
            WHERE email = $1
            AND organization_id = $2
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, email, organization_id)
        return row is not None

    async def check_phone_exists_for_other_user(
        self, phone: str, organization_id: str, user_id: str | None = None
    ) -> bool:
        """Check if phone number exists for another user.

        Args:
            phone: Phone number
            organization_id: Organization ID
            user_id: Optional user ID to exclude from check

        Returns:
            bool: True if phone number exists for another user, False otherwise
        """
        where_clause = "WHERE phone = $1 AND organization_id = $2"
        params: list[Any] = [phone, organization_id]

        if user_id:
            where_clause += " AND user_id != $3"
            params.append(user_id)

        query = f"""
            SELECT id
            FROM organization_members
            {where_clause}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *params)

        return row is not None

    async def get_users_details_list(
        self,
        organization_id: str,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get paginated list of users with optional search."""
        where_clause = "WHERE organization_id = $1"
        params: list[Any] = [organization_id]
        param_index = 2

        if search:
            search_pattern = f"%{search}%"
            search_param = f"${param_index}"
            where_clause += f"""
                AND (
                    email ILIKE {search_param}
                    OR first_name ILIKE {search_param}
                    OR last_name ILIKE {search_param}
                    OR salutation ILIKE {search_param}
                    OR phone ILIKE {search_param}
                )
            """
            params.append(search_pattern)
            param_index += 1

        limit_param = f"${param_index}"
        offset_param = f"${param_index + 1}"
        params.extend([limit, offset])

        query = f"""
            SELECT
                id,
                user_id,
                email,
                first_name,
                last_name,
                salutation,
                phone,
                timezone,
                role_id,
                status,
                created_at,
                updated_at,
                last_active_at
            FROM organization_members
            {where_clause}
            ORDER BY created_at DESC
            LIMIT {limit_param}
            OFFSET {offset_param}
        """
        rows = await self.db_connection.fetch(query, *params)

        return [dict(row) for row in rows]

    async def get_users_total_count(
        self,
        organization_id: str,
        search: str | None = None,
    ) -> int:
        """Get total count of users matching search criteria."""
        where_clause = "WHERE organization_id = $1"
        params: list[Any] = [organization_id]

        if search:
            search_pattern = f"%{search}%"
            where_clause += """
                AND (
                    email ILIKE $2
                    OR first_name ILIKE $2
                    OR last_name ILIKE $2
                    OR salutation ILIKE $2
                    OR phone ILIKE $2
                )
            """
            params.append(search_pattern)

        query = f"""
            SELECT COUNT(*) AS count
            FROM organization_members
            {where_clause}
        """
        row = await self.db_connection.fetchrow(query, *params)

        return row["count"] if row else 0

    async def get_organization_member_status_by_email(self, email: str) -> str | None:
        """Get organization member status by email.

        Args:
            email: Email address

        Returns:
            str: Organization member status or None if not found
        """
        query = """
            SELECT status
            FROM organization_members
            WHERE email = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, email)
        return row["status"] if row else None

    # UPDATE OPERATIONS
    async def update_user_info(
        self, user_id: str, organization_id: str, update_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update user information.

        Args:
            user_id: User ID
            organization_id: Organization ID
            update_data: Update data dictionary

        Returns:
            dict containing the updated user or None if not found
        """
        if not update_data:
            return None

        # Build dynamic update query
        set_clauses = []
        params: list[Any] = []
        for field, value in update_data.items():
            if value is not None:
                set_clauses.append(f"{field} = ${len(params) + 1}")
                params.append(value)

        if not set_clauses:
            return None

        # Always update updated_at
        set_clauses.append("updated_at = NOW()")

        # Store number of update parameters before adding WHERE clause params
        num_update_params = len(params)
        params.extend([user_id, organization_id])

        query = f"""
            UPDATE organization_members
            SET {", ".join(set_clauses)}
            WHERE user_id = ${num_update_params + 1}
            AND organization_id = ${num_update_params + 2}
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, *params)
        return dict(row) if row else None

    async def update_user_activity(self, user_id: str, organization_id: str) -> None:
        """Update user's last active timestamp.

        Args:
            user_id: User ID
            organization_id: Organization ID
        """
        query = """
            UPDATE organization_members
            SET last_active_at = NOW(), updated_at = NOW()
            WHERE user_id = $1
            AND organization_id = $2
            AND status = 'active'
        """
        await self.db_connection.execute(query, user_id, organization_id)

    async def update_user_status(self, user_id: str, organization_id: str, status: str) -> bool:
        """Update user status in the organization.

        Args:
            user_id: User ID
            organization_id: Organization ID
            status: New status value (e.g., 'active', 'suspended')

        Returns:
            bool: True if user status was updated successfully, False otherwise
        """
        query = """
            UPDATE organization_members
            SET status = $1, updated_at = NOW()
            WHERE user_id = $2
            AND organization_id = $3
            RETURNING id
        """
        row = await self.db_connection.fetchrow(query, status, user_id, organization_id)
        return row is not None

    async def suspend_user(self, user_id: str, organization_id: str) -> bool:
        """Suspend a user in the organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was suspended successfully, False otherwise
        """
        return await self.update_user_status(user_id, organization_id, "suspended")

    async def revoke_suspended_user(self, user_id: str, organization_id: str) -> bool:
        """Revoke a suspended user in the organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was revoked successfully, False otherwise
        """
        return await self.update_user_status(user_id, organization_id, "active")

    async def update_user_email(self, user_id: str, organization_id: str, new_email: str) -> bool:
        """Update user's email address.

        Args:
            user_id: User ID
            organization_id: Organization ID
            new_email: New email address

        Returns:
            bool: True if user's email address was updated successfully, False otherwise
        """
        query = """
            UPDATE organization_members
            SET email = $1, updated_at = NOW()
            WHERE user_id = $2
            AND organization_id = $3
            RETURNING id
        """
        row = await self.db_connection.fetchrow(query, new_email, user_id, organization_id)
        return row is not None

    async def update_user_email_by_user_id(self, user_id: str, new_email: str) -> int:
        """Update user's email address across all organizations.

        Args:
            user_id: User ID
            new_email: New email address

        Returns:
            int: Number of rows updated
        """
        query = """
            UPDATE organization_members
            SET email = $1, updated_at = NOW()
            WHERE user_id = $2
        """
        result = await self.db_connection.execute(query, new_email, user_id)
        # asyncpg execute returns status string like "UPDATE 3", extract number
        return int(result.split()[-1]) if result else 0

    async def update_user_phone_by_user_id(self, user_id: str, new_phone: str) -> int:
        """Update user's phone number across all organizations.

        Args:
            user_id: User ID
            new_phone: New phone number

        Returns:
            int: Number of rows updated
        """
        query = """
            UPDATE organization_members
            SET phone = $1, updated_at = NOW()
            WHERE user_id = $2
        """
        result = await self.db_connection.execute(query, new_phone, user_id)
        # asyncpg execute returns status string like "UPDATE 3", extract number
        return int(result.split()[-1]) if result else 0

    # DELETE OPERATIONS
    async def delete_user(self, user_id: str, organization_id: str) -> bool:
        """Delete user from organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if user was deleted successfully, False otherwise
        """
        query = """
            DELETE FROM organization_members
            WHERE user_id = $1
            AND organization_id = $2
            RETURNING id
        """
        row = await self.db_connection.fetchrow(query, user_id, organization_id)
        return row is not None

    async def get_organization_id_by_user_id(self, user_id: str) -> str | None:
        """Get organization_id for a user from organization_members table.

        Args:
            user_id: User ID

        Returns:
            organization_id as string or None if user is not a member of any organization
        """
        query = """
            SELECT organization_id
            FROM organization_members
            WHERE user_id = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, user_id)
        return str(row["organization_id"]) if row and row.get("organization_id") else None
