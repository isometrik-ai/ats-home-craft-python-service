# pylint: disable=invalid-name,E0213,C0301
"""
Schemas Module

This module contains all Pydantic models and schemas related to user management.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""
from typing import Optional
from urllib.parse import urlparse
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

def _bad_request(detail: str) -> None:
    """Raise a standardized HTTP 400 error with the given detail.

    Centralizing this avoids repetition across validation branches.
    """
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def validate_url_field(value: Optional[str], field_name: str = "URL") -> Optional[str]:
    """
    Shared URL validation function for avatar_url and logo_url fields.

    Validates that a URL:
    - Is None (allowed for optional fields)
    - Is an empty/whitespace string (converted to None)
    - Starts with http:// or https://
    - Contains a valid domain or host

    Args:
        value: The URL value to validate
        field_name: Name of the field for error messages (default: "URL")

    Returns:
        The validated URL string, or None if value was None/empty

    Raises:
        ValueError: If the URL is invalid
    """
    if value is None:
        return None

    # Handle empty string or whitespace-only string
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

    # Validate URL format
    try:
        result = urlparse(value)
        if not result.scheme or result.scheme not in ('http', 'https'):
            raise ValueError(f"{field_name} must start with http:// or https://")
        if not result.netloc:
            raise ValueError(f"{field_name} must contain a valid domain or host")
        return value
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"{field_name} must be a valid URL (e.g., https://example.com/image.jpg)")


class ResponseModel(BaseModel):
    """Standard error response model."""
    message: str = Field(
        ..., description="Response message describing the operation result"
    )
