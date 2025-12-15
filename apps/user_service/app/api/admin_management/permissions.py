"""Permissions Management API Module
This module provides CRUD operations for permission management.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    check_permissions,
    format_permissions_data,
    handle_api_exceptions,
)

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    CreatePermissionRequest,
)

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.permission_operations import (
    create_new_permission,
    delete_permission,
    get_all_permissions,
    get_permission_details_by_id,
)
from libs.shared_middleware.jwt_auth import (
    get_user_from_auth,
)

# Local imports
from libs.shared_utils.common_query import SETTINGS_ROLES_MANAGE, SETTINGS_SYSTEM_MANAGE
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for permissions endpoints
router = APIRouter(prefix="/permissions", tags=["Permissions Management"])

# Initialize logger for permissions module
logger = get_logger("permissions-api")


@handle_api_exceptions("get permissions")
@router.get(
    "",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get all permissions for the current organization",
    summary="Get all permissions for the current organization",
    responses={
        http_status.HTTP_200_OK: {"description": "Permissions retrieved successfully"},
        http_status.HTTP_204_NO_CONTENT: {"description": "No permissions found"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_permissions(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """Get all permissions for the current organization"""

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_ROLES_MANAGE,
    )

    permissions_data = await get_all_permissions(organization_id=user_context.organization_id)

    if not permissions_data:
        return success_response(
            request=request,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_204_NO_CONTENT,
        )

    # Format permissions data efficiently
    permissions = format_permissions_data(permissions_data)

    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=permissions,
    )


@handle_api_exceptions("get permission by ID")
@router.get(
    "/{permission_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get permission by ID",
    summary="Get permission by ID",
    responses={
        http_status.HTTP_200_OK: {"description": "Permission retrieved successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Permission not found"},
    },
)
@limiter.limit("100/minute")
async def get_permission_by_id(
    request: Request,
    permission_id: str = Path(..., description="The ID of the permission to retrieve"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get permission by ID"""
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_ROLES_MANAGE,
    )

    permission = await get_permission_details_by_id(
        permission_id=permission_id, organization_id=user_context.organization_id
    )

    if not permission:
        raise NotFoundException(
            message_key="permissions.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    permissions = format_permissions_data([permission])

    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=permissions,
    )


@handle_api_exceptions("create permission")
@router.post(
    "",
    response_model=None,
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new permission",
    summary="Create a new permission",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Permission created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=["access_control", "soc2_audit", "audit_required"],
    table_name="permissions",
    category="PERMISSION",
)
async def create_permission(
    request: Request,
    permission_data: CreatePermissionRequest,
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a new permission

    Returns:
        PermissionResponse: Success message indicating API is working
    """
    # Set audit context for permission creation
    request.state.audit_table = "permissions"
    request.state.audit_description = (
        f"Created new permission: {permission_data.name} (code: {permission_data.code})"
    )
    request.state.audit_risk_level = "medium"

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    permission = await create_new_permission(
        permission_data=permission_data,
        organization_id=user_context.organization_id,
    )

    if not permission:
        raise ValidationException(
            message_key="permissions.errors.creation_failed",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    permissions = format_permissions_data([permission])

    # Set audit data for successful creation
    request.state.raw_audit_new_data = {
        "permission_id": str(permission["id"]),
        "permission_name": permission["name"],
        "permission_code": permission["code"],
        "permission_category": permission["category"],
        "permission_description": permission["description"],
        "organization_id": user_context.organization_id,
        "created_at": (permission["created_at"] if permission["created_at"] else None),
    }
    return success_response(
        request=request,
        message_key="permissions.success.creation_successful",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=permissions,
    )


@router.delete(
    "/{permission_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Delete a permission by ID",
    summary="Delete a permission by ID",
    responses={
        http_status.HTTP_200_OK: {"description": "Permission deleted successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Permission not found"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=["access_control", "soc2_audit", "audit_required"],
    table_name="permissions",
    category="PERMISSION",
)
async def delete_permission_by_id(
    request: Request,
    permission_id: str = Path(..., description="The ID of the permission to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a permission by ID"""
    # Set audit context for permission deletion
    request.state.audit_requested_id = str(permission_id)
    request.state.audit_table = "permissions"
    request.state.audit_description = f"Deleted permission with ID: {permission_id}"
    request.state.audit_risk_level = "high"

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    permission = await get_permission_details_by_id(permission_id, user_context.organization_id)
    if not permission:
        raise NotFoundException(
            message_key="permissions.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Set audit data for deletion (placeholder since this endpoint is not fully implemented)
    current_timestamp = datetime.now(timezone.utc).isoformat()
    request.state.raw_audit_old_data = {
        "permission_id": str(permission_id),
        "deletion_timestamp": current_timestamp,
    }

    request.state.raw_audit_new_data = {
        "permission_id": str(permission_id),
        "status": "DELETED",
        "deletion_timestamp": current_timestamp,
    }

    result = await delete_permission(permission_id, user_context.organization_id)
    if not result:
        raise ValidationException(
            message_key="permissions.errors.deletion_failed",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    return success_response(
        request=request,
        message_key="permissions.success.deletion_successful",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
