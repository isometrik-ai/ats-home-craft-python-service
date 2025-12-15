"""User Schemas Module.

This module contains all Pydantic models and schemas related to user management.
These schemas are used for request/response validation and API documentation.
"""

import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserStatus(str, Enum):
    """Enumeration for user account status"""

    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class RoleInfo(BaseModel):
    """Model for role information

    Attributes:
        role_id (str): Unique identifier for the role
        role_name (str): Human-readable name of the role
    """

    role_id: str = Field(..., description="Unique identifier for the role")
    # role_name: str = Field(..., description="Human-readable name of the role")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "role_name": "Administrator",
            }
        }
    )


class RoleInfoWithDescription(RoleInfo):
    """Role with descrption"""

    description: str = Field(..., description="Optional description for the role")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "role_name": "Administrator",
                "description": "This role can manage users and permissions.",
            }
        }
    )


class PermissionInfo(BaseModel):
    """Model for permission information

    Attributes:
        permission_id (str): Unique identifier for the permission
        permission_name (str): Human-readable name of the permission
        permission_code (str): Unique code for the permission
        category (Optional[str]): Category grouping for the permission
    """

    permission_id: str = Field(..., description="Unique identifier for the permission")
    permission_name: str = Field(..., description="Human-readable name of the permission")
    permission_code: str = Field(..., description="Unique code for the permission")
    category: str | None = Field(None, description="Category grouping for the permission")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "permission_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "permission_name": "Read Organization",
                "permission_code": "org.read",
                "category": "organization",
            }
        }
    )


class VerificationPreference(BaseModel):
    """Model for verification preference settings"""

    two_fa_enabled: bool = Field(
        False,
        description="Whether 2FA verification is enabled or disabled",
    )
    verification_method: str = Field(None, description="Type of verification: PHONE or EMAIL")

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        json_schema_extra={"example": {"two_fa_enabled": True, "verification_method": "PHONE"}},
    )


class Indentites(BaseModel):
    """Model for user indentites"""

    provider: str = Field(..., description="Provider of the indentite")
    provider_id: str = Field(..., description="Data of the indentite")
    created_at: datetime.datetime = Field(
        ..., description="ISO timestamp when the indentite was created"
    )
    updated_at: datetime.datetime = Field(
        ..., description="ISO timestamp when the indentite was updated"
    )
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "google",
                "identity_data": {"sub": "1234567890"},
                "created_at": "2024-12-19T10:00:00Z",
                "updated_at": "2024-12-19T10:00:00Z",
                "last_sign_in_at": "2024-12-19T10:00:00Z",
            }
        }
    )


