# pylint: disable=invalid-name,E0213
"""
Invites Schemas Module

This module contains all Pydantic models and schemas related to invites.
These schemas are used for request/response validation and API documentation.
"""

from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from apps.user_service.app.schemas.common import PaginationBase
from apps.user_service.app.schemas import ResponseModel

# Constants for examples
EXAMPLE_EMAIL = "newuser@example.com"
EXAMPLE_TIMESTAMP = "2024-12-19T10:00:00Z"


class InviteDetailsResponse(BaseModel):
    """
    Response model for invitation details operations
    """
    valid: bool
    email: Optional[str] = None
    organization_name: Optional[str] = None
    organization_id: Optional[str] = None
    role: Optional[str] = None
    invited_by: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class InviteResponse(BaseModel):
    """
    Response model for invitation details operations
    """
    success: bool
    invite_id: Optional[str] = None
    invite_url: Optional[str] = None
    email: Optional[str] = None
    expires_at: Optional[str] = None
    message: Optional[str] = None


class InviteAcceptResponse(BaseModel):
    """
    Response model for invitation acceptance operations
    """
    success: bool
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    role: Optional[str] = None
    already_member: Optional[bool] = None
    message: Optional[str] = None
    error: Optional[str] = None


class InviteCreateRequest(BaseModel):
    """
    Request model for invitation creation operations
    """
    email: EmailStr = Field(..., description="Email address to invite")
    role: str = Field(default="member", description="Role: owner, admin, or member")
    expires_in_days: int = Field(default=7, ge=1, le=30, description="Days until invite expires")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": EXAMPLE_EMAIL,
                "role": "member",
                "expires_in_days": 7
            }
        }
    )


class InviteAcceptRequest(BaseModel):
    """
    Request model for invitation acceptance operations
    """
    token: str = Field(..., description="Invite token from the URL")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "abc123xyz456..."
            }
        }
    )


class InviteListItem(BaseModel):
    """Model for invitation list item"""

    invite_id: str = Field(..., description="Unique identifier for the invitation")
    email: str = Field(..., description="Email address of the invitee")
    role: str = Field(..., description="Role assigned to the invitee")
    status: str = Field(..., description="Current status of the invitation")
    invited_by: str = Field(..., description="User ID who sent the invitation")
    expires_at: str = Field(..., description="ISO timestamp when invitation expires")
    created_at: str = Field(..., description="ISO timestamp when invitation was created")
    updated_at: str = Field(..., description="ISO timestamp when invitation was last updated")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invite_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "email": EXAMPLE_EMAIL,
                "role": "member",
                "status": "pending",
                "invited_by": "550e8400-e29b-41d4-a716-446655440000",
                "expires_at": "2024-12-26T10:00:00Z",
                "created_at": EXAMPLE_TIMESTAMP,
                "updated_at": EXAMPLE_TIMESTAMP
            }
        }
    )


class InviteListResponse(PaginationBase, ResponseModel):
    """Response model for invitation list operations"""

    data: List[InviteListItem] = Field(
        ..., description="List of invitations if successful"
    )
    total_count: int = Field(..., description="Total number of invitations")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Retrieved 5 invitations",
                "data": [
                    {
                        "invite_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "email": EXAMPLE_EMAIL,
                        "role": "member",
                        "status": "pending", # pending, accepted, rejected, expired, revoked
                        "invited_by": "550e8400-e29b-41d4-a716-446655440000",
                        "expires_at": "2024-12-26T10:00:00Z",
                        "created_at": EXAMPLE_TIMESTAMP,
                        "updated_at": EXAMPLE_TIMESTAMP
                    }
                ],
                "total_count": 5,
                "page": 1,
                "page_size": 20
            }
        }
    )


class InviteListQueryParams(BaseModel):
    """Query parameters for invitation listing"""

    page: int = Field(default=1, ge=1, description="Page number for pagination")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items per page")
    status: Optional[str] = Field(None, description="Filter by invitation status")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 20,
                "status": "pending"
            }
        }
    )
