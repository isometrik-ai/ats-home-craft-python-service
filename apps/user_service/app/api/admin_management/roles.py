"""Roles Management API Module
This module provides CRUD operations for role management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (  # adjust path as needed
    audit_api_call,
)

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    CreateRoleRequest,
    CreateRoleResponse,
    RoleDetailItem,
    RoleDetailResponse,
    RoleItem,
    RolesResponse,
    UpdateRoleRequest,
    UpdateRoleResponse,
)

# Utility imports
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    format_iso_datetime,
    format_permissions_data,
    handle_api_exceptions,
    safe_json_loads,
    validate_uuid_format,
)
from libs.shared_db.postgres_db.user_service_operations.permission_operations import (
    get_permission_details_by_id,
)
from libs.shared_db.postgres_db.user_service_operations.role_operations import (
    assign_permissions_to_role,
    check_permissions_exist,
    check_role_exists,
    check_role_name_unique,
    check_role_usage,
    create_role,
    delete_role,
    get_role_by_id,
    get_role_permissions,
    get_roles_count,
    get_roles_list,
    update_role,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Local imports
from libs.shared_utils.common_query import SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Initialize logger for roles module
logger = get_logger("roles-api")

# Create router for roles endpoints
router = APIRouter(prefix="/roles", tags=["Roles Management"])


async def check_permission_exist_in_organization(role_data, user_context):
    """Check if permissions exist in organization"""
    permissions_exist = await check_permissions_exist(
        permission_ids=role_data.permission_ids,
        organization_id=user_context.organization_id,
    )
    if not permissions_exist:
        raise ForbiddenException(
            message_key="errors.forbidden",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


@handle_api_exceptions("get roles")
@router.get(
    "",
    response_model=RolesResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get all roles for the current organization",
    summary="Get all roles for the current organization",
    responses={
        http_status.HTTP_200_OK: {"description": "Roles retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_roles(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        None, description="Search term to filter roles by name (case-insensitive)"
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """Get all roles for the current organization"""
    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user, SETTINGS_ROLES_MANAGE)

    # Get roles using centralized operations
    roles_data = await get_roles_list(
        organization_id=user_context.organization_id,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    if not roles_data:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            page=page,
            page_size=page_size,
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
            permission_ids=[
                str(perm["id"])
                for perm in await get_role_permissions(role["id"], user_context.organization_id)
            ],
            permission_count=role["permission_count"],
            permission_categories=safe_json_loads(role["permission_categories"], {}),
        )
        for role in roles_data
    ]

    total_count = await get_roles_count(organization_id=user_context.organization_id, search=search)

    return list_response(
        request=request,
        items=roles,
        total=total_count,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        page=page,
        page_size=page_size,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get role by ID")
@router.get(
    "/{role_id}",
    response_model=RoleDetailResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get role by ID",
    summary="Get role by ID",
    responses={
        http_status.HTTP_200_OK: {
            "model": RoleDetailResponse,
            "description": "Role retrieved successfully",
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Role not found"},
    },
)
@limiter.limit("100/minute")
async def get_role_from_id(
    request: Request,
    role_id: UUID = Path(..., description="The UUID of the role to get"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get role by ID with all associated permissions"""
    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user, [SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE]
    )

    # Get role details using centralized operation
    role_data = await get_role_by_id(role_id, user_context.organization_id)

    # Check if role exists in user's organization
    if not role_data:
        raise NotFoundException(
            message_key="roles.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Get permissions for this role using centralized operation
    role_permissions_data = await get_role_permissions(role_id, user_context.organization_id)

    permissions_data = await asyncio.gather(
        *[
            get_permission_details_by_id(permission["permission_id"], user_context.organization_id)
            for permission in role_permissions_data
        ]
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
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=role_detail,
    )


@handle_api_exceptions("create role")
@router.post(
    "",
    response_model=CreateRoleResponse,
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new role",
    summary="Create a new role",
    responses={
        http_status.HTTP_201_CREATED: {
            "model": CreateRoleResponse,
            "description": "Role created successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
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
    request: Request,
    role_data: CreateRoleRequest,
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a new role with associated permissions"""
    # Validate role type using utility function
    # Set audit context for role creation
    request.state.audit_table = "roles"
    request.state.audit_description = f"Created new role: {role_data.name}"
    request.state.audit_risk_level = "medium"

    # Validate permission IDs format using utility function
    if role_data.permission_ids:
        for uuid_str in role_data.permission_ids:
            validate_uuid_format(uuid_str, "permission ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_ROLES_MANAGE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Validate that all permission IDs exist in the organization using centralized operation
    await check_permission_exist_in_organization(role_data, user_context)

    # Check if role name is unique using centralized operation
    name_unique = await check_role_name_unique(
        name=role_data.name,
        organization_id=user_context.organization_id,
    )
    if not name_unique:
        raise ConflictException(
            message_key="roles.errors.role_name_already_exists",
            params={"role_name": role_data.name},
            custom_code=CustomStatusCode.CONFLICT,
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
    return success_response(
        request=request,
        message_key="roles.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update role")
@router.put(
    "/{role_id}",
    response_model=UpdateRoleResponse,
    status_code=http_status.HTTP_200_OK,
    description="Update an existing role",
    summary="Update an existing role",
    responses={
        http_status.HTTP_200_OK: {
            "model": UpdateRoleResponse,
            "description": "Role updated successfully",
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Role not found"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
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
    request: Request,
    role_data: UpdateRoleRequest,
    role_id: UUID = Path(..., description="The UUID of the role to update"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update an existing role's properties and permissions"""
    request.state.audit_requested_id = role_id

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user, [SETTINGS_ROLES_MANAGE, SETTINGS_USERS_MANAGE]
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Check if role exists in organization using centralized operation
    role_exists = await check_role_exists(role_id, user_context.organization_id)
    if not role_exists:
        raise NotFoundException(
            message_key="roles.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

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
    if role_data.permission_ids is not None and len(role_data.permission_ids) > 0:
        for uuid_str in role_data.permission_ids:
            validate_uuid_format(uuid_str, "permission ID")

        await check_permission_exist_in_organization(role_data, user_context)

    # Check if new name conflicts with existing roles using centralized operation
    if role_data.name is not None and role_data.name != existing_role["name"]:
        name_unique = await check_role_name_unique(
            name=role_data.name,
            organization_id=user_context.organization_id,
            exclude_role_id=role_id,
        )
        if not name_unique:
            raise ConflictException(
                message_key="roles.errors.role_name_already_exists",
                params={"role_name": role_data.name},
                custom_code=CustomStatusCode.CONFLICT,
            )

    # Prepare update data for centralized operation
    update_data = {
        key: value
        for key, value in {
            "name": role_data.name,
            "description": role_data.description,
            "is_default": role_data.is_default,
        }.items()
        if value is not None
    }

    # Update the role using centralized operation
    if update_data:
        await update_role(role_id, user_context.organization_id, update_data)

    # Handle permissions update if provided using centralized operations
    # Add new permissions if any are provided using centralized operation
    if role_data.permission_ids is not None and len(role_data.permission_ids) > 0:
        await assign_permissions_to_role(
            role_id=role_id,
            organization_id=user_context.organization_id,
            permission_ids=role_data.permission_ids,
        )

    # Set new values for audit comparison
    new_role_data = {
        "role_id": role_id,
        "role_name": (role_data.name if role_data.name is not None else existing_role["name"]),
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

    return success_response(
        request=request,
        message_key="roles.success.updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete role")
@router.delete(
    "/{role_id}",
    status_code=http_status.HTTP_200_OK,
    description="Delete an existing role",
    summary="Delete an existing role",
    responses={
        http_status.HTTP_200_OK: {"description": "Role deleted successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Role not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
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
    request: Request,
    role_id: UUID = Path(..., description="The UUID of the role to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an existing role"""
    request.state.audit_requested_id = role_id

    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user, SETTINGS_ROLES_MANAGE)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Check if role exists in organization using centralized operation
    role_exists = await check_role_exists(role_id, user_context.organization_id)
    if not role_exists:
        raise NotFoundException(
            message_key="roles.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

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
        raise ConflictException(
            message_key="roles.errors.role_in_use",
            params={"member_count": member_count},
            custom_code=CustomStatusCode.CONFLICT,
        )

    # Delete the role using centralized operation
    role_deleted = await delete_role(role_id, user_context.organization_id)

    if not role_deleted:
        raise NotFoundException(
            message_key="roles.errors.role_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
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
    return success_response(
        request=request,
        message_key="roles.success.deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
