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

    async def verify_current_password(self, user_id: str, current_password: str) -> bool:
        """Verify current password using Postgres crypt() function.

        This uses the EXACT same crypt() function that Supabase/GoTrue uses internally
        to verify passwords. No assumptions about bcrypt implementation needed.

        Args:
            user_id: User ID
            current_password: Password to verify

        Returns:
            True if password is correct, False otherwise
        """
        # Query that uses Postgres's crypt() function to verify password
        # crypt(password, hash) returns the hash if password matches
        query = """
            SELECT encrypted_password = crypt($1, encrypted_password) AS password_valid
            FROM auth.users
            WHERE id = $2
        """

        result = await self.db_connection.fetchval(
            query,
            current_password,  # $1 - the password to verify
            user_id,  # $2 - the user ID
        )

        # result will be True if password matches, False if not, None if user not found
        return result is True

    async def _verify_credentials_by_email(self, email: str, password: str) -> bool:
        """Verify email and password combination.

        This verifies that BOTH the email exists AND the password is correct.
        Used when we need to authenticate by email (e.g., check_2fa_status).

        Args:
            email: User email
            password: Password to verify

        Returns:
            True if credentials are correct, False otherwise
        """
        query = """
            SELECT encrypted_password = crypt($2, encrypted_password) AS password_valid
            FROM auth.users
            WHERE email = $1
        """

        result = await self.db_connection.fetchrow(
            query,
            email,  # $1
            password,  # $2
        )

        if not result:
            # Email not found
            return False

        if not result["password_valid"]:
            # Email found but password incorrect
            return False

        # Both email and password are correct
        return True
