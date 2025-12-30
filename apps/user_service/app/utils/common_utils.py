"""Common API Utilities Module.

This module provides reusable utility functions for FastAPI endpoints that are
shared across all API modules. These utilities eliminate code duplication and
standardize common operations.
"""

import json
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from fastapi import HTTPException, Request

from apps.user_service.app.schemas.admin_access_management import PermissionItem
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_user_profile_by_id,
)
from libs.shared_db.supabase_db.admin_operations.user import (
    get_user_by_id,
    update_metadata_of_user,
)
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_middleware.jwt_auth import check_user_access_async
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("common_utils")

# ============================================================================
# DATA CLASSES
# ============================================================================


@dataclass
class UserContext:
    """User context extracted and validated from JWT token."""

    user_id: str
    email: str
    organization_id: str | None = None
    user_type: str | None = None


@dataclass
class PerformanceTimer:
    """Performance timing context manager and utility."""

    operation_name: str
    start_time: float | None = None

    def __post_init__(self):
        self.start_time = time.time()

    def checkpoint(self) -> float:
        """Log a timing checkpoint and return elapsed time in ms."""
        assert self.start_time is not None, "Timer not initialized"
        elapsed = (time.time() - self.start_time) * 1000

        return elapsed

    def total_time(self) -> float:
        """Return total elapsed time in ms."""
        assert self.start_time is not None, "Timer not initialized"
        elapsed = (time.time() - self.start_time) * 1000
        return elapsed


# ============================================================================
# PERMISSION UTILITIES
# ============================================================================


def format_permissions_data(
    permissions_data: list[dict[str, Any]],
) -> list[PermissionItem]:
    """Format permissions data into PermissionItem objects.

    Args:
        permissions_data (list[dict[str, Any]]): Permissions data

    Returns:
        list[PermissionItem]: Formatted permissions data
    """
    if permissions_data:
        return [
            PermissionItem(
                id=str(permission["id"]),
                name=permission["name"],
                code=permission["code"],
                category=permission["category"],
                description=permission["description"],
                created_at=format_iso_datetime(permission["created_at"]) or "",
            )
            for permission in permissions_data
        ]
    return []


# ============================================================================
# USER CONTEXT EXTRACTION
# ============================================================================


