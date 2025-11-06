"""
Organisation Management Utilities Module

This module provides specialized utility functions for organisation management operations.
These utilities handle organisation-specific validations, database operations, and business logic.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Organisation-Specific Operations Covered:
1. Organisation existence checking
2. Organisation slug uniqueness validation
3. Organisation status validation
4. Organisation creation helpers
5. Organisation query building
6. Default permissions and roles setup
"""

from typing import Optional, Any, Dict

from fastapi import HTTPException, status
from postgrest import APIError

# Local imports
from apps.user_service.app.dependencies.common_utils import ORG_STATUSES
from apps.user_service.app.dependencies.logger import get_logger

from libs.shared_db.supabase_db.admin_operations.user_utility_admin import log_exception
from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    create_new_organisation,
    create_super_admin_role,
    create_default_permissions_for_organisation,
    assign_all_permissions_to_role,
    add_member_to_organisation,
)

logger = get_logger("organisation_utils")


def validate_organisation_status(org_status: str) -> None:
    """
    Validate organisation status against allowed values.

    Args:
        org_status (str): Organisation status to validate

    Raises:
        HTTPException: 422 for invalid organisation status

    Usage:
        validate_organisation_status(body.status)
    """
    if org_status not in ORG_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Status must be one of: {', '.join(ORG_STATUSES)}",
        )


def validate_organisation_name_filter(name: str) -> str:
    """
    Validate and sanitize organisation name filter.

    Args:
        name (str): Organisation name filter to validate

    Returns:
        str: Sanitized name filter

    Raises:
        HTTPException: 422 for invalid name filter

    Usage:
        clean_name = validate_organisation_name_filter(name)
    """
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name filter cannot be empty",
        )

    name = name.strip()
    if len(name) < 1 or len(name) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name filter must be between 1 and 255 characters",
        )

    return name


# ============================================================================
# ORGANISATION RESPONSE HELPERS
# ============================================================================


def build_organisation_filter_message(
    name: Optional[str] = None,
    org_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """
    Build a filter description message for organisation API responses.

    Args:
        name (Optional[str]): Search term
        org_status (Optional[str]): Organisation status filter
        page (int): Page number
        page_size (int): Page size

    Returns:
        str: Formatted filter message

    Usage:
        filter_msg = build_organisation_filter_message(name="acme", org_status="active")
    """
    filter_info = []

    if name:
        filter_info.append(f"name='{name}'")
    if org_status:
        filter_info.append(f"status='{org_status}'")
    if page > 1:
        filter_info.append(f"page={page}")
    filter_info.append(f"page_size={page_size}")

    filter_text = f" with filters: {', '.join(filter_info)}" if filter_info else ""
    return f"All organizations retrieved successfully{filter_text}"



async def create_organisation_with_super_admin(org_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new organisation."""
    try:
        await create_new_organisation(org_data)

        # Create Super Admin role
        super_admin_role_result = await create_super_admin_role(org_data["organization_id"])
        super_admin_role_id = super_admin_role_result['id']

        # Create default permissions
        await create_default_permissions_for_organisation(
            org_data["organization_id"]
        )

        # Assign all permissions to Super Admin role
        await assign_all_permissions_to_role(
            super_admin_role_id, org_data["organization_id"]
        )

        # Create organization member
        await add_member_to_organisation(org_data["organization_id"], {
            "user_id": org_data["user_id"],
            "email": org_data["email"],
            "first_name": org_data.get("first_name", None),
            "last_name": org_data.get("last_name", None),
            "phone": org_data.get("phone", None),
            "timezone": org_data.get("timezone", "UTC"),
            "role_id": super_admin_role_id,
            "status": "active",
        })

    except Exception as db_error:
        log_exception()
        
        # Check for duplicate key errors (e.g., duplicate slug)
        error_message = str(db_error).lower()
        error_code = None
        
        # Check if it's an APIError with code 23505 (PostgreSQL duplicate key violation)
        if isinstance(db_error, APIError):
            # APIError might have message as dict or string
            error_details = getattr(db_error, 'message', {})
            if isinstance(error_details, dict):
                error_code = error_details.get('code')
                error_message = str(error_details.get('message', '')).lower()
            elif isinstance(error_details, str):
                error_message = error_details.lower()
            
            # Also check args and other attributes
            if hasattr(db_error, 'args') and db_error.args:
                for arg in db_error.args:
                    if isinstance(arg, dict):
                        error_code = arg.get('code') or error_code
                        if 'message' in arg:
                            error_message += " " + str(arg.get('message', '')).lower()
        
        # Check error message string for duplicate key patterns
        error_str = str(db_error).lower()
        if hasattr(db_error, '__dict__'):
            error_str += " " + str(db_error.__dict__).lower()
        
        # Check for duplicate key violations in error message or code
        if (
            error_code == '23505'
            or str(error_code) == '23505'
            or 'duplicate key' in error_message
            or 'duplicate key' in error_str
            or 'organizations_slug_key' in error_message
            or 'organizations_slug_key' in error_str
            or ('slug' in error_message and 'duplicate' in error_message)
            or ('slug' in error_str and 'duplicate' in error_str)
        ):
            # Try to delete the Supabase user if organization creation failed due to duplicate slug
            try:
                await delete_auth_user(org_data["user_id"])
            except Exception:
                # Ignore cleanup errors for duplicate slug case
                pass
            
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Organisation slug already exists. Please choose a different name.",
            ) from db_error
        
        # Check for Row Level Security (RLS) policy violations
        if (
            error_code == '42501'
            or str(error_code) == '42501'
            or 'row-level security policy' in error_message
            or 'row-level security policy' in error_str
            or 'violates row-level security' in error_message
            or 'violates row-level security' in error_str
        ):
            # Try to delete the Supabase user if organization creation failed due to RLS
            try:
                await delete_auth_user(org_data["user_id"])
            except Exception:
                # Ignore cleanup errors for RLS case
                pass
            
            logger.error(
                "RLS policy violation during organization creation - Organization ID: %s, User ID: %s, Error: %s",
                org_data.get("organization_id"), org_data.get("user_id"), str(db_error)
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database security policy error. Please contact support. Error: Row-level security policy violation.",
            ) from db_error

        # Try to delete the Supabase user if database transaction fails
        try:
            await delete_auth_user(org_data["user_id"])
        except Exception as cleanup_error:  # noqa: W0718
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create account. Please try again.",
            ) from cleanup_error

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account. Please try again.",
        ) from db_error
