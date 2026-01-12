"""Common API Utilities Module.

This module provides reusable utility functions for FastAPI endpoints that are
shared across all API modules. These utilities eliminate code duplication and
standardize common operations.
"""

import hashlib
import json
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

import asyncpg
from fastapi import HTTPException, Request

from apps.user_service.app.db.repositories import (
    OrganizationMemberRepository,
    OrganizationRepository,
)
from apps.user_service.app.schemas.admin_access_management import PermissionItem
from libs.shared_middleware.jwt_auth import check_user_access_async
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import is_system_super_admin

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


async def extract_user_context(
    current_user: dict, db_connection: asyncpg.Connection
) -> UserContext:
    """Extract and validate user context from JWT token.

    This function performs comprehensive validation of JWT token data including:
    - User ID (sub) validation
    - Organization ID extraction from user_metadata
    - Email validation
    - Presence checks for all required fields

    Args:
        current_user (dict): Decoded JWT token containing user information
        db_connection (asyncpg.Connection): Database connection (required)

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
        email = current_user.get("email")

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

        # Get organization_id from organization_members table
        org_member_repo = OrganizationMemberRepository(db_connection)
        organization_id = await org_member_repo.get_organization_id_by_user_id(user_id)

        user_type = "organization_member" if organization_id else None

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
    db_connection: asyncpg.Connection,
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
        db_connection (asyncpg.Connection): Database connection
        organization_id (str): Organization ID

    Raises:
        HTTPException: If HTTP error occurs
        InternalServerErrorException: If internal error occurs

    Usage:
        await require_permission(
            "settings.roles.manage",
            user_context,
            db_connection,
            organization_id
        )
    """
    try:
        if isinstance(permission_code, str):
            permission_codes = [permission_code]
        else:
            permission_codes = permission_code

        has_permission = await check_user_access_async(
            permission_code=permission_codes,
            user_id=user_context.user_id,
            organization_id=organization_id,
            db_connection=db_connection,
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
    db_connection: asyncpg.Connection,
    permission_codes: list[str] | str,
    organization_id: str | None = None,
) -> UserContext:
    """Extracts user context and checks if the user has the given permission.

    Args:
        current_user (dict): Current user data
        db_connection (asyncpg.Connection): Database connection
        permission_codes (list[str] | str): Permission codes to check
        organization_id (str | None): Organization ID

    Returns:
        UserContext: User context
    """
    user_context = await extract_user_context(current_user, db_connection)
    await require_permission(
        permission_code=permission_codes,
        user_context=user_context,
        db_connection=db_connection,
        organization_id=organization_id if organization_id else user_context.organization_id,
    )
    return user_context


async def require_organization_creator(
    user_context: UserContext,
    organization_id: str,
    db_connection: asyncpg.Connection,
) -> None:
    """Check if the user is the organization creator.

    Args:
        user_context (UserContext): Validated user context
        organization_id (str): Organization ID to check
        db_connection (asyncpg.Connection): Database connection

    Raises:
        ForbiddenException: If user is not the organization creator
        NotFoundException: If organization is not found
    """
    org_repo = OrganizationRepository(db_connection)
    is_owner = await org_repo.is_user_organization_owner(organization_id, user_context.user_id)
    if not is_owner:
        raise ForbiddenException(
            message_key="organizations.errors.only_creator_can_request_deletion",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


async def require_super_admin(
    current_user: dict,
) -> None:
    """Check if the current user is a system super admin.

    Checks app_metadata.role for 'system_super_admin' value from JWT token.

    Args:
        current_user (dict): Decoded JWT token containing user information

    Raises:
        ForbiddenException: If user is not a system super admin

    Usage:
        await require_super_admin(current_user)
    """
    is_admin = await is_system_super_admin(current_user)
    if not is_admin:
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


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

# Default pagination limits
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# UUID validation regex (if needed for additional validation)
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def set_audit_old_data_from_user(request: Request, current_user_data: dict) -> None:
    """Set audit old data from user profile information.

    This utility function handles the common pattern of setting
    request.state.raw_audit_old_data with user profile information
    for audit comparison.

    Args:
        request (Request): FastAPI request object for setting audit state
        current_user_data (dict): User profile data from database
    """
    # Compute full_name from first_name and last_name
    first_name = current_user_data.get("first_name", "")
    last_name = current_user_data.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip() or current_user_data.get("full_name")

    audit_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
        "full_name": full_name,
        "first_name": current_user_data.get("first_name"),
        "last_name": current_user_data.get("last_name"),
        "phone_number": current_user_data.get("phone_number"),
        "phone_isd_code": current_user_data.get("phone_isd_code"),
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


def hash_token(token: str) -> str:
    """Hash token using SHA256 for secure storage"""
    return hashlib.sha256(token.encode()).hexdigest()


def parse_json_field(field_value: str | dict[str, Any] | None) -> dict[str, Any]:
    """Parse a JSON field that may be a string or dict.

    Args:
        field_value: The field value that may be a JSON string or dict

    Returns:
        dict[str, Any]: Parsed dictionary, empty dict if None or invalid
    """
    if field_value is None:
        return {}
    if isinstance(field_value, dict):
        return field_value
    if isinstance(field_value, str):
        if not field_value:
            return {}
        return json.loads(field_value)
    return {}