async def extract_user_context(current_user: dict) -> UserContext:
    """Extract and validate user context from JWT token.

    This function performs comprehensive validation of JWT token data including:
    - User ID (sub) validation
    - Organization ID extraction from user_metadata
    - Email validation
    - Presence checks for all required fields

    Args:
        current_user (dict): Decoded JWT token containing user information

    Returns:
        UserContext: Validated user context object

    Raises:
        ValidationException: If missing or invalid token data
        HTTPException: If HTTP error occurs
        InternalServerErrorException: If internal error occurs

    Usage:
        user_context = await extract_user_context(current_user)
    """
    try:
        user_id = current_user.get("sub")
        user_metadata = current_user.get("user_metadata", {})
        organization_id = user_metadata.get("organization_id", None)
        email = current_user.get("email")
        user_type = user_metadata.get("type", None)  # Extract type from JWT

        # Validation: Ensure required fields are present
        if not user_id:
            raise ValidationException(
                message_key="errors.invalid_token",
                custom_code=CustomStatusCode.INVALID_DATA,
                params={"error": "user ID not found"},
            )

        if not email:
            raise ValidationException(
                message_key="errors.invalid_token",
                custom_code=CustomStatusCode.INVALID_DATA,
                params={"error": "email not found"},
            )

        if not organization_id:
            user_data = await get_user_by_id(user_id)
            organization_id = user_data.user.user_metadata.get("organization_id", None)

            # If organization_id is still None, try to get it from organization_members table
            if not organization_id:
                # Query organization_members table to find user's organization
                supabase = await get_supabase_admin_client()
                result = (
                    await supabase.table("organization_members")
                    .select("organization_id")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )

                if result.data and len(result.data) > 0:
                    # Use the first organization found
                    organization_id = result.data[0]["organization_id"]

                    # Update user metadata with the found organization_id
                    await update_metadata_of_user(
                        user_id,
                        {
                            "organization_id": organization_id,
                            "type": "organization_member",
                        },
                    )
                else:
                    # Organization ID not found, but allow user to proceed
                    # This is normal for new users creating their first organization
                    organization_id = None

        return UserContext(
            user_id=user_id,
            email=email,
            organization_id=organization_id,
            user_type=user_type,
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error extracting user context: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


# ============================================================================
# PERMISSION CHECKING
# ============================================================================


async def require_permission(
    permission_code: str | list[str],
    user_context: UserContext,
    organization_id: str | None = None,
) -> None:
    """Check user permission with performance timing and detailed error handling.

    This function performs async permission checking with:
    - Performance timing measurement
    - Detailed error messages
    - Standardized permission denied responses

    Args:
        permission_code (str): Permission code to check (e.g., "settings.roles.manage")
        user_context (UserContext): Validated user context
        organization_id (str): Organization ID

    Raises:
        HTTPException: If HTTP error occurs
        InternalServerErrorException: If internal error occurs

    Usage:
        await require_permission("settings.roles.manage", user_context, organization_id)
    """
    try:
        if isinstance(permission_code, str):
            permission_codes = [permission_code]
        else:
            permission_codes = permission_code

        has_permission = await check_user_access_async(
            permission_code=permission_codes,
            user_id=user_context.user_id,
            organisation_id=organization_id,
        )

        if not has_permission:
            raise ForbiddenException(
                message_key="errors.insufficient_permissions",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

    except HTTPException as error:
        raise error
    except Exception as error:
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


# ============================================================================
# PERMISSION CHECKING
# ============================================================================


async def check_permissions(
    current_user: dict,
    permission_codes: list[str] | str,
    organization_id: str | None = None,
) -> UserContext:
    """Extracts user context and checks if the user has the given permission.

    Args:
        current_user (dict): Current user data
        permission_codes (list[str] | str): Permission codes to check
        organization_id (str | None): Organization ID

    Returns:
        UserContext: User context
    """
    user_context = await extract_user_context(current_user)
    await require_permission(
        permission_code=permission_codes,
        user_context=user_context,
        organization_id=organization_id if organization_id else user_context.organization_id,
    )
    return user_context


# ============================================================================
# UUID VALIDATION
# ============================================================================


def validate_uuid_format(value: str, field_name: str = "ID") -> None:
    """Validate UUID format and raise HTTPException if invalid.

    Args:
        value (str): UUID string to validate
        field_name (str): Field name for error message (default: "ID")

    Raises:
        ValidationException: If invalid UUID format

    Usage:
        validate_uuid_format(role_id, "role ID")
        validate_uuid_format(user_id, "user ID")
    """
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ValidationException(
            message_key="errors.invalid_uuid_format",
            custom_code=CustomStatusCode.INVALID_DATA,
            params={"field_name": field_name},
        ) from exc


# ============================================================================
# EXCEPTION HANDLING DECORATORS
# ============================================================================


def handle_api_exceptions(operation_name: str):
    """Decorator for standardized exception handling in API endpoints.

    This decorator provides:
    - HTTPException pass-through (preserves status codes and messages)
    - Generic exception catching with operation context
    - Standardized error logging
    - Internal server error responses for unexpected exceptions

    Args:
        operation_name (str): Operation description for error logging

    Usage:
        @handle_api_exceptions("get roles")
        async def get_roles_endpoint(...):
            # endpoint implementation
    """

    def decorator(func: Callable[[Any], Any]):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except ValueError as error:
                raise ValidationException(
                    message_key="errors.value_error",
                    custom_code=CustomStatusCode.INVALID_DATA,
                    params={"operation_name": operation_name, "error": str(error)},
                ) from error
            except Exception as error:
                traceback.print_exc()
                logger.error("Error in %s: %s", operation_name, str(error))
                raise InternalServerErrorException(
                    message_key="errors.internal_server_error",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                ) from error

        return wrapper

    return decorator


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def format_iso_datetime(dt: Any) -> str | None:
    """Format datetime or ISO-string to ISO string, handling None values.

    Handles both datetime objects and ISO string inputs.

    Args:
        dt (Any): Datetime object or ISO string to format

    Returns:
        str | None: Formatted ISO string or None if input is None/empty
    """
    if not dt:
        return None

    # If it's already a string, return as-is (assuming it's already in ISO format)
    if isinstance(dt, str):
        return dt

    # If it's a datetime object, convert to ISO string
    if hasattr(dt, "isoformat"):
        return dt.isoformat()

    # Fallback: convert to string
    return str(dt)


def safe_json_loads(json_str: str | None, default: Any = None) -> Any:
    """Safely parse JSON string with fallback.

    Args:
        json_str (str | None): JSON string to parse
        default (Any): Default value if parsing fails

    Returns:
        Any: Parsed JSON or default value

    Usage:
        categories = safe_json_loads(role["permission_categories"], {})
    """
    if not json_str:
        return default

    try:
        if isinstance(json_str, str):
            return json.loads(json_str)
        return json_str
    except (json.JSONDecodeError, TypeError):
        return default


# ============================================================================
# VALIDATION CONSTANTS
# ============================================================================

# Common validation patterns
ROLE_TYPES = ["system", "custom"]
ORG_STATUSES = ["active", "suspended", "trial"]
USER_STATUSES = ["active", "inactive", "pending", "invited"]

# Default pagination limits
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# UUID validation regex (if needed for additional validation)
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


async def get_user_in_organization(user_id: str, organization_id: str):
    """Fetch user profile data and raise 404 if not found in organization.

    This utility function handles the common pattern of:
    1. Fetching user profile data
    2. Checking if user exists in organization
    3. Raising 404 if user not found

    Args:
        user_id (str): User ID to fetch
        organization_id (str): Organization ID for filtering

    Returns:
        Record: User profile data

    Raises:
        NotFoundException: If user not found in organization
    """

    current_user_data = await get_user_profile_by_id(user_id, organization_id)
    if not current_user_data:
        raise NotFoundException(
            message_key="errors.user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            params={"user_id": user_id},
        )

    return current_user_data


def set_audit_old_data_from_user(request: Request, current_user_data: dict) -> None:
    """Set audit old data from user profile information.

    This utility function handles the common pattern of setting
    request.state.raw_audit_old_data with user profile information
    for audit comparison.

    Args:
        request (Request): FastAPI request object for setting audit state
        current_user_data (dict): User profile data from database
    """
    audit_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": current_user_data["full_name"],
        "first_name": current_user_data.get("first_name"),
        "last_name": current_user_data.get("last_name"),
        "phone": current_user_data.get("phone"),
        "timezone": current_user_data.get("timezone"),
        "avatar_url": current_user_data.get("avatar_url"),
        "status": current_user_data.get("status"),
        "role_id": str(current_user_data.get("role_id", "")),
        "organization_id": str(current_user_data["organization_id"]),
    }

    # Add optional timestamp fields if they exist
    if current_user_data.get("joined_at"):
        audit_data["joined_at"] = format_iso_datetime(current_user_data["joined_at"])
    if current_user_data.get("last_active_at"):
        audit_data["last_active_at"] = format_iso_datetime(current_user_data["last_active_at"])

    request.state.raw_audit_old_data = audit_data
