# pylint: disable=invalid-name,E0213
"""
Admin Access Management Schemas Module

This module contains all Pydantic models and schemas related to admin access management.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

from apps.user_service.app.schemas import ResponseModel
from libs.shared_utils.common_query import SETTINGS_ROLES_MANAGE

DESCRIPTION_OF_THE_ROLE = "Description of the role"
IS_DEFAULT_ROLE = "Whether this is a system role (True) or custom role (False)"
EXAMPLE_DESCRIPTION_OF_THE_ROLE = "Full access to all system features"

class UserQueryParams(BaseModel):
    """Query parameters for Users API

    Attributes:
        search (Optional[str]): Search term to filter Users by name (case-insensitive)
    """

    search: Optional[str] = Field(
        None, description="Search term to filter Users by name (case-insensitive)"
    )
    page: int = Field(1, ge=0, description="Number of Users to skip for pagination")
    page_size: int = Field(
        20, ge=1, le=100, description="Maximum number of Users to return (max: 100)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "admin",
                "page": 1,
                "page_size": 20,
            }
        }
    )


class RoleQueryParams(BaseModel):
    """Query parameters for roles API

    Attributes:
        search (Optional[str]): Search term to filter roles by name (case-insensitive)
        skip (int): Number of roles to skip for pagination
        limit (int): Maximum number of roles to return
        role_type (Optional[str]): Filter by role type - "system" or "custom"
    """

    search: Optional[str] = Field(
        None, description="Search term to filter roles by name (case-insensitive)"
    )
    skip: int = Field(0, ge=0, description="Number of roles to skip for pagination")
    limit: int = Field(
        10, ge=1, le=100, description="Maximum number of roles to return (max: 100)"
    )
    sort_type: Optional[bool] = Field(
        default=False,
        description=(
            "Sort roles alphabetically: set to True to sort, "
            " False to retain original order."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "admin",
                "skip": 0,
                "limit": 10,
                "sort_type": False,
            }
        }
    )


class RoleItem(BaseModel):
    """Model for role information in lists

    Attributes:
        id (str): Unique identifier for the role
        name (str): Human-readable name of the role
        description (Optional[str]): Description of the role
        is_default (bool): Whether this is a system role (True) or custom role (False)
        created_at (str): ISO timestamp when role was created
        user_count (int): Number of users assigned to this role
        permission_count (int): Total number of permissions assigned to this role
        permission_categories (dict): Count of permissions by category
    """

    id: str = Field(..., description="Unique identifier for the role")
    name: str = Field(..., description="Human-readable name of the role")
    description: Optional[str] = Field(None, description=DESCRIPTION_OF_THE_ROLE)
    is_default: bool = Field(
        ..., description=IS_DEFAULT_ROLE
    )
    created_at: str = Field(..., description="ISO timestamp when role was created")
    user_count: int = Field(..., description="Number of users assigned to this role")
    permission_count: int = Field(
        ..., description="Total number of permissions assigned to this role"
    )
    permission_categories: dict = Field(
        ..., description="Count of permissions by category"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "Administrator",
                "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                "is_default": True,
                "created_at": "2024-12-19T10:00:00Z",
                "user_count": 3,
                "permission_count": 12,
                "permission_categories": {
                    "business": 5,
                    "settings": 4,
                    "automation": 2,
                    "talent": 1,
                }
            }
        }
    )


class PermissionItem(BaseModel):
    """Model for permission information in lists

    Attributes:
        id (str): Unique identifier for the permission
        name (str): Human-readable name of the permission
        code (str): Unique code for the permission
        category (Optional[str]): Category grouping for the permission
        description (Optional[str]): Description of the permission
        created_at (str): ISO timestamp when permission was created
    """

    id: str = Field(..., description="Unique identifier for the permission")
    name: str = Field(..., description="Human-readable name of the permission")
    code: str = Field(..., description="Unique code for the permission")
    category: Optional[str] = Field(
        None, description="Category grouping for the permission"
    )
    description: Optional[str] = Field(
        None, description="Description of the permission"
    )
    created_at: str = Field(
        ..., description="ISO timestamp when permission was created"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "name": "Manage Roles",
                "code": SETTINGS_ROLES_MANAGE,
                "category": "settings",
                "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                "created_at": "2024-12-1T10:00:00Z",
            }
        }
    )


class RolesResponse(ResponseModel):
    """Response model for roles operations

    Attributes:
        message (str): Response message describing the operation result
        roles (List[RoleItem]): List of roles
        total_count (int): Total number of roles available (for pagination)
    """

    roles: List[RoleItem] = Field(..., description="List of roles")
    total_count: int = Field(
        ..., description="Total number of roles available (for pagination)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status_code": 200,
                "message": "Roles retrieved successfully with filters: limit=10",
                "roles": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "name": "Administrator",
                        "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                        "is_default": True,
                        "created_at": "2024-12-02T10:00:00Z",
                    }
                ],
                "total_count": 1,
            }
        }
    )


class PermissionsResponse(ResponseModel):
    """Response model for permissions operations

    Attributes:
        status_code (int): HTTP status code
        message (str): Response message describing the operation result
        permissions (List[PermissionItem]): List of permissions
    """

    permissions: List[PermissionItem] = Field(..., description="List of permissions")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status_code": 200,
                "message": "Permissions retrieved successfully",
                "permissions": [
                    {
                        "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "name": "Manage Permissions Roles",
                        "code": SETTINGS_ROLES_MANAGE,
                        "category": "settings",
                        "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                        "created_at": "2024-12-19T08:00:00Z",
                    }
                ],
            }
        }
    )


class RoleDetailItem(BaseModel):
    """Model for detailed role information with permissions

    Attributes:
        id (str): Unique identifier for the role
        name (str): Human-readable name of the role
        description (Optional[str]): Description of the role
        is_default (bool): Whether this is a system role (True) or custom role (False)
        created_at (str): ISO timestamp when role was created
        updated_at (str): ISO timestamp when role was last updated
        permissions (List[PermissionItem]): List of permissions assigned to this role
    """

    id: str = Field(..., description="Unique identifier for the role")
    name: str = Field(..., description="Human-readable name of the role")
    description: Optional[str] = Field(None, description=DESCRIPTION_OF_THE_ROLE)
    is_default: bool = Field(
        ..., description=IS_DEFAULT_ROLE
    )
    created_at: str = Field(..., description="ISO timestamp when role was created")
    updated_at: str = Field(..., description="ISO timestamp when role was last updated")
    permissions: List[PermissionItem] = Field(
        ..., description="List of permissions assigned to this role"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "Administrator",
                "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                "is_default": True,
                "created_at": "2024-12-13T10:00:00Z",
                "updated_at": "2024-12-19T12:00:00Z",
                "permissions": [
                    {
                        "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "name": "Manage Role Details",
                        "code": SETTINGS_ROLES_MANAGE,
                        "category": "settings",
                        "description": "Allows managing roles and permissions",
                        "created_at": "2024-12-19T11:50:00Z",
                    }
                ],
            }
        }
    )


class RoleDetailResponse(ResponseModel):
    """Response model for single role detail operations

    Attributes:
        status_code (int): HTTP status code
        message (str): Response message describing the operation result
        role (RoleDetailItem): Detailed role information with permissions
    """

    role: RoleDetailItem = Field(
        ..., description="Detailed role information with permissions"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Role details retrieved successfully",
                "role": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "name": "Administrator",
                    "description": EXAMPLE_DESCRIPTION_OF_THE_ROLE,
                    "is_default": True,
                    "created_at": "2024-12-20T10:00:00Z",
                    "updated_at": "2024-12-19T12:00:00Z",
                    "permissions": [],
                }
            }
        }
    )


class CreateRoleRequest(BaseModel):
    """Request model for creating a new role

    Attributes:
        name (str): Name of the role (required)
        description (Optional[str]): Description of the role
        permission_ids (List[str]): List of permission IDs to assign to this role
    """

    name: str = Field(..., min_length=2, max_length=100, description="Name of the role")
    description: Optional[str] = Field(
        None, max_length=500, description=DESCRIPTION_OF_THE_ROLE
    )
    permission_ids: List[str] = Field(
        ..., description="List of permission IDs to assign to this role"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Project Manager",
                "description": "Manages projects and team members",
                "permission_ids": [
                    "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "7ba7b810-9dad-11d1-80b4-00c04fd430c9",
                ],
            }
        }
    )


class CreateRoleResponse(ResponseModel):
    """Response model for role creation operations

    Attributes:
        message (str): Response message describing the operation result
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status_code": 201, "message": "Role created successfully"}
        }
    )


