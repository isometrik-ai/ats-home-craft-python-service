"""
Roles Management API Module

This module provides CRUD operations for role management.
All endpoints include proper authentication, validation, and database operations.

"""
from datetime import datetime, timezone
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Logger import
from apps.user_service.app.dependencies.logger import get_logger
# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    format_iso_datetime,
    safe_json_loads,
    validate_uuid_format,
    check_permissions,
    format_permissions_data,
)
from apps.user_service.app.dependencies.roles_utils import (
    build_role_filter_message,
)

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    RolesResponse,
    RoleItem,
    RoleQueryParams,
    RoleDetailResponse,
    RoleDetailItem,
    CreateRoleRequest,
    CreateRoleResponse,
    UpdateRoleRequest,
    UpdateRoleResponse
)

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)  # adjust path as needed

# Local imports
from libs.shared_utils.common_query import SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.postgres_db.user_service_operations.role_operations import (
    create_role,
    get_role_by_id,
    get_roles_list,
    get_roles_count,
    get_role_permissions,
    update_role,
    delete_role,
    check_role_exists,
    assign_permissions_to_role,
    check_role_usage,
    check_permissions_exist,
    check_role_name_unique,
)
from libs.shared_db.postgres_db.user_service_operations.permission_operations import (
    get_permission_details_by_id,
)

# Initialize logger for roles module
logger = get_logger("roles-api")

# Create router for roles endpoints
router = APIRouter(prefix="/roles", tags=["Roles Management"])

role_data_permission_validity = lambda obj: obj is not None and len(obj) > 0

def check_role_data_exist(data_object: dict, request_id: str):
    """
    Check if role data exists
    """
    if not data_object:
        logger.warning("Role not found or access denied - Request ID: %s",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found or access denied",
        )

async def check_permission_exist_in_organization(
    role_data, user_context, request_id):
    """
    Check if permissions exist in organization
    """
    permissions_exist = await check_permissions_exist(
        permission_ids=role_data.permission_ids,
        organization_id=user_context.organization_id,
    )
    if not permissions_exist:
        logger.warning(
            "Invalid permission IDs provided - Request ID: %s, ",
            request_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more permission IDs are invalid",
        )


class RoleResponse(BaseModel):
    """Response model for role operations"""

    message: str
    status: str = "success"

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {"message": self.message, "status": self.status}


