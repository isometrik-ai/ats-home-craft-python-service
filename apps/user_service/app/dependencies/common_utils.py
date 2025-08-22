"""
Common API Utilities Module

This module provides reusable utility functions for FastAPI endpoints that are
shared across all API modules. These utilities eliminate code duplication and
standardize common operations.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Common Patterns Covered:
1. JWT Token validation and user context extraction
2. Permission checking with performance timing
3. Performance timing measurements
4. UUID validation
5. Exception handling decorators
6. Pagination parameter validation
"""

import time
import uuid
import json
from typing import Optional, List, Dict, Any, Callable, Union
from functools import wraps
from dataclasses import dataclass

from fastapi import HTTPException, status
from libs.shared_middleware.jwt_auth import check_user_access_async

from apps.user_service.app.dependencies.user_utils import fetch_user_profile

# ============================================================================
# DATA CLASSES
# ============================================================================


@dataclass
class UserContext:
    """User context extracted and validated from JWT token."""

    user_id: str
    organization_id: str
    email: str
    user_type: str


@dataclass
class PerformanceTimer:
    """Performance timing context manager and utility."""

    operation_name: str
    start_time: Optional[float] = None

    def __post_init__(self):
        self.start_time = time.time()

    def checkpoint(self, step_name: str) -> float:
        """Log a timing checkpoint and return elapsed time in ms."""
        assert self.start_time is not None, "Timer not initialized"
        elapsed = (time.time() - self.start_time) * 1000
        print(f"{step_name} took {elapsed:.2f}ms")
        return elapsed

    def total_time(self) -> float:
        """Return total elapsed time in ms."""
        assert self.start_time is not None, "Timer not initialized"
        elapsed = (time.time() - self.start_time) * 1000
        print(f"Total {self.operation_name} time: {elapsed:.2f}ms")
        return elapsed


# ============================================================================
# USER CONTEXT EXTRACTION
# ============================================================================


def extract_user_context(current_user: dict) -> UserContext:
    """
    Extract and validate user context from JWT token.

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
        HTTPException: 400 for missing or invalid token data

    Usage:
        user_context = extract_user_context(current_user)
        print(f"User: {user_context.email} in org: {user_context.organization_id}")
    """
    user_id = current_user.get("sub")
    user_metadata = current_user.get("user_metadata", {})
    organization_id = user_metadata.get("organization_id")
    token_email = current_user.get("email")
    user_type = user_metadata.get("type", "")  # Extract otype from JWT

    # Validation: Ensure required fields are present
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token: user ID not found",
        )

    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token: organization ID not found",
        )

    if not token_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token: email not found",
        )

    if not user_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token: user type not found",
        )

    # Validate user type is one of the expected values
    valid_user_types = ["organization_member", "client", "candidate"]
    if user_type not in valid_user_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid token: user type '{user_type}' is not supported. Expected one of: {', '.join(valid_user_types)}",
        )

    return UserContext(user_id, organization_id, token_email, user_type)


# ============================================================================
# PERMISSION CHECKING
# ============================================================================


async def require_permission(
    permission_code: Union[str, List[str]],
    user_context: UserContext,
    db_conn,
    action_description: str = "perform this action",
    with_timing: bool = True,
) -> None:
    """
    Check user permission with performance timing and detailed error handling.

    This function performs async permission checking with:
    - Performance timing measurement
    - Detailed error messages
    - Standardized permission denied responses

    Args:
        permission_code (str): Permission code to check (e.g., "settings.roles.manage")
        user_context (UserContext): Validated user context
        db_conn: AsyncPG database connection
        action_description (str): Description for error message (default: "perform this action")
        with_timing (bool): Whether to log timing information (default: True)

    Raises:
        HTTPException: 403 for insufficient permissions

    Usage:
        await require_permission("settings.roles.manage", user_context, db_conn, "manage roles")
    """
    if with_timing:
        permission_start = time.time()

    if isinstance(permission_code, str):
        permission_codes = [permission_code]
    else:
        permission_codes = permission_code

    has_permission = await check_user_access_async(
        permission_code=permission_codes,
        user_id=user_context.user_id,
        organisation_id=user_context.organization_id,
        db_conn=db_conn,
    )

    if with_timing:
        permission_end = time.time()
        print(
            f"Permission check took {(permission_end - permission_start) * 1000:.2f}ms"
        )

    if not has_permission:
        print(f"Permission denied - {action_description}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions to {action_description}",
        )


# ============================================================================
# UUID VALIDATION
# ============================================================================


def validate_uuid_format(value: str, field_name: str = "ID") -> None:
    """
    Validate UUID format and raise HTTPException if invalid.

    Args:
        value (str): UUID string to validate
        field_name (str): Field name for error message (default: "ID")

    Raises:
        HTTPException: 400 for invalid UUID format

    Usage:
        validate_uuid_format(role_id, "role ID")
        validate_uuid_format(user_id, "user ID")
    """
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name} format",
        ) from exc