class UpdateRoleRequest(BaseModel):
    """Request model for updating an existing role

    All fields are optional to allow partial updates.

    Attributes:
        name (Optional[str]): Updated name of the role
        description (Optional[str]): Updated description of the role
        is_default (Optional[bool]): Whether this is a system role (True) or custom role (False)
        permission_ids (Optional[List[str]]): List of permission IDs to assign to this role.
                                If provided with values, replaces all existing permissions.
                                If provided as empty array, removes all permissions.
                                If not provided, permissions remain unchanged.
    """

    name: Optional[str] = Field(
        None, min_length=2, max_length=100, description="Updated name of the role"
    )
    description: Optional[str] = Field(
        None, max_length=500, description=f"Updated {DESCRIPTION_OF_THE_ROLE}"
    )
    is_default: Optional[bool] = Field(
        None, description=IS_DEFAULT_ROLE
    )
    permission_ids: Optional[List[str]] = Field(
        None, description="List of permission IDs to assign to this role"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Senior Project Manager",
                "description": "Manages multiple projects and senior team members",
                "is_default": False,
                "permission_ids": [
                    "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "7ba7b810-9dad-11d1-80b4-00c04fd430c9",
                ],
            }
        }
    )


class UpdateRoleResponse(ResponseModel):
    """Response model for role update operations

    Attributes:
        status_code (int): HTTP status code
        message (str): Response message describing the operation result
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status_code": 200, "message": "Role updated successfully"}
        }
    )


class DeleteRoleResponse(ResponseModel):
    """Response model for role deletion operations

    Attributes:
        message (str): Response message describing the operation result
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status_code": 200, "message": "Role deleted successfully"}
        }
    )


