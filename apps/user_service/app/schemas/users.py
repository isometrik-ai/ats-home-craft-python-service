# pylint: disable=invalid-name,E0213
"""
User Schemas Module

This module contains all Pydantic models and schemas related to user management.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

from typing import List, Optional
from enum import Enum

from pydantic import BaseModel, Field, EmailStr, ConfigDict

from apps.user_service.app.schemas.common import PaginationBase, SimpleResponse
from apps.user_service.app.schemas import ResponseModel

class UserStatus(str, Enum):
    """Enumeration for user account status"""

    active = "active"
    invited = "invited"
    suspended = "suspended"


class RoleInfo(BaseModel):
    """Model for role information

    Attributes:
        role_id (str): Unique identifier for the role
        role_name (str): Human-readable name of the role
    """

    role_id: str = Field(..., description="Unique identifier for the role")
    role_name: str = Field(..., description="Human-readable name of the role")

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
    permission_name: str = Field(
        ..., description="Human-readable name of the permission"
    )
    permission_code: str = Field(..., description="Unique code for the permission")
    category: Optional[str] = Field(
        None, description="Category grouping for the permission"
    )

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
    full_name: Optional[str] = Field(None, description="full name of the user")
    first_name: Optional[str] = Field(None, description="User's first name")
    last_name: Optional[str] = Field(None, description="User's last name")
    avatar_url: Optional[str] = Field(None, description="URL to user's profile picture")
    phone: Optional[str] = Field(None, description="User's phone number")
    timezone: str = Field(default="UTC", description="User's timezone setting")
    status: str = Field(..., description="User's membership status in organization")
    joined_at: str = Field(
        ..., description="ISO timestamp when user joined organization"
    )
    last_active_at: Optional[str] = Field(
        None, description="ISO timestamp of last activity"
    )
    organization_id: str = Field(
        ..., description="ID of the organization user belongs to"
    )
    user_type: str = Field(
        ..., description="Type of user (organization_member, client, candidate)"
    )
    role: Optional[RoleInfoWithDescription] = Field(
        None,
        description="User's assigned role information (only for organization_member)",
    )
    permissions: List[PermissionInfo] = Field(
        default_factory=list,
        description="List of all user permissions (only for organization_member)",
    )
    candidate_data: Optional[dict] = Field(
        None,
        description="Detailed candidate profile data (only for candidate user type)",
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
            }
        }
    )



class UserProfileResponse(ResponseModel):
    """Response model for user profile operations

    This is the standard response wrapper for user profile endpoints.

    Attributes:
        message (str): Response message describing the operation result
        data (Optional[UserProfileData]): User profile data if successful
    """

    data: Optional[UserProfileData] = Field(
        None, description="User profile data if successful"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "User profile retrieved successfully",
                "data": {
                    "user_id": "550e8400-e29b-41d4-a716-446655440000",
                    "email": "doe@example.com",
                    "full_name": "John Doe",
                    "timezone": "UTC",
                    "status": "active",
                }
            }
        }
    )



class UserResponse(SimpleResponse):
    """Response model for basic User operations."""


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
    full_name: str = Field(
        ..., min_length=2, max_length=255, description="User's full name"
    )
    phone: Optional[str] = Field(None, description="User's phone number")
    timezone: Optional[str] = Field(
        default="UTC", description="User's timezone preference"
    )
    role_id: str = Field(..., description="ID of the role to assign to the user")
    organization_id: Optional[str] = Field(
        None, description="ID of the organization to add user to"
    )

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

    full_name: Optional[str] = Field(
        None, min_length=2, max_length=255, description="Updated full name"
    )
    first_name: Optional[str] = Field(None, description="Updated first name")
    last_name: Optional[str] = Field(None, description="Updated last name")
    phone: Optional[str] = Field(None, description="Updated phone number")
    timezone: Optional[str] = Field(None, description="Updated timezone preference")
    avatar_url: Optional[str] = Field(None, description="Updated avatar URL")
    role_id: Optional[str] = Field(None, description="Updated role assignment")
    status: Optional[UserStatus] = Field(
        None, description="User status: active, invited, or suspended"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "full_name": "Updated Name",
                "phone": "+0987654321",
                "timezone": "America/New_York",
                "avatar_url": "https://example.com/new-avatar.jpg",
                "role_id": "new-role-id",
            }
        }
    )


class UpdateUserResponse(ResponseModel):
    """Response model for user update operations

    Attributes:
        message (str): Response message
        data (Optional[UserProfileData]): Updated user profile data
    """

    data: Optional[UserProfileData] = Field(
        None, description="Updated user profile data"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "User updated successfully",
                "data": None,
            }
        }
    )


class BanRequest(BaseModel):
    """Request model for banning a user."""

    duration: Optional[str] = Field(None, description="7d")
    reason: Optional[str] = Field(None, description="Reason for banning the users")



class BanResponse(ResponseModel):
    """Response model after banning a user."""

    reason: str


class UnbanResponse(ResponseModel):
    """Response model after unbanning a user."""



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
    full_name: Optional[str] = Field(None, description="User's full name")
    phone: Optional[str] = Field(None, description="Updated phone number")
    first_name: Optional[str] = Field(None, description="Updated first name")
    last_name: Optional[str] = Field(None, description="Updated last name")
    role_name: str = Field(..., description="Name of user's assigned role")
    status: str = Field(..., description="User's membership status")
    joined_at: str = Field(..., description="ISO timestamp when user joined")
    last_active_at: Optional[str] = Field(
        None, description="ISO timestamp of last activity"
    )
    permissions_count: int = Field(
        0, description="Number of permissions assigned to the user"
    )
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


class UserListResponse(PaginationBase, ResponseModel):
    """Response model for user list operations

    Attributes:
        message (str): Response message
        data (List[UserListItem]): List of users
        total_count (int): Total number of users
        page (int): Current page number
        page_size (int): Number of items per page
    """

    data: List[UserListItem] = Field(..., description="List of users")
    total_count: int = Field(..., description="Total number of users")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Users retrieved successfully",
                "data": [],
                "total_count": 0,
                "page": 1,
                "page_size": 20,
            }
        }
    )