def validate_uuid_list(uuid_list: List[str], field_name: str = "ID") -> None:
    """
    Validate a list of UUIDs and raise HTTPException if any are invalid.

    Args:
        uuid_list (List[str]): List of UUID strings to validate
        field_name (str): Field name for error message (default: "ID")

    Raises:
        HTTPException: 400 for invalid UUID format

    Usage:
        validate_uuid_list(permission_ids, "permission ID")
    """
    for uuid_str in uuid_list:
        try:
            uuid.UUID(uuid_str)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field_name} format: {uuid_str}",
            ) from exc


# ============================================================================
# PAGINATION VALIDATION
# ============================================================================


def validate_pagination_params(
    page: int = 1, page_size: int = 20, max_page_size: int = 100
) -> tuple:
    """
    Validate pagination parameters and calculate offset.

    Args:
        page (int): Page number (must be >= 1)
        page_size (int): Items per page (must be between 1 and max_page_size)
        max_page_size (int): Maximum allowed page size (default: 100)

    Returns:
        tuple: (validated_page, validated_page_size, calculated_offset)

    Raises:
        HTTPException: 422 for invalid pagination parameters

    Usage:
        page, page_size, offset = validate_pagination_params(page, page_size)
    """
    if page <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Page must be a positive integer",
        )

    if page_size <= 0 or page_size > max_page_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Page size must be between 1 and {max_page_size}",
        )

    offset = (page - 1) * page_size
    return page, page_size, offset


# ============================================================================
# EXCEPTION HANDLING DECORATORS
# ============================================================================


def handle_api_exceptions(operation_name: str):
    """
    Decorator for standardized exception handling in API endpoints.

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

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # Re-raise HTTP exceptions as-is (preserves status codes)
                raise
            except Exception as error:
                # Handle any other unexpected errors
                print(f"Error in {operation_name}: {error}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Internal server error during {operation_name}",
                ) from error

        return wrapper

    return decorator


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def format_iso_datetime(dt) -> Optional[str]:
    """
    Format datetime to ISO string, handling None values.

    Args:
        dt: Datetime object or None

    Returns:
        Optional[str]: ISO formatted string or None

    Usage:
        created_at = format_iso_datetime(role["created_at"])
    """
    return dt.isoformat() if dt else None


def safe_json_loads(json_str, default=None):
    """
    Safely parse JSON string with fallback.

    Args:
        json_str: JSON string to parse
        default: Default value if parsing fails

    Returns:
        Parsed JSON or default value

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


def build_filter_message(filters: Dict[str, Any]) -> str:
    """
    Build a filter description message for API responses.

    Args:
        filters (Dict[str, Any]): Dictionary of applied filters

    Returns:
        str: Formatted filter message

    Usage:
        filter_msg = build_filter_message({"search": "admin", "type": "system"})
        # Returns: " with filters: search='admin', type=system"
    """
    if not filters:
        return ""

    filter_parts = []
    for key, value in filters.items():
        if value is not None:
            if isinstance(value, str):
                filter_parts.append(f"{key}='{value}'")
            else:
                filter_parts.append(f"{key}={value}")

    return f" with filters: {', '.join(filter_parts)}" if filter_parts else ""


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


async def get_user_in_organization(db_conn, user_id: str, organization_id: str):
    """
    Fetch user profile data and raise 404 if not found in organization.

    This utility function handles the common pattern of:
    1. Fetching user profile data
    2. Checking if user exists in organization
    3. Raising 404 if user not found

    Args:
        db_conn: Database connection
        user_id (str): User ID to fetch
        organization_id (str): Organization ID for filtering

    Returns:
        Record: User profile data

    Raises:
        HTTPException: 404 if user not found in organization
    """

    current_user_data = await fetch_user_profile(db_conn, user_id, organization_id)
    if not current_user_data:
        raise HTTPException(status_code=404, detail="User not found in organization")

    return current_user_data


def set_audit_old_data_from_user(request, current_user_data: dict):
    """
    Set audit old data from user profile information.

    This utility function handles the common pattern of setting
    request.state.raw_audit_old_data with user profile information
    for audit comparison.

    Args:
        request: FastAPI request object for setting audit state
        current_user_data (dict): User profile data from database

    Usage:
        set_audit_old_data_from_user(request, current_user_data)
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
        audit_data["joined_at"] = current_user_data["joined_at"].isoformat()
    if current_user_data.get("last_active_at"):
        audit_data["last_active_at"] = current_user_data["last_active_at"].isoformat()

    request.state.raw_audit_old_data = audit_data
