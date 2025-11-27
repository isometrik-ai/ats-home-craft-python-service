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

from libs.shared_db.supabase_db.admin_operations.user_utility_admin import log_exception
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    create_new_organisation,
    create_super_admin_role,
    create_default_permissions_for_organisation,
    assign_all_permissions_to_role,
    add_member_to_organisation,
    update_organisation_settings,
    get_organisation_details_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    DatabaseOperationError,
    SupabaseAPIError,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_application
)

FAIL_CREATE_ACCOUNT = "Failed to create account. Please try again."


async def _save_isometrik_application_data(
    organization_id: str,
    isometrik_data: Dict[str, Any]
) -> None:
    """Save Isometrik application data to organization settings."""
    current_org = await get_organisation_details_by_id(organization_id)
    if current_org and current_org.get("settings"):
        current_settings = current_org["settings"]
    else:
        current_settings = {}
    
    current_settings["isometrik_application_details"] = isometrik_data
    await update_organisation_settings(organization_id, current_settings)


async def _create_isometrik_application_if_enabled(
    organization_name: str
) -> Optional[Dict[str, Any]]:
    """Create Isometrik application if enabled, otherwise return None."""
    from libs.shared_utils.isometrik_service import (
        is_isometrik_enabled,
        create_isometrik_application,
        IsometrikAPIError,
        IsometrikConnectionError
    )
    
    if not is_isometrik_enabled():
        return None
    
    try:
        isometrik_response = await create_isometrik_application(
            organization_name=organization_name,
            product_types=["chat", "video"],
            plan="basic"
        )
        
        if not isometrik_response or not isometrik_response.get("data"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create Isometrik application: Invalid response from Isometrik API"
            )
        return isometrik_response
    except (IsometrikAPIError, IsometrikConnectionError) as isometrik_error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Isometrik application: {str(isometrik_error)}"
        ) from isometrik_error


def _extract_error_details(api_error: Any) -> tuple[Optional[str], str, str]:
    """Extract error code, message, and full error string from API error."""
    error_code = None
    error_message = ""
    
    match api_error:
        case APIError():
            error_details = getattr(api_error, 'message', {})
            match error_details:
                case dict():
                    error_code = error_details.get('code')
                    error_message = str(error_details.get('message', '')).lower()
                case str():
                    error_message = error_details.lower()
        case SupabaseAPIError():
            original_error = getattr(api_error, '__cause__', None)
            match original_error:
                case APIError():
                    error_details = getattr(original_error, 'message', {})
                    match error_details:
                        case dict():
                            error_code = error_details.get('code')
                            error_message = str(error_details.get('message', '')).lower()
                        case str():
                            error_message = error_details.lower()
    
    error_str = str(api_error).lower()
    if hasattr(api_error, '__dict__'):
        error_str += " " + str(api_error.__dict__).lower()
    
    return error_code, error_message, error_str


def _is_duplicate_key_violation(
    error_code: Optional[str],
    error_message: str,
    error_str: str
) -> bool:
    """Check if error is a duplicate key violation."""
    return any([
        error_code == '23505',
        str(error_code) == '23505',
        'duplicate key' in error_message,
        'duplicate key' in error_str,
        'organizations_slug_key' in error_message,
        'organizations_slug_key' in error_str,
        all(word in error_message for word in ('slug', 'duplicate')),
        all(word in error_str for word in ('slug', 'duplicate'))
    ])


def _is_rls_violation(
    error_code: Optional[str],
    error_message: str,
    error_str: str
) -> bool:
    """Check if error is a Row Level Security policy violation."""
    return any([
        str(error_code) == '42501',
        'row-level security policy' in error_message,
        'row-level security policy' in error_str,
        'violates row-level security' in error_message,
        'violates row-level security' in error_str
    ])


def _handle_api_error(api_error: Any) -> None:
    """Handle API errors and raise appropriate HTTP exceptions."""
    log_exception()
    
    error_code, error_message, error_str = _extract_error_details(api_error)
    
    if _is_duplicate_key_violation(error_code, error_message, error_str):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation slug already exists. Please choose a different name.",
        ) from api_error
    
    if _is_rls_violation(error_code, error_message, error_str):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database security policy error. Please contact support."
            " Error: Row-level security policy violation.",
        ) from api_error
    
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=FAIL_CREATE_ACCOUNT,
    ) from api_error




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


async def create_organisation_with_super_admin(org_data: Dict[str, Any]) -> None:
    """
    Create a new organisation with super admin role and default permissions.

    This function performs a multi-step operation:
    1. If Isometrik is enabled, create Isometrik application first (must succeed)
    2. Creates the organization record
    3. Creates a super admin role for the organization
    4. Creates default permissions for the organization
    5. Assigns all permissions to the super admin role
    6. Adds the user as an organization member with super admin role
    7. If Isometrik is enabled and application was created, save Isometrik data

    Args:
        org_data: Dictionary containing organization and user data

    Returns:
        None
    Raises:
        HTTPException:
            - 409: If organization slug already exists (duplicate key violation)
            - 500: For RLS policy violations or other database errors
            - 500: If Isometrik is enabled and application creation fails
    """
    try:
        # Step 0: Create Isometrik application first if enabled (must succeed before org creation)
        organization_name = org_data.get("name", "Unknown Organization")
        isometrik_response = await _create_isometrik_application_if_enabled(organization_name)

        # Step 1: Create the organization record
        await create_new_organisation(org_data)

        # Step 2: Create Super Admin role
        super_admin_role_result = await create_super_admin_role(org_data["organization_id"])
        super_admin_role_id = super_admin_role_result['id']

        # Step 3: Create default permissions
        await create_default_permissions_for_organisation(org_data["organization_id"])

        # Step 4: Assign all permissions to Super Admin role
        await assign_all_permissions_to_role(super_admin_role_id, org_data["organization_id"])

        # Step 5: Add user as organization member
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

        # Step 6: Save Isometrik application data if it was created successfully
        if isometrik_response and isometrik_response.get("data"):
            isometrik_data = isometrik_response["data"]
            await _save_isometrik_application_data(org_data["organization_id"], isometrik_data)

    except HTTPException:
        # Re-raise HTTP exceptions as-is (preserves status codes)
        raise
    except (APIError, SupabaseAPIError) as api_error:
        _handle_api_error(api_error)
    except DatabaseOperationError as db_error:
        # Handle other database operation errors (network, validation, etc.)
        log_exception()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=FAIL_CREATE_ACCOUNT,
        ) from db_error
    except Exception as unexpected_error:
        # Handle any other unexpected errors.
        log_exception()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=FAIL_CREATE_ACCOUNT,
        ) from unexpected_error