class CreatePermissionRequest(BaseModel):
    """Request model for creating a new permission

    Attributes
    ----------
    code : str
        Short, unique code for this permission **within an organisation**
        (must match the `UNIQUE(organization_id, code)` constraint in the DB).

    name : str
        Human-readable name that appears in the UI.

    description : Optional[str]
        Longer explanation of what the permission allows.

    category : Optional[str]
        Logical grouping (e.g. "projects", "users") to help organise the
        permission list.
    """

    code: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Unique code for the permission (e.g. 'project.create')",
    )
    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Display name of the permission",
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Detailed description of what the permission allows",
    )
    category: Optional[str] = Field(
        None,
        max_length=100,
        description="Logical grouping for easier filtering (e.g. 'projects')",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "project.create",
                "name": "Create Projects",
                "description": "Allows the user to create new projects",
                "category": "projects",
            }
        }
    )


# ============================================================================
# SESSION MANAGEMENT SCHEMAS
# ============================================================================


class SessionQueryParams(BaseModel):
    """Query parameters for Sessions API

    Attributes:
        search (Optional[str]): Search term to filter sessions
            by user email or IP address (case-insensitive)
        page (int): Page number for pagination
        page_size (int): Number of sessions per page
        session_status (Optional[str]): Filter by session status (active, inactive, terminated)
        login_method (Optional[str]): Filter by login method (password, sso, mfa)
    """

    search: Optional[str] = Field(
        None,
        description="Search term to filter sessions by user email or IP address (case-insensitive)",
    )
    page: int = Field(1, ge=1, description="Page number for pagination")
    page_size: int = Field(
        20, ge=1, le=100, description="Maximum number of sessions to return (max: 100)"
    )
    session_status: Optional[str] = Field(
        None, description="Filter by session status (active, inactive, terminated)"
    )
    login_method: Optional[str] = Field(
        None, description="Filter by login method (password, sso, mfa)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "192.168.1.1",
                "page": 1,
                "page_size": 20,
                "session_status": "active",
                "login_method": "password",
            }
        }
    )


class SessionItem(BaseModel):
    """Model for session information

    Attributes:
        id (str): Unique session identifier
        user_id (str): User ID associated with the session
        organization_id (str): Organization ID associated with the session
        ip_address (str): IP address from which session was created
        user_agent (str): User agent string
        device_fingerprint (Optional[str]): Device fingerprint for security
        risk_score (int): Security risk score (0-100)
        login_timestamp (str): ISO timestamp when session was created
        logout_timestamp (Optional[str]): ISO timestamp when session was logged out
        session_status (str): Current session status (active, inactive, terminated)
        login_method (str): Method used for login
        accessed_phi (bool): Whether PHI was accessed during session
        phi_access_purpose (Optional[str]): Purpose of PHI access if applicable
    """

    id: str = Field(..., description="Unique session identifier")
    user_id: str = Field(..., description="User ID associated with the session")
    organization_id: str = Field(
        ..., description="Organization ID associated with the session"
    )
    ip_address: str = Field(
        ..., description="IP address from which session was created"
    )
    user_agent: str = Field(..., description="User agent string")
    device_fingerprint: Optional[str] = Field(
        None, description="Device fingerprint for security"
    )
    risk_score: int = Field(..., description="Security risk score (0-100)")
    login_timestamp: str = Field(
        ..., description="ISO timestamp when session was created"
    )
    logout_timestamp: Optional[str] = Field(
        None, description="ISO timestamp when session was logged out"
    )
    session_status: str = Field(..., description="Current session status")
    login_method: str = Field(..., description="Method used for login")
    accessed_phi: bool = Field(
        ..., description="Whether PHI was accessed during session"
    )
    phi_access_purpose: Optional[str] = Field(
        None, description="Purpose of PHI access if applicable"
    )