class UserProfileData(BaseModel):
    """Model for complete user profile data

    This model contains all user information including personal details,
    organization membership, role assignment, and permissions.

    Attributes:
        user_id (str): Unique identifier for the user
        email (str): User's email address
        full_name (Optional[str]): User's full name
        avatar_url (Optional[str]): URL to user's profile picture
        phone (Optional[str]): User's phone number
        timezone (str): User's timezone setting
        status (str): User's membership status in organization
        joined_at (str): ISO timestamp when user joined organization
        last_active_at (Optional[str]): ISO timestamp of last activity
        organization_id (str): ID of the organization user belongs to
        user_type (str): Type of user (organization_member, client, candidate)
        role (Optional[RoleInfoWithDescription]): User's assigned role information
            (only for organization_member)
        permissions (List[PermissionInfo]): List of all user permissions
            (only for organization_member)
    """

    user_id: str = Field(..., description="Unique identifier for the user")
    email: str = Field(..., description="User's email address")
    full_name: str | None = Field(None, description="full name of the user")
    first_name: str | None = Field(None, description="User's first name")
    last_name: str | None = Field(None, description="User's last name")
    avatar_url: str | None = Field(None, description="URL to user's profile picture")
    phone: str | None = Field(None, description="User's phone number")
    timezone: str = Field(default="UTC", description="User's timezone setting")
    salutation: str | None = Field(None, description="User's salutation")
    status: str = Field(..., description="User's membership status in organization")
    joined_at: str | None = Field(None, description="ISO timestamp when user joined organization")
    last_active_at: str | None = Field(None, description="ISO timestamp of last activity")
    organization_id: str = Field(..., description="ID of the organization user belongs to")
    # user_type: str = Field(
    #     ..., description="Type of user (organization_member, client, candidate)"
    # )
    role: RoleInfoWithDescription | None = Field(
        None,
        description="User's assigned role information (only for organization_member)",
    )
    permissions: list[PermissionInfo] = Field(
        default_factory=list,
        description="List of all user permissions (only for organization_member)",
    )
    candidate_data: dict | None = Field(
        None,
        description="Detailed candidate profile data (only for candidate user type)",
    )
    identities: list[Indentites] | None = Field(
        None,
        description="List of all user identities (only for organization_member)",
    )
    verification_preference: VerificationPreference | None = Field(
        None,
        description="Verification preference settings (enabled/disabled and type: PHONE or EMAIL)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "john.doe@example.com",
                "full_name": "John Jani janardhan",
                "avatar_url": "https://example.com/avatar.jpg",
                "phone": "+1234567890",
                "timezone": "UTC",
                "salutation": "Mr.",
                "status": "active",
                "joined_at": "2024-12-19T10:00:00Z",
                "last_active_at": "2024-12-19T15:30:00Z",
                "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "user_type": "organization_member",
                "role": {
                    "role_id": "550e8400-e29b-41d4-a716-446655440000",
                    "role_name": "Administrator",
                    "description": "This role can manage users and permissions.",
                },
                "permissions": [
                    {
                        "permission_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "permission_name": "Read Organization",
                        "permission_code": "org.read",
                        "category": "organization",
                    }
                ],
                "candidate_data": None,
                "identities": [
                    {
                        "provider": "google",
                        "identity_data": {"sub": "1234567890"},
                        "created_at": "2024-12-19T10:00:00Z",
                        "updated_at": "2024-12-19T10:00:00Z",
                        "last_sign_in_at": "2024-12-19T10:00:00Z",
                    }
                ],
                "verification_preference": {
                    "two_fa_enabled": True,
                    "verification_method": "PHONE",
                },
            }
        }
    )


class UpdateUserEmailRequest(BaseModel):
    """Request model for creating a new user

    Attributes:
        email (EmailStr): User's email address (required)
    """

    email: EmailStr = Field(..., description="User's New Updated email address")


class CreateUserRequest(BaseModel):
    """Request model for creating a new user

    Attributes:
        email (EmailStr): User's email address (required)
        full_name (str): User's full name (required)
        phone (Optional[str]): User's phone number
        timezone (str): User's timezone preference
        role_id (str): ID of the role to assign to the user
        organization_id (str): ID of the organization to add user to
    """

    email: EmailStr = Field(..., description="User's New email address")
    full_name: str = Field(..., min_length=2, max_length=255, description="User's full name")
    phone: str | None = Field(None, description="User's phone number")
    timezone: str | None = Field(default="UTC", description="User's timezone preference")
    role_id: str = Field(..., description="ID of the role to assign to the user")
    organization_id: str | None = Field(None, description="ID of the organization to add user to")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "new.user@example.com",
                "full_name": "New User",
                "phone": "+1234567890",
                "timezone": "UTC",
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            }
        }
    )


class UpdateUserRequest(BaseModel):
    """Request model for updating user information

    All fields are optional for partial updates.

    Attributes:
        full_name (Optional[str]): Updated full name
        phone (Optional[str]): Updated phone number
        timezone (Optional[str]): Updated timezone preference
        avatar_url (Optional[str]): Updated avatar URL
        role_id (Optional[str]): Updated role assignment
    """

    full_name: str | None = Field(
        None, min_length=2, max_length=255, description="Updated full name"
    )
    first_name: str | None = Field(None, description="Updated first name")
    last_name: str | None = Field(None, description="Updated last name")
    phone: str | None = Field(None, description="Updated phone number")
    timezone: str | None = Field(None, description="Updated timezone preference")
    avatar_url: str | None = Field(
        None,
        description="Updated avatar path (e.g., 'house-of-apps-legal-ai/user-id/filename.jpg')",
    )
    role_id: str | None = Field(None, description="Updated role assignment")
    status: UserStatus | None = Field(
        None, description="User status: active, invited, or suspended"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "full_name": "Updated Name",
                "phone": "+0987654321",
                "timezone": "America/New_York",
                "avatar_url": "house-of-apps-legal-ai/user-id/new-avatar.jpg",
                "role_id": "new-role-id",
            }
        }
    )


