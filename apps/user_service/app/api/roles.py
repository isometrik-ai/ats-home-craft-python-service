"""Roles Management API Module."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.admin_access_management import (
    CreateRoleRequest,
    CreateRoleResponse,
    RoleDetailItem,
    RoleDetailResponse,
    RolesResponse,
    UpdateRoleRequest,
    UpdateRoleResponse,
)
from apps.user_service.app.services.role_service import RoleService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    ROLES_MANAGEMENT_CREATE,
    ROLES_MANAGEMENT_DELETE,
    ROLES_MANAGEMENT_EDIT,
    ROLES_MANAGEMENT_VIEW,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("roles-api")
router = APIRouter(prefix="/roles", tags=["Roles Management"])


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
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        None, description="Search term to filter roles by name (case-insensitive)"
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """Get all roles for the current organization"""
    user_context = await check_permissions(current_user, db_connection, ROLES_MANAGEMENT_VIEW)

    role_service = RoleService(db_connection=db_connection, user_context=user_context)
    roles, total_count = await role_service.list_roles(
        search=search, limit=page_size, offset=(page - 1) * page_size
    )

    if not roles:
        return success_response(
            request=request,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_204_NO_CONTENT,
        )

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
    db_connection: asyncpg.Connection = Depends(db_conn),
    role_id: UUID = Path(..., description="The UUID of the role to get"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get role by ID with all associated permissions"""
    user_context = await check_permissions(current_user, db_connection, ROLES_MANAGEMENT_VIEW)

    role_service = RoleService(db_connection=db_connection, user_context=user_context)
    role_detail: RoleDetailItem = await role_service.get_role_details(str(role_id))

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
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a new role with associated permissions"""
    request.state.audit_table = "roles"
    request.state.audit_description = f"Created new role: {role_data.name}"
    request.state.audit_risk_level = "medium"

    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=ROLES_MANAGEMENT_CREATE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    role_service = RoleService(db_connection=db_connection, user_context=user_context)
    created_role = await role_service.create_role(role_data)

    request.state.raw_audit_new_data = {
        "role_id": str(created_role["id"]),
        "role_name": role_data.name,
        "description": role_data.description,
        "permission_ids": role_data.permission_ids,
        "organization_id": user_context.organization_id,
        "created_at": created_role["created_at"],
    }
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
    db_connection: asyncpg.Connection = Depends(db_uow),
    role_id: UUID = Path(..., description="The UUID of the role to update"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update an existing role's properties and permissions"""
    request.state.audit_requested_id = role_id

    user_context = await check_permissions(current_user, db_connection, ROLES_MANAGEMENT_EDIT)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    role_service = RoleService(db_connection=db_connection, user_context=user_context)

    existing_role_detail = await role_service.get_role_details(str(role_id))
    permission_ids = [str(perm.id) for perm in existing_role_detail.permissions]

    request.state.audit_table = "roles"
    request.state.audit_description = f"Updated role: {existing_role_detail.name}"
    request.state.audit_risk_level = "medium"
    request.state.raw_audit_old_data = {
        "role_id": str(existing_role_detail.id),
        "role_name": existing_role_detail.name,
        "description": existing_role_detail.description,
        "is_default": existing_role_detail.is_default,
        "permission_ids": permission_ids,
        "organization_id": user_context.organization_id,
    }

    await role_service.update_role(str(role_id), role_data)

    updated_role_detail = await role_service.get_role_details(str(role_id))
    updated_permission_ids = [str(perm.id) for perm in updated_role_detail.permissions]

    request.state.raw_audit_new_data = {
        "role_id": str(updated_role_detail.id),
        "role_name": updated_role_detail.name,
        "description": updated_role_detail.description,
        "is_default": updated_role_detail.is_default,
        "permission_ids": updated_permission_ids,
        "organization_id": user_context.organization_id,
    }

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
    db_connection: asyncpg.Connection = Depends(db_uow),
    role_id: UUID = Path(..., description="The UUID of the role to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an existing role"""
    request.state.audit_requested_id = role_id

    user_context = await check_permissions(current_user, db_connection, ROLES_MANAGEMENT_DELETE)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    role_service = RoleService(db_connection=db_connection, user_context=user_context)

    existing_role_detail = await role_service.get_role_details(str(role_id))
    current_permission_ids = [str(perm.id) for perm in existing_role_detail.permissions]

    request.state.audit_table = "roles"
    request.state.audit_requested_id = role_id
    request.state.audit_description = f"Deleted role: {existing_role_detail.name}"
    request.state.audit_risk_level = "high"
    request.state.raw_audit_old_data = {
        "role_id": str(existing_role_detail.id),
        "role_name": existing_role_detail.name,
        "description": existing_role_detail.description,
        "is_default": existing_role_detail.is_default,
        "permission_ids": current_permission_ids,
        "organization_id": user_context.organization_id,
    }

    await role_service.delete_role(str(role_id))

    request.state.raw_audit_new_data = {
        "role_id": str(existing_role_detail.id),
        "role_name": existing_role_detail.name,
        "description": "ROLE_DELETED",
        "is_default": existing_role_detail.is_default,
        "permission_ids": [],
        "organization_id": user_context.organization_id,
    }
    return success_response(
        request=request,
        message_key="roles.success.deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
