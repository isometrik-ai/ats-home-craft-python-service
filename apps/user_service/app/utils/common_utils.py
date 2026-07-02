"""Common API Utilities Module.

This module provides reusable utility functions for FastAPI endpoints that are
shared across all API modules. These utilities eliminate code duplication and
standardize common operations.
"""

import hashlib
import json
import re
import secrets
import string
import time
import traceback
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from functools import wraps
from typing import Any

import asyncpg
from fastapi import HTTPException, Request
from pydantic import BaseModel

from apps.user_service.app.schemas.admin_access_management import PermissionItem
from libs.shared_middleware.jwt_auth import check_user_access_async
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.session_context_cache import resolve_session_context
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
# ENUM / STRING UTILITIES
# ============================================================================


def enum_member_title_label(member: Enum) -> str:
    """Title-case label from an enum member name."""
    return member.name.replace("_", " ").title()


def name_to_email_domain_label(value: str) -> str:
    """Convert an arbitrary organization name to a safe email domain label.

    Example: "T's org" -> "ts-org"
    """
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    lowered = ascii_value.strip().lower()
    lowered = lowered.replace("&", "and")
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered[:63] or "org"


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
    current_user: dict,
    db_connection: asyncpg.Connection,
    request: Request | None = None,
) -> UserContext:
    """Extract and validate user context from JWT token.

    This function performs comprehensive validation of JWT token data including:
    - User ID (sub) validation
    - Organization ID extraction from session context
    - Email validation
    - Presence checks for all required fields

    Args:
        current_user (dict): Decoded JWT token containing user information
        db_connection (asyncpg.Connection): Database connection (required)
        request (Request | None): Optional request for cached session context

    Returns:
        UserContext: Validated user context object

    Raises:
        ValidationException: If missing or invalid token data
        HTTPException: If HTTP error occurs
        InternalServerErrorException: If internal error occurs

    Usage:
        user_context = await extract_user_context(current_user, db_connection, request)
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

        organization_id = None
        session_id = current_user.get("session_id")

        session_ctx = current_user.get("_session_context")
        if session_ctx is not None:
            organization_id = session_ctx.get("organization_id")
        else:
            audit_context = (
                getattr(request.state, "audit_user_context", None) if request is not None else None
            )
            if audit_context and audit_context.get("user_id") == user_id:
                organization_id = audit_context.get("organization_id")
            else:
                session_ctx = await resolve_session_context(
                    user_id=user_id,
                    session_id=session_id,
                    db_connection=db_connection,
                )
                if session_ctx is None:
                    raise ValidationException(
                        message_key="auth.errors.session_not_found",
                        custom_code=CustomStatusCode.UNAUTHORIZED,
                    )
                organization_id = session_ctx.get("organization_id")

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
    request: Request | None = None,
) -> UserContext:
    """Extracts user context and checks if the user has the given permission.

    Always uses organization_id from session, never from user-provided parameter.
    Validates that session can access requested organization if provided.

    Args:
        current_user (dict): Current user data
        db_connection (asyncpg.Connection): Database connection
        permission_codes (list[str] | str): Permission codes to check
        organization_id (str | None): Organization ID for validation (not used for permission check)
        request (Request | None): Optional request for session context resolution

    Returns:
        UserContext: User context
    """
    user_context = await extract_user_context(current_user, db_connection, request=request)

    # Validate session can access requested organization if provided
    # Use already-fetched organization_id from user_context to avoid redundant DB query
    if organization_id:
        if (
            user_context.organization_id is not None
            and user_context.organization_id != organization_id
        ):
            raise ForbiddenException(
                message_key="auth.errors.session_cannot_access_organization",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

    # Always use organization_id from session (user_context.organization_id)
    await require_permission(
        permission_code=permission_codes,
        user_context=user_context,
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    return user_context


async def extract_onboarding_contact_context(
    current_user: dict,
    db_connection: asyncpg.Connection,
    request: Request | None = None,
) -> tuple[UserContext, dict[str, Any]]:
    """Resolve JWT user to active contact within selected organization."""
    from apps.user_service.app.db.repositories.contacts_repository import (
        ContactsRepository,
    )

    user_context = await extract_user_context(current_user, db_connection, request=request)
    if not user_context.organization_id:
        raise ValidationException(
            message_key="auth.errors.session_not_found",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )

    contacts_repo = ContactsRepository(db_connection)
    contact = await contacts_repo.get_active_contact_by_user_id(
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
    )
    if not contact:
        from libs.shared_utils.http_exceptions import NotFoundException

        raise NotFoundException(
            message_key="contact_onboarding.errors.contact_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    return user_context, contact


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
    from apps.user_service.app.db.repositories import OrganizationRepository

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


_NUMERIC_DATE_RE = re.compile(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$")

_STRPTIME_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%m.%d.%Y",
    "%d.%m.%Y",
    "%Y.%m.%d",
    "%m/%d/%y",
    "%d/%m/%y",
)


def _date_from_numeric_parts(first: int, second: int, third: int) -> date:
    """Build a date when the year is the last numeric segment (MDY or DMY)."""
    if third <= 31:
        raise ValueError("year must be a four-digit value in the last segment")
    year = third
    if first > 12:
        day, month = first, second
    elif second > 12:
        month, day = first, second
    else:
        month, day = first, second
    return date(year, month, day)


def _parse_numeric_delimited_date(value: str) -> date | None:
    """Parse dates like ``11/2/1992`` or ``31-12-1992`` with variable-width segments."""
    match = _NUMERIC_DATE_RE.match(value.strip())
    if not match:
        return None
    first, second, third = (int(match.group(i)) for i in range(1, 4))
    if first > 31:
        return date(first, second, third)
    if third > 31:
        return _date_from_numeric_parts(first, second, third)
    return None


def _parse_iso_date_string(raw: str) -> date | None:
    """Try ISO date (or datetime prefix) parsing."""
    iso_candidate = raw.split("T", maxsplit=1)[0].split(" ", maxsplit=1)[0]
    try:
        return date.fromisoformat(iso_candidate)
    except ValueError:
        return None


def _parse_strptime_date_string(raw: str) -> date | None:
    """Try known fixed-width date string formats."""
    for fmt in _STRPTIME_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_flexible_date_string(raw: str, *, original: str) -> date:
    """Parse a non-empty date string or raise ``ValueError``."""
    for parser in (
        _parse_iso_date_string,
        _parse_strptime_date_string,
        _parse_numeric_delimited_date,
    ):
        parsed = parser(raw)
        if parsed is not None:
            return parsed
    raise ValueError(f"Unable to parse date: {original!r}")


def parse_flexible_date(value: Any) -> date | None:
    """Parse common date inputs into a ``date`` for API/CSV validation.

    Accepts ISO ``YYYY-MM-DD``, several fixed-width slash/dash/dot formats, and
    variable-width numeric dates (e.g. ``11/2/1992``). Ambiguous ``MM/DD`` vs
    ``DD/MM`` values default to US month-first when both parts are <= 12.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise TypeError(f"date must be a string or date, got {type(value).__name__}")

    raw = value.strip()
    if not raw:
        return None

    return _parse_flexible_date_string(raw, original=value)


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
    audit_data = {
        "user_id": str(current_user_data["user_id"]),
        "email": current_user_data["email"],
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
    if isinstance(field_value, list):
        return field_value
    if isinstance(field_value, dict):
        return field_value
    if isinstance(field_value, str):
        if not field_value:
            return {}
        return json.loads(field_value)
    return {}


def coerce_json_list(value: Any) -> list[Any]:
    """Normalize a DB/API value to a list (JSON string, list, or None)."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = parse_json_field(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def safe_str(value: Any) -> str:
    """Convert a value into a stable string (ids/labels)."""
    return "" if value is None else str(value)


def title_case_field(field: str) -> str:
    """Humanize a snake_case field name for messages."""
    cleaned = field.replace("_", " ").strip()
    if cleaned.lower().endswith(" id"):
        cleaned = cleaned[: -len(" id")].strip()
    return cleaned


def get_nested(data: Any, path: str) -> Any:
    """Resolve a dotted path (e.g. `a.b.c`) against dicts safely."""
    if not path or not isinstance(data, dict):
        return None
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def parse_json_any(value: Any, default: Any = None) -> Any:
    """Parse JSON-like values that may arrive as dict/list/JSON-string."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return default

    parsed = parse_json_field(value)
    return parsed if parsed != {} else default


def normalize_nested_addresses_for_audit(
    normalized: dict[str, Any],
    *,
    parent_fk_field: str,
) -> None:
    """Normalize nested ``addresses`` on a contact or company audit snapshot dict.

    Stringifies ``id`` and the parent FK (``contact_id`` or ``company_id``), formats
    nested timestamps, and coerces ``address_data`` for stable audit diffs.
    """
    addresses_value = normalized.get("addresses")
    if not isinstance(addresses_value, list):
        return
    fixed: list[dict[str, Any]] = []
    for addr in addresses_value:
        if not isinstance(addr, dict):
            continue
        addr_norm = dict(addr)
        for id_field in ("id", parent_fk_field):
            if addr_norm.get(id_field) is not None:
                addr_norm[id_field] = str(addr_norm[id_field])
        for dt_field in ("created_at", "updated_at"):
            if addr_norm.get(dt_field) is not None:
                addr_norm[dt_field] = format_iso_datetime(addr_norm.get(dt_field))
        raw_address_data = addr_norm.get("address_data")
        parsed_address_data = parse_json_any(raw_address_data, raw_address_data)
        if isinstance(parsed_address_data, dict):
            addr_norm["address_data"] = parsed_address_data
        fixed.append(addr_norm)
    normalized["addresses"] = fixed


def extract_audit_data_value(audit_values: dict[str, Any] | None, field_path: str) -> Any:
    """Extract a changed field value from audit `{"data": ...}` payloads.

    Supports `field` and `data.field` shapes for `changed_fields`.
    """
    if not audit_values or not field_path:
        return None

    normalized = field_path[5:] if field_path.startswith("data.") else field_path
    data = audit_values.get("data")
    if not isinstance(data, dict):
        return None

    if "." in normalized:
        return get_nested(data, normalized)
    return data.get(normalized)


def serialize_jsonb_param(column_name: str, value: Any, jsonb_columns: frozenset[str]) -> Any:
    """Serialize JSONB column values to a JSON string for asyncpg; pass others through."""
    if column_name in jsonb_columns and isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def json_dumps_or_none(value: Any) -> str | None:
    """Serialize a JSON payload to string, preserving None.

    - None -> None (meaning "no change" for optional payloads)
    - [] / {} -> "[]" / "{}" (meaning "clear" vs "set empty")
    """
    return None if value is None else json.dumps(value)


def serialize_pydantic_models(value: Any) -> Any:
    """Recursively convert Pydantic models and other
    non-serializable objects to JSON-serializable primitives.

    Args:
        value: The value to serialize (can be Pydantic model, dict, list, enum, or primitive)

    Returns:
        JSON-serializable value
    """
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: serialize_pydantic_models(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize_pydantic_models(item) for item in value]
    return value


def generate_random_password(length: int = 12) -> str:
    """Generate a secure random password.

    Args:
        length: Length of the password (default: 12)

    Returns:
        str: A secure random password containing
        uppercase, lowercase, digits, and special characters
    """
    # Define character sets
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special_chars = "!@#$%^&*"

    # Ensure at least one character from each set
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special_chars),
    ]

    # Fill the rest with random characters from all sets
    all_chars = uppercase + lowercase + digits + special_chars
    password.extend(secrets.choice(all_chars) for _ in range(length - 4))

    # Shuffle to avoid predictable pattern
    secrets.SystemRandom().shuffle(password)

    return "".join(password)
