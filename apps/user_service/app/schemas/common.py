# pylint: disable=invalid-name,E0213
"""Common shared schemas for pagination and responses."""

from typing import Optional  # standard
from typing import Dict, Any

from pydantic import BaseModel, Field, ConfigDict  # third-party


class AuditContext(BaseModel):
    """Audit Logs parameters."""

    user_context: Optional[Dict[str, Any]] = None
    record_id: Optional[str] = None
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None


class AuditLogsQueryParams(BaseModel):
    """Query parameters for audit logs API

    Attributes:
        search (Optional[str]): Search term to filter audit
         logs by description, action_type, or table_name (case-insensitive)
        skip (int): Number of audit logs to skip for pagination
        limit (int): Maximum number of audit logs to return
    """

    search: Optional[str] = Field(
        default=None,
        description=(
            "Search term to filter audit logs by description, action_type, "
            "or table_name (case-insensitive)"
        ),
    )
    skip: int = Field(
        0, ge=0, description="Number of audit logs to skip for pagination"
    )
    limit: int = Field(
        20,
        ge=1,
        le=100,
        description="Maximum number of audit logs to return (max: 100)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "user login",
                "skip": 0,
                "limit": 20,
            }
        }
    )


class PaginationBase(BaseModel):
    """Base model for paginated request parameters."""

    page: int = Field(default=1, description="Current page number")
    page_size: int = Field(default=20, description="Number of items per page")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 20,
            }
        }
    )


class SimpleResponse(BaseModel):
    """Standard API response for simple success/failure operations."""

    message: str = Field(..., description="Response message")
    status: str = Field(default="success", description="Operation status indicator")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Operation completed successfully",
                "status": "success",
            }
        }
    )


class CommonSearchQueryParams(BaseModel):
    """Common query parameters for search and pagination.

    Attributes:
        search (Optional[str]): Search term to filter by name/description (case-insensitive)
        skip (int): Number of items to skip for pagination
        limit (int): Maximum number of items to return
    """

    search: Optional[str] = Field(
        None, description="Search term to filter by name/description (case-insensitive)"
    )
    skip: int = Field(0, ge=0, description="Number of items to skip for pagination")
    limit: int = Field(
        10, ge=1, le=100, description="Maximum number of items to return (max: 100)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "client",
                "skip": 0,
                "limit": 20,
            }
        }
    )