class BanRequest(BaseModel):
    """Request model for banning a user."""

    duration: str | None = Field(None, description="7d")
    reason: str | None = Field(None, description="Reason for banning the users")


class UserListItem(BaseModel):
    """Model for user list item (summary view)

    Used for displaying users in lists without full profile details.

    Attributes:
        user_id (str): Unique identifier for the user
        email (str): User's email address
        full_name (Optional[str]): User's full name
        role_name (str): Name of user's assigned role
        status (str): User's membership status
        joined_at (str): ISO timestamp when user joined
        last_active_at (Optional[str]): ISO timestamp of last activity
    """

    user_id: str = Field(..., description="Unique identifier for the user")
    email: str = Field(..., description="email address of the user")
    first_name: str | None = Field(None, description="Updated first name")
    last_name: str | None = Field(None, description="Updated last name")
    salutation: str | None = Field(None, description="Updated salutation")
    phone: str | None = Field(None, description="Updated phone number")
    # role_name: str = Field(..., description="Name of user's assigned role")
    status: str = Field(..., description="User's membership status")
    joined_at: str = Field(..., description="ISO timestamp when user joined")
    last_active_at: str | None = Field(None, description="ISO timestamp of last activity")
    permissions_count: int = Field(0, description="Number of permissions assigned to the user")
    role_id: str = Field(..., description="ID of the role assigned to the user")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "john@example.com",
                "full_name": "J Jonnah Jamison",
                "role_name": "Administrator",
                "status": "active",
                "joined_at": "2024-12-19T10:00:00Z",
                "last_active_at": "2024-12-19T15:30:00Z",
                "permissions_count": 10,
                "role_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        }
    )


class UserListResponse(BaseModel):
    """Response model for user list operations

    Attributes:
        message (str): Response message
        data (list[UserListItem]): List of users
        total_count (int): Total number of users
        page (int): Current page number
        page_size (int): Number of items per page
    """

    data: list[UserListItem] = Field(..., description="List of users")
    total_count: int = Field(..., description="Total number of users")
    message: str = Field(..., description="Response message describing the operation result")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total number of pages")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Users retrieved successfully",
                "data": [],
                "total_count": 0,
                "page": 1,
                "page_size": 20,
                "total_pages": 0,
            }
        }
    )


class UpdateUserProfileRequest(BaseModel):
    """Request model for updating user profile information.

    Only these fields can be updated:
    - first_name: Updated first name
    - last_name: Updated last name
    - salutation: Updated salutation (Mr., Mrs., Ms., Dr., Prof., Adv.)
    - timezone: Updated timezone preference
    - avatar_url: Updated avatar path (e.g., 'house-of-apps-legal-ai/user-id/filename.jpg')
    - two_fa_enabled: Enable or disable verification preference
    - verification_method: Type of verification preference (PHONE or EMAIL, defaults to EMAIL)

    full_name will be automatically calculated from first_name + last_name.
    """

    first_name: str | None = Field(None, description="Updated first name")
    last_name: str | None = Field(None, description="Updated last name")
    salutation: str | None = Field(None, description="Salutation for the user")
    timezone: str | None = Field(None, description="Updated timezone preference")
    avatar_url: str | None = Field(
        None,
        description="Updated avatar path (e.g., 'house-of-apps-legal-ai/user-id/filename.jpg')",
    )
    two_fa_enabled: bool | None = Field(
        None, description="Enable or disable verification preference"
    )
    verification_method: str | None = Field(
        "EMAIL",
        description="Type of verification preference: PHONE or EMAIL (defaults to EMAIL)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "first_name": "John",
                "last_name": "Doe",
                "salutation": "Mr.",
                "timezone": "America/New_York",
                "avatar_url": "house-of-apps-legal-ai/user-id/avatar.jpg",
                "two_fa_enabled": True,
                "verification_method": "EMAIL",
            }
        }
    }