# @handle_api_exceptions("get roles")
@router.get("", response_model=RolesResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="general",
#     compliance_tags=["access_control", "soc2_audit"],
#     table_name="roles",
#     category="ROLE",
# )
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_roles(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: RoleQueryParams = Depends()
):
    """
    Get all roles for the current organization (Optimized & Truly Async)

    This endpoint retrieves all roles for the authenticated user's organization.
    Uses truly async database operations for best performance and scalability.


    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        query_params (RoleQueryParams): Query parameters object containing
            search, pagination, and filter options

    Returns:
        RolesResponse: List of roles with id, name, description, is_default,
            and created_at

    Raises:
        HTTPException: 400 for invalid parameters
        HTTPException: 403 for insufficient permissions
        HTTPException: 500 for database errors
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user, SETTINGS_ROLES_MANAGE,
        action_description="access role list")

    # Get roles using centralized operations
    roles_data = await get_roles_list(
        organization_id=user_context.organization_id,
        search=query_params.search,
        limit=query_params.limit,
        offset=query_params.skip,
    )

    # Get total count using centralized operation
    total_count = await get_roles_count(
        organization_id=user_context.organization_id,
        search=query_params.search,
    )

    # Format roles data using utility functions
    roles = [
        RoleItem(
            id=str(role["id"]),
            name=role["name"],
            description=role["description"],
            is_default=role["is_default"],
            created_at=format_iso_datetime(role["created_at"]) or "",
            user_count=role["user_count"],
            permission_count=role["permission_count"],
            permission_categories=safe_json_loads(role["permission_categories"], {}),
        )
        for role in roles_data
    ]

    # Build response message using utility function
    message = build_role_filter_message(
        search=query_params.search,
        skip=query_params.skip,
        limit=query_params.limit,
    )

    return RolesResponse(
        message=message,
        roles=roles,
        total_count=max(total_count, len(roles))
    )


# @handle_api_exceptions("get role by ID")
@router.get(
    "/{role_id}", response_model=RoleDetailResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",
#         "pii",
#         "audit_required",
#     ],
#     table_name="roles",
#     category="ROLE",
# )
async def get_role_from_id(
    role_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Get role by ID with all associated permissions (Optimized & Truly Async)

    Args:
        role_id (str): UUID of the role to retrieve
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        RoleDetailResponse: Detailed role information with associated permissions

    """
    request_id = str(uuid.uuid4())

    # Validate role_id format using utility function
    request.state.audit_requested_id = role_id
    validate_uuid_format(role_id, "role ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user, [SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE])

    # Get role details using centralized operation
    role_data = await get_role_by_id(role_id, user_context.organization_id)

    # Check if role exists in user's organization
    check_role_data_exist(role_data, request_id)

    # Get permissions for this role using centralized operation
    role_permissions_data = await get_role_permissions(role_id, user_context.organization_id)

    permissions_data = await asyncio.gather(
        *[get_permission_details_by_id(permission["permission_id"], user_context.organization_id)
        for permission in role_permissions_data]
    )

    # Format permissions data using utility functions
    permissions = format_permissions_data(permissions_data)

    # Format role data using utility functions
    role_detail = RoleDetailItem(
        id=str(role_data["id"]),
        name=role_data["name"],
        description=role_data["description"],
        is_default=role_data["is_default"],
        created_at=format_iso_datetime(role_data["created_at"]) or "",
        updated_at=format_iso_datetime(role_data["updated_at"]) or "",
        permissions=permissions,
    )

    return RoleDetailResponse(
        message=(
            f"Role details retrieved successfully "
            f"(found {len(permissions)} permissions)"
        ),
        role=role_detail,
    )


# @handle_api_exceptions("create role")
@router.post("", response_model=CreateRoleResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role creation affects access control and security policies.
        "soc2_audit",  # Role management is critical for SOC2 compliance and access governance.
        "audit_required",  # Role creation must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def create_new_role(
    role_data: CreateRoleRequest,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Create a new role with associated permissions (Optimized & Truly Async

    Args:
        role_data (CreateRoleRequest): Role creation data including name, type,
            description, and permission IDs
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        CreateRoleResponse: Created role information with associated permissions

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    # Validate role type using utility function
    # Set audit context for role creation
    request.state.audit_table = "roles"
    request.state.audit_description = (
        f"Created new role: {role_data.name}"
    )
    request.state.audit_risk_level = "medium"

    # Validate permission IDs format using utility function
    if role_data.permission_ids:
        for uuid_str in role_data.permission_ids:
            validate_uuid_format(uuid_str, "permission ID")

    # Extract and validate user context from JWT token
    # Check permission using utility function
    user_context = await check_permissions(current_user=current_user,
        permission_codes=SETTINGS_ROLES_MANAGE, action_description="create role")

    # Validate that all permission IDs exist in the organization using centralized operation
    await check_permission_exist_in_organization(role_data, user_context, request_id)

    # Check if role name is unique using centralized operation
    name_unique = await check_role_name_unique(
        name=role_data.name,
        organization_id=user_context.organization_id,
    )
    if not name_unique:
        logger.warning(
            "Role name already exists - Request ID: %s, ",
            request_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role name already exists",
        )

    # Create the role using centralized operation
    created_role = await create_role(
        name=role_data.name,
        description=role_data.description,
        organization_id=user_context.organization_id,
    )

    role_id = created_role["id"]

    # Set audit context with new role data
    request.state.raw_audit_new_data = {
        "role_id": str(role_id),
        "role_name": role_data.name,
        "description": role_data.description,
        "permission_ids": role_data.permission_ids,
        "organization_id": user_context.organization_id,
        "created_at": created_role["created_at"],
    }

    # Assign permissions to the role using centralized operation
    await assign_permissions_to_role(
        role_id=str(role_id),
        organization_id=user_context.organization_id,
        permission_ids=role_data.permission_ids,
    )

    return CreateRoleResponse(
        message="Role created successfully"
    )


def _build_update_response_message(role_data: UpdateRoleRequest):
    """Build response message based on what was updated."""
    updated_fields = []

    if role_data.name is not None:
        updated_fields.append("name")
    if role_data.description is not None:
        updated_fields.append("description")
    if role_data.is_default is not None:
        updated_fields.append("type")
    if role_data.permission_ids is not None:
        if len(role_data.permission_ids) == 0:
            updated_fields.append("permissions (removed all)")
        else:
            updated_fields.append(
                f"permissions ({len(role_data.permission_ids)} assigned)"
            )

    if not updated_fields:
        return "No changes were made to the role"

    return f"Role updated successfully. Updated: {', '.join(updated_fields)}"


# @handle_api_exceptions("update role")
@router.put(
    "/{role_id}",
    response_model=UpdateRoleResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role updates affect access control and security policies.
        "soc2_audit",  # Role modifications are critical for SOC2 compliance and access governance.
        "audit_required",  # Role updates must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def update_role_data(
    role_id: str,
    role_data: UpdateRoleRequest,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Update an existing role's properties and permissions (Optimized & Truly Async)

    Update Behavior:
    - name: If provided, updates the role name (validates uniqueness)
    - description: If provided, updates the role description
    - is_default: If provided, updates the role type (system vs custom)
    - permission_ids: If provided:
        * Array with values: Replaces all existing permissions with these
        * Empty array: Removes all permissions from the role
        * Not provided: Leaves permissions unchanged

    Args:
        role_id (str): UUID of the role to update
        role_data (UpdateRoleRequest): Updated role data (all fields optional)
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        UpdateRoleResponse: Success message describing what was updated

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    request.state.audit_requested_id = role_id

    # Validate role_id format using utility function
    validate_uuid_format(role_id, "role_ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user, [SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE],
        "Update Role Data")

    # Check if role exists in organization using centralized operation
    role_exists = await check_role_exists(role_id, user_context.organization_id)
    check_role_data_exist(role_exists, request_id)

    # Get existing role data using centralized operation
    existing_role = await get_role_by_id(role_id, user_context.organization_id)

    # Get current permissions for the role using centralized operation
    current_permissions = await get_role_permissions(role_id, user_context.organization_id)

    # Set audit context for role update
    request.state.audit_table = "roles"
    request.state.audit_description = f"Updated role: {existing_role['name']}"
    request.state.audit_risk_level = "medium"

    # Set old values for audit comparison
    request.state.raw_audit_old_data = {
        "role_id": role_id,
        "role_name": existing_role["name"],
        "description": existing_role["description"],
        "is_default": existing_role["is_default"],
        "permission_ids": current_permissions,
        "organization_id": user_context.organization_id,
    }

    # Validate permission IDs if provided using centralized operation
    if role_data_permission_validity(role_data.permission_ids):
        for uuid_str in role_data.permission_ids:
            validate_uuid_format(uuid_str, "permission ID")

        await check_permission_exist_in_organization(role_data, user_context, request_id)

    # Check if new name conflicts with existing roles using centralized operation
    if role_data.name is not None and role_data.name != existing_role["name"]:
        name_unique = await check_role_name_unique(
            name=role_data.name,
            organization_id=user_context.organization_id,
            exclude_role_id=role_id,
        )
        if not name_unique:
            logger.warning(
                "Role name already exists - Request ID: %s, ",
                request_id
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role name already exists",
            )

    # Prepare update data for centralized operation
    update_data = {
        key: value for key, value in {
            "name": role_data.name,
            "description": role_data.description,
            "is_default": role_data.is_default
        }.items() if value is not None
    }

    # Update the role using centralized operation
    if update_data:
        await update_role(role_id, user_context.organization_id, update_data)

    # Handle permissions update if provided using centralized operations
    # Add new permissions if any are provided using centralized operation
    if role_data_permission_validity(role_data.permission_ids):
        await assign_permissions_to_role(
            role_id=role_id,
            organization_id=user_context.organization_id,
            permission_ids=role_data.permission_ids,
        )

    # Build response message based on what was updated
    message = _build_update_response_message(role_data)

    # Set new values for audit comparison
    new_role_data = {
        "role_id": role_id,
        "role_name": (
            role_data.name if role_data.name is not None else existing_role["name"]
        ),
        "description": (
            role_data.description
            if role_data.description is not None
            else existing_role["description"]
        ),
        "is_default": (
            role_data.is_default
            if role_data.is_default is not None
            else existing_role["is_default"]
        ),
        "permission_ids": role_data.permission_ids,
        "organization_id": user_context.organization_id,
    }

    request.state.raw_audit_new_data = new_role_data

    return UpdateRoleResponse(
        message=message,
    )


# @handle_api_exceptions("delete role")
@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "access_control",  # Role deletion affects access control and security policies.
        "soc2_audit",  # Role deletion is critical for SOC2 compliance and access governance.
        "audit_required",  # Role deletion must be logged for compliance and security audits.
    ],
    table_name="roles",
    category="ROLE",
)
async def delete_role_data(
    role_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Delete an existing role (Optimized & Truly Async)

    Args:
        role_id (str): UUID of the role to delete
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        DeleteRoleResponse: Success message for role deletion
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    request.state.audit_requested_id = role_id

    # Validate role_id format using utility function
    validate_uuid_format(role_id, "role ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user, SETTINGS_ROLES_MANAGE)

    # Check if role exists in organization using centralized operation
    role_exists = await check_role_exists(role_id, user_context.organization_id)
    check_role_data_exist(role_exists, request_id)

    # Get existing role data using centralized operation
    existing_role = await get_role_by_id(role_id, user_context.organization_id)

    # Get current permissions for the role using centralized operation
    current_permissions = await get_role_permissions(role_id, user_context.organization_id)
    current_permission_ids = [str(perm["id"]) for perm in current_permissions]

    # Set audit context for role deletion
    request.state.audit_table = "roles"
    request.state.audit_requested_id = role_id
    request.state.audit_description = f"Deleted role: {existing_role['name']}"
    request.state.audit_risk_level = "high"

    # Set old values for audit comparison (what was deleted)
    request.state.raw_audit_old_data = {
        "role_id": role_id,
        "role_name": existing_role["name"],
        "description": existing_role["description"],
        "is_default": existing_role["is_default"],
        "permission_ids": current_permission_ids,
        "organization_id": user_context.organization_id,
    }

    # Check if role is in use by organization members using centralized operation
    member_count = await check_role_usage(role_id, user_context.organization_id)

    if member_count > 0:
        logger.warning("Cannot delete role - it is currently in use - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete role. It is currently assigned to "
                f"{member_count} organization member(s)"
            ),
        )

    # Delete the role using centralized operation
    role_deleted = await delete_role(role_id, user_context.organization_id)

    if not role_deleted:
        logger.warning("Role not found or already deleted - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found or already deleted",
        )

    # Set new values for audit comparison (empty since role was deleted)
    request.state.raw_audit_new_data = {
        "role_id": role_id,
        "role_name": existing_role["name"],
        "description": "ROLE_DELETED",
        "is_default": existing_role["is_default"],
        "permission_ids": [],
        "organization_id": user_context.organization_id,
        "deletion_timestamp": datetime.now(timezone.utc).isoformat(),
    }
