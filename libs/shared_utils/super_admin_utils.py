"""Utities to handle super admin operations"""

from enum import Enum

import asyncpg

from libs.shared_utils.logger import get_logger

logger = get_logger("super_admin_utils")


class SuperAdminRole(str, Enum):
    """Enum for super admin role values."""

    SYSTEM_SUPER_ADMIN = "system_super_admin"


async def is_system_super_admin(
    current_user: dict,
) -> bool:
    """Check if the current user is a system super admin.

    Checks app_metadata.role for SuperAdminRole.SYSTEM_SUPER_ADMIN value from JWT token.

    Args:
        current_user: Decoded JWT token containing user information

    Returns:
        bool: True if user is a system super admin, False otherwise
    """
    app_metadata = current_user.get("app_metadata", {})
    return app_metadata.get("role") == SuperAdminRole.SYSTEM_SUPER_ADMIN


async def get_system_super_admin_emails(
    db_connection: asyncpg.Connection,
) -> list[str]:
    """Get the email addresses of all system super admin users.

    Queries auth.users table for all users with SuperAdminRole.SYSTEM_SUPER_ADMIN role.

    Args:
        db_connection: Database connection (asyncpg.Connection)

    Returns:
        list[str]: List of email addresses of super admins (empty list if none found)
    """
    query = f"""
        SELECT email
        FROM auth.users
        WHERE raw_app_meta_data->>'role' = '{SuperAdminRole.SYSTEM_SUPER_ADMIN.value}'
        AND email IS NOT NULL
    """
    rows = await db_connection.fetch(query)

    emails = []
    for row in rows:
        email = row.get("email")
        if email:
            emails.append(email)

    return emails
