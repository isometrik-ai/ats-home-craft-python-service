"""
Roles Management Utilities Module

This module provides specialized utility functions for role management operations.
These utilities handle role-specific validations, database operations, and business logic.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Role-Specific Operations Covered:
1. Permission checking
2. Role response helpers
"""

from typing import Optional

from .common_utils import (
    extract_user_context,
    require_permission,
)

# ============================================================================
# ROLE PERMISSION CHECKING
# ============================================================================


async def check_roles_manage_permission(
    current_user, action_description="access role details"
):
    """
    Extracts user context and checks if the user has 'settings.roles.manage' permission.
    """
    user_context = extract_user_context(current_user)
    await require_permission(
        permission_code="settings.roles.manage",
        user_context=user_context,
        action_description=action_description,
    )
    return user_context


async def check_roles_manage_multiple_permission(
    current_user, action_description="access role details"
):
    """
    Extracts user context and checks if the user has 'settings.roles.manage' permission.
    """
    user_context = extract_user_context(current_user)
    await require_permission(
        permission_code=["settings.roles.manage", "settings.users.manage"],
        user_context=user_context,
        action_description=action_description,
    )
    return user_context


# ============================================================================
# ROLE RESPONSE HELPERS
# ============================================================================


def build_role_filter_message(
    search: Optional[str] = None,
    role_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> str:
    """
    Build a filter description message for role API responses.

    Args:
        search (Optional[str]): Search term
        role_type (Optional[str]): Role type filter
        skip (int): Skip/offset value
        limit (int): Limit value

    Returns:
        str: Formatted filter message

    Usage:
        filter_msg = build_role_filter_message(search="admin", role_type="system")
    """
    filter_info = []

    if search:
        filter_info.append(f"search='{search}'")
    if role_type:
        filter_info.append(f"type={role_type}")
    if skip > 0:
        filter_info.append(f"skip={skip}")
    filter_info.append(f"limit={limit}")

    filter_text = f" with filters: {', '.join(filter_info)}" if filter_info else ""
    return f"Roles retrieved successfully{filter_text}"