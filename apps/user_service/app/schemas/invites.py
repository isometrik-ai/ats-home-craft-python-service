# pylint: disable=invalid-name,E0213
"""
Invites Schemas Module

This module contains all Pydantic models and schemas related to invites.
These schemas are used for request/response validation and API documentation.
"""

import uuid
from typing import Optional, List, Literal
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
    message: Optional[str] = None
    error: Optional[str] = None


class InviteCreateRequest(BaseModel):
    """
    Request model for invitation creation operations
    """
    salutation: Optional[Literal["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.","Adv."]] = Field(None, description="Salutation for the user")
    first_name: str = Field(..., min_length=2)
    last_name: Optional[str] = Field(None, min_length=2)
    email: EmailStr = Field(..., description="Email address to invite")
    phone: Optional[str] = None
    role_id: uuid.UUID = Field(default="member", description="Role: owner, admin, or member")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": EXAMPLE_EMAIL,
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "expires_in_days": 7
            }
        }
    )


class InviteAcceptBySettingPasswordRequest(BaseModel):
    """
    Request model for invitation acceptance operations
    """
    token: str = Field(..., description="Invite token from the URL")
    password: str = Field(..., description="Password for the user")

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
    role_id: uuid.UUID = Field(..., description="Role assigned to the invitee")
    status: str = Field(..., description="Current status of the invitation")
    invited_by: str = Field(..., description="User ID who sent the invitation")
    expires_at: str = Field(..., description="ISO timestamp when invitation expires")
    created_at: str = Field(..., description="ISO timestamp when invitation was created")
    updated_at: str = Field(..., description="ISO timestamp when invitation was last updated")
    salutation: Optional[Literal["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.","Adv."]] = Field(None, description="Salutation for the user")
    first_name: Optional[str] = Field(None, min_length=2)
    last_name: Optional[str] = Field(None, min_length=2)
    phone: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invite_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "email": EXAMPLE_EMAIL,
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "invited_by": "550e8400-e29b-41d4-a716-446655440000",
                "expires_at": "2024-12-26T10:00:00Z",
                "created_at": EXAMPLE_TIMESTAMP,
                "updated_at": EXAMPLE_TIMESTAMP,
                "salutation": "Mr.",
                "first_name": "John",
                "last_name": "Doe",
                "phone": "+1234567890"
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
                        "role_id": "550e8400-e29b-41d4-a716-446655440000",
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
