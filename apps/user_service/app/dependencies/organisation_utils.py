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

from typing import Optional

from fastapi import HTTPException, status

# Local imports
from apps.user_service.app.dependencies.common_utils import ORG_STATUSES


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