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
    create_isometrik_application,
    update_isometrik_application,
    create_isometrik_chat_user
)
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    update_organisation_settings,
)

FAIL_CREATE_ACCOUNT = "Failed to create account. Please try again."

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


async def create_organisation_with_super_admin(org_data: Dict[str, Any]) -> None:
    """
    Create a new organisation with super admin role and default permissions.

    This function performs a multi-step operation:
    1. Creates the organization record
    2. Creates a super admin role for the organization
    3. Creates default permissions for the organization
    4. Assigns all permissions to the super admin role
    5. Adds the user as an organization member with super admin role

    Args:
        org_data: Dictionary containing organization and user data

    Returns:
        None
    Raises:
        HTTPException:
            - 409: If organization slug already exists (duplicate key violation)
            - 500: For RLS policy violations or other database errors
    """
    try:
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

        # Step 6: Create Isometrik application (non-blocking - log errors but don't fail org creation)
        try:
            isometrik_response = await create_isometrik_application(
                organization_name=org_data["name"],
                organization_id=org_data["organization_id"],
                product_types=["chat", "video"],
                plan="basic"
            )
            
            # Store Isometrik response data in organization settings
            if isometrik_response and isometrik_response.get("data"):
                isometrik_data = isometrik_response["data"]
                # Get current settings and update with Isometrik data
                current_org = await get_organisation_details_by_id(org_data["organization_id"])
                if current_org and current_org.get("settings"):
                    current_settings = current_org["settings"]
                else:
                    current_settings = {}
                
                # Store Isometrik data in settings
                current_settings["isometrik"] = {
                    "projectId": isometrik_data.get("projectId"),
                    "keysetId": isometrik_data.get("keysetId"),
                    "keysetName": isometrik_data.get("keysetName"),
                    "appSecret": isometrik_data.get("appSecret"),
                    "userSecret": isometrik_data.get("userSecret"),
                    "licenseKey": isometrik_data.get("licenseKey"),
                    "publishKey": isometrik_data.get("publishKey"),
                    "subscribeKey": isometrik_data.get("subscribeKey"),
                    "accountId": isometrik_data.get("accountId", org_data["organization_id"]),
                }
                
                # Update organization settings with Isometrik data
                await update_organisation_settings(org_data["organization_id"], current_settings)
                logger.info(
                    "Successfully created and stored Isometrik application for organization: %s (projectId: %s)",
                    org_data["organization_id"],
                    isometrik_data.get("projectId")
                )
                
                # Step 7: Create Isometrik chat user (non-blocking - log errors but don't fail org creation)
                try:
                    chat_user_response = await create_isometrik_chat_user(
                        organization_id=org_data["organization_id"],
                        first_name=org_data.get("first_name"),
                        last_name=org_data.get("last_name"),
                        email=org_data["email"],
                        isometrik_credentials={
                            "userSecret": isometrik_data.get("userSecret"),
                            "licenseKey": isometrik_data.get("licenseKey"),
                            "appSecret": isometrik_data.get("appSecret")
                        }
                    )
                    
                    # Store chat user data in settings
                    if chat_user_response and chat_user_response.get("data"):
                        chat_user_data = chat_user_response["data"]
                        # Get updated settings
                        updated_org = await get_organisation_details_by_id(org_data["organization_id"])
                        if updated_org and updated_org.get("settings"):
                            updated_settings = updated_org["settings"]
                        else:
                            updated_settings = current_settings
                        
                        # Add chat user data to Isometrik settings
                        if "isometrik" not in updated_settings:
                            updated_settings["isometrik"] = {}
                        
                        updated_settings["isometrik"]["isometrikUserId"] = chat_user_data.get("userId")
                        updated_settings["isometrik"]["isometrikRes"] = chat_user_data
                        updated_settings["isometrik"]["isometrikToken"] = chat_user_data.get("userToken")
                        
                        # Update organization settings with chat user data
                        await update_organisation_settings(org_data["organization_id"], updated_settings)
                        logger.info(
                            "Successfully created and stored Isometrik chat user for organization: %s (userId: %s)",
                            org_data["organization_id"],
                            chat_user_data.get("userId")
                        )
                    else:
                        logger.warning(
                            "Isometrik chat user response missing data for organization: %s",
                            org_data["organization_id"]
                        )
                except Exception as chat_user_error:
                    # Log error but don't fail organization creation
                    logger.warning(
                        "Failed to create Isometrik chat user for organization %s: %s",
                        org_data["organization_id"],
                        str(chat_user_error)
                    )
                    # Continue with organization creation even if chat user creation fails
            else:
                logger.warning(
                    "Isometrik response missing data for organization: %s",
                    org_data["organization_id"]
                )
        except Exception as isometrik_error:
            # Log error but don't fail organization creation
            logger.warning(
                "Failed to create Isometrik application for organization %s: %s",
                org_data["organization_id"],
                str(isometrik_error)
            )
            # Continue with organization creation even if Isometrik fails

    except HTTPException:
        # Re-raise HTTP exceptions as-is (preserves status codes)
        raise
    except (APIError, SupabaseAPIError) as api_error:
        # Handle Supabase API errors (PostgreSQL errors wrapped by Supabase).
        # These can include:
        # - Duplicate key violations (error code 23505)
        # - RLS policy violations (error code 42501)
        # - Other database constraint violations
        log_exception()

        # Extract error code and message from APIError
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

        # Build comprehensive error message for pattern matching
        error_str = str(api_error).lower()
        if hasattr(api_error, '__dict__'):
            error_str += " " + str(api_error.__dict__).lower()

        # Check for duplicate key violation (PostgreSQL error code 23505)
        # This typically occurs when organization slug already exists
        if any([
            error_code == '23505',
            str(error_code) == '23505',
            'duplicate key' in error_message,
            'duplicate key' in error_str,
            'organizations_slug_key' in error_message,
            'organizations_slug_key' in error_str,
            all(word in error_message for word in ('slug', 'duplicate')),
            all(word in error_str for word in ('slug', 'duplicate'))
        ]):
            logger.warning(
                "Duplicate organization slug detected - Organization ID: %s, Slug: %s",
                org_data.get("organization_id"), org_data.get("slug")
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Organisation slug already exists. Please choose a different name.",
            ) from api_error

        # Check for Row Level Security (RLS) policy violation (PostgreSQL error code 42501)
        if any([
            str(error_code) == '42501',
            'row-level security policy' in error_message,
            'row-level security policy' in error_str,
            'violates row-level security' in error_message,
            'violates row-level security' in error_str
        ]):
            logger.error(
                "RLS policy violation during organization creation"
                "- Organization ID: %s, User ID: %s, Error: %s",
                org_data.get("organization_id"), org_data.get("user_id"), str(api_error)
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database security policy error. Please contact support."
                " Error: Row-level security policy violation.",
            ) from api_error

        # Other API errors - generic database error
        logger.error(
            "Database API error during organization creation - Organization ID: %s, Error: %s",
            org_data.get("organization_id"), str(api_error)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=FAIL_CREATE_ACCOUNT,
        ) from api_error

    except DatabaseOperationError as db_error:
        # Handle other database operation errors (network, validation, etc.)
        # These are wrapped by the handle_database_errors decorator.
        log_exception()
        logger.error(
            "Database operation error during organization creation "
            "- Organization ID: %s, Error: %s",
            org_data.get("organization_id"), str(db_error)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=FAIL_CREATE_ACCOUNT,
        ) from db_error

    except Exception as unexpected_error:
        # Handle any other unexpected errors.
        # This should rarely happen as database operations are wrapped.
        log_exception()
        logger.error(
            "Unexpected error during organization creation - Organization ID: %s, Error: %s",
            org_data.get("organization_id"), str(unexpected_error),
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=FAIL_CREATE_ACCOUNT,
        ) from unexpected_error