class SessionsResponse(ResponseModel):
    """Response model for sessions list operations

    Attributes:
        message (str): Response message describing the operation result
        sessions (List[SessionItem]): List of sessions
        total_count (int): Total number of sessions
        page (int): Current page number
        page_size (int): Number of items per page
    """

    sessions: List[SessionItem] = Field(..., description="List of sessions")
    total_count: int = Field(..., description="Total number of sessions")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Sessions retrieved successfully",
                "sessions": [],
                "total_count": 0,
                "page": 1,
                "page_size": 20,
            }
        }
    )


class CreateSessionRequest(BaseModel):
    """Request model for creating a new user session

    Note: This endpoint does not require a request body. All session information
    is extracted from the JWT token and request headers.

    Headers used:
    - Authorization: Bearer <JWT_TOKEN> (contains session_id in jti claim)
    - X-Device-Fingerprint: Device fingerprint for security
    - X-Risk-Score: Security risk score (0-100)
    - X-MFA-Token: MFA token if using multi-factor authentication
    - X-SSO-Provider: SSO provider if using single sign-on
    - User-Agent: Browser/client user agent string
    - X-Forwarded-For: Client IP address (if behind proxy)
    - X-Real-IP: Real client IP address (if behind proxy)
    """

    model_config = ConfigDict(
        json_schema_extra={"example": {"note": "No request body required. "}}
    )


class CreateSessionResponse(ResponseModel):
    """Response model for session creation operations

    Attributes:
        message (str): Response message describing the operation result
        session (SessionItem): Created session information
    """

    session: SessionItem = Field(..., description="Created session information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Session created successfully",
                "session": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "user_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "organization_id": "7ba7b810-9dad-11d1-80b4-00c04fd430c9",
                    "ip_address": "192.168.1.1",
                    "user_agent": "Mozilla/5.0...",
                    "device_fingerprint": "abc123def456",
                    "risk_score": 25,
                    "login_timestamp": "2024-12-19T10:30:00Z",
                    "logout_timestamp": None,
                    "session_status": "active",
                    "login_method": "password",
                    "accessed_phi": False,
                    "phi_access_purpose": None,
                }
            }
        }
    )


class UpdateSessionRequest(BaseModel):
    """Request model for updating session logout information

    Note: This endpoint extracts the session ID from the JWT token automatically.
    No request body is required - the endpoint automatically sets logout timestamp
    and session status to 'inactive'.

    Attributes:
        session_status (Optional[str]): New session status (inactive, terminated)
        accessed_phi (Optional[bool]): Whether PHI was accessed during session
        phi_access_purpose (Optional[str]): Purpose of PHI access if applicable
        logout_reason (Optional[str]): Reason for logout
             (user_logout, timeout, admin_terminated, etc.)
    """

    session_status: Optional[str] = Field(
        None, description="New session status (inactive, terminated)"
    )
    accessed_phi: Optional[bool] = Field(
        None, description="Whether PHI was accessed during session"
    )
    phi_access_purpose: Optional[str] = Field(
        None, max_length=500, description="Purpose of PHI access if applicable"
    )
    logout_reason: Optional[str] = Field(
        None,
        max_length=200,
        description="Reason for logout (user_logout, timeout, admin_terminated, etc.)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "note": "No request body required. Session logout is automatic based on JWT token."
            }
        }
    )


class UpdateSessionResponse(ResponseModel):
    """Response model for session update operations

    Attributes:
        message (str): Response message describing the operation result
        session (SessionItem): Updated session information
    """

    session: SessionItem = Field(..., description="Updated session information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Session logout updated successfully",
                "session": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "user_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "organization_id": "7ba7b810-9dad-11d1-80b4-00c04fd430c9",
                    "ip_address": "192.168.1.1",
                    "user_agent": "Mozilla/5.0...",
                    "device_fingerprint": "abc123def456",
                    "risk_score": 25,
                    "login_timestamp": "2024-12-19T10:30:00Z",
                    "logout_timestamp": "2024-12-19T11:30:00Z",
                    "session_status": "inactive",
                    "login_method": "password",
                    "accessed_phi": False,
                    "phi_access_purpose": None,
                }
            }
        }
    )
