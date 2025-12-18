"""User Database Repository Module - AsyncPG Implementation

This module contains all User-related database operations using asyncpg.
All SQL queries for user management are centralized here with proper
transaction handling and efficient batch operations.
"""

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("user_repository")


class UserRepository:
    """Database operations class for user management using asyncpg.
    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection):
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    def _normalize_phone(phone: str) -> str:
        """Normalize phone number by removing '+' sign for comparison.
        Supabase phone field may not preserve '+' sign, so we normalize for matching.

        Args:
            phone: Phone number to normalize

        Returns:
            Normalized phone number (without '+')
        """
        if not phone:
            return phone
        # Remove '+' sign if present for comparison
        return phone.lstrip("+")

    async def get_organization_member_status_by_email(self, email: str) -> str | None:
        """Get organization member status by email from the 'organization_members' table.

        Args:
            email: The email of the user.

        Returns:
            str | None: Member status if found, else None.
        """
        query = """
            SELECT status
            FROM organization_members
            WHERE email = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, email)

        if row:
            return row["status"]
        return None

    async def get_auth_user_by_email(self, email: str) -> dict | None:
        """Get a user from the 'auth.users' table by email.

        Args:
            email: The email of the user.

        Returns:
            dict | None: User data as dictionary if found, else None.
        """
        query = """
            SELECT *
            FROM auth.users
            WHERE email = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, email)

        if row:
            # Convert asyncpg Record to standard dict
            return dict(row)

        logger.warning("User with email '%s' not found.", email)
        return None

    async def phone_exists_for_other_user(self, phone: str, user_id: str | None = None) -> bool:
        """Check if a phone number already exists in auth.users.
        Exact match, no normalization.

        Args:
            phone: Phone number to check
            user_id: Optional user ID to exclude from check. If None, check all users.

        Returns:
            True if phone exists, False otherwise
        """
        query = "SELECT 1 FROM auth.users WHERE phone = $1"
        params = [phone]

        if user_id:
            query += " AND id != $2"
            params.append(user_id)

        query += " LIMIT 1"

        row = await self.db_connection.fetchrow(query, *params)
        return bool(row)
