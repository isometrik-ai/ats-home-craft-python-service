"""Invites Schemas Module.

This module contains all Pydantic models and schemas related to invites.
These schemas are used for request/response validation and API documentation.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from apps.user_service.app.schemas.enums import InviteStatus


class InviteDetailsResponse(BaseModel):
    """Response model for invitation details operations."""

    valid: bool
    email: str | None = None
    organization_name: str | None = None
    organization_id: str | None = None
    role: str | None = None
    invited_by: str | None = None
    expires_at: str | None = None
    error: str | None = None


class InviteResponse(BaseModel):
    """Response model for invitation details operations."""

    invite_id: str | None = None
    invite_url: str | None = None
    email: str | None = None
    expires_at: str | None = None


class InvitedUserInfo(BaseModel):
    """User information model"""

    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    phone_isd_code: str | None = None
    timezone: str | None = Field(alias="timezone")
    org_setup_status_completed: bool = False
    organization_id: str | None = None


class InviteAcceptResponse(BaseModel):
    """Response model for invitation acceptance operations."""

    access_token: str
    refresh_token: str
    expires_in: int
    expires_at: datetime
    user: InvitedUserInfo


class InviteCreateRequest(BaseModel):
    """Request model for invitation creation operations."""

    salutation: Literal["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Adv."] | None = Field(
        None, description="Salutation for the user"
    )
    first_name: str = Field(..., min_length=2)
    last_name: str | None = Field(None, min_length=1)
    email: EmailStr = Field(..., description="Email address to invite")
    phone_number: str | None = None
    phone_isd_code: str | None = None
    role_id: uuid.UUID = Field(default="member", description="Role: owner, admin, or member")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "newuser@example.com",
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "expires_in_days": 7,
            }
        }
    )


class InviteAcceptBySettingPasswordRequest(BaseModel):
    """Request model for invitation acceptance operations."""

    token: str = Field(..., description="Invite token from the URL")
    password: str | None = Field(
        None,
        description=(
            "Password for the user. Required for new users and for existing users who "
            "already have a password set; optional for existing users without password."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "abc123xyz456...",
                "password": "Password@123",
            }
        }
    )


class InviteValidateLinkRequest(BaseModel):
    """Request model for invite link validation operations."""

    token: str = Field(..., description="Invite token from the URL")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "abc123xyz456...",
            }
        }
    )


class InviteValidateLinkResponse(BaseModel):
    """Response model for invite link validation operations."""

    is_existing_user: bool = Field(..., description="Whether the user already exists in the system")
    has_password: bool = Field(
        default=False,
        description="Whether user has a password (auth.users.encrypted_password is present)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "is_existing_user": True,
            }
        }
    )


class InviteListItem(BaseModel):
    """Model for invitation list item"""

    invite_id: str = Field(..., description="Unique identifier for the invitation")
    email: str = Field(..., description="Email address of the invitee")
    role_id: uuid.UUID = Field(..., description="Role assigned to the invitee")
    status: InviteStatus = Field(..., description="Current status of the invitation")
    invited_by: str = Field(..., description="User ID who sent the invitation")
    expires_at: str = Field(..., description="ISO timestamp when invitation expires")
    created_at: str = Field(..., description="ISO timestamp when invitation was created")
    updated_at: str = Field(..., description="ISO timestamp when invitation was last updated")
    salutation: Literal["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Adv."] | None = Field(
        None, description="Salutation for the user"
    )
    first_name: str | None = Field(None, min_length=2)
    last_name: str | None = Field(None, min_length=1)
    phone: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "invite_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "email": "newuser@example.com",
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": InviteStatus.PENDING.value,
                "invited_by": "550e8400-e29b-41d4-a716-446655440000",
                "expires_at": "2024-12-26T10:00:00Z",
                "created_at": "2024-12-19T10:00:00Z",
                "updated_at": "2024-12-19T10:00:00Z",
                "salutation": "Mr.",
                "first_name": "John",
                "last_name": "Doe",
                "phone": "+1234567890",
            }
        }
    )


class InviteListResponse(BaseModel):
    """Response model for invitation list operations"""

    data: list[InviteListItem] = Field(..., description="List of invitations if successful")
    total_count: int = Field(..., description="Total number of invitations")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Retrieved 5 invitations",
                "data": [
                    {
                        "invite_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "email": "newuser@example.com",
                        "role_id": "550e8400-e29b-41d4-a716-446655440000",
                        "status": InviteStatus.PENDING.value,
                        "invited_by": "550e8400-e29b-41d4-a716-446655440000",
                        "expires_at": "2024-12-26T10:00:00Z",
                        "created_at": "2024-12-19T10:00:00Z",
                        "updated_at": "2024-12-19T10:00:00Z",
                    }
                ],
                "total_count": 5,
                "page": 1,
                "page_size": 20,
            }
        }
    )


class InviteListQueryParams(BaseModel):
    """Query parameters for invitation listing"""

    page: int = Field(default=1, ge=1, description="Page number for pagination")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items per page")
    status: InviteStatus | None = Field(None, description="Filter by invitation status")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 20,
                "status": InviteStatus.PENDING.value,
            }
        }
    )
