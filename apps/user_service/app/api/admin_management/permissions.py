"""
Permissions Management API Module

This module provides CRUD operations for permission management.
All endpoints include proper authentication, validation, and database operations.

"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    validate_uuid_format,
    handle_api_exceptions,
    format_permissions_data
)

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    PermissionsResponse,
    CreatePermissionRequest,
)


from apps.user_service.app.dependencies.common_utils import check_permissions

from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_utils.common_query import (
    SETTINGS_ROLES_MANAGE,
    SETTINGS_SYSTEM_MANAGE
)
from libs.shared_middleware.jwt_auth import (
    get_user_from_auth,
)
# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.permission_operations import (
    get_permission_details_by_id,
    get_all_permissions,
    create_new_permission,
    delete_permission,
)

# Import DatabaseOperationError for manual error handling
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    DatabaseOperationError
)

# Create router for permissions endpoints
router = APIRouter(prefix="/permissions", tags=["Permissions Management"])

# Initialize logger for permissions module
logger = get_logger("permissions-api")

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"
PERMISSIONS_RETRIEVED_SUCCESSFULLY_MESSAGE = "Permissions retrieved successfully"


class PermissionResponse(BaseModel):
    """Response model for permission operations"""

    message: str
    status: str = "success"


@handle_api_exceptions("get permissions")
@router.get("", response_model=PermissionsResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="general",
#     compliance_tags=["access_control", "soc2_audit"],
#     table_name="permissions",
#     category="PERMISSION",
# )
async def get_permissions(
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Get all permissions for the current organization (Optimized & Truly Async)

    This endpoint retrieves all permissions available in the organization.
    Uses truly async database operations for best performance and scalability.

    Performance Features:
    - JWT authentication
    - Async database operations (non-blocking)
    - Connection pooling
    - Efficient queries with proper indexing

    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        PermissionsResponse: List of permissions with id, name,
          code, category, description, and created_at
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for permissions listing
    request.state.audit_table = "permissions"
    request.state.audit_description = "Retrieved all permissions for organization"
    request.state.audit_risk_level = "low"

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user, permission_codes=SETTINGS_ROLES_MANAGE,
        action_description="get permissions"
    )

    try:
        permissions_data = await get_all_permissions(user_context.organization_id)
    except DatabaseOperationError as e:
        logger.error(
            "Database error retrieving permissions - Request ID: %s, Error: %s",
            request_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error during get permissions: {str(e)}",
        ) from e

    if not permissions_data:
        logger.warning("No permissions found - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No permissions found",
        )

    # Format permissions data efficiently
    permissions = format_permissions_data(permissions_data)


    # Set audit data for successful retrieval
    request.state.raw_audit_new_data = {
        "organization_id": user_context.organization_id,
        "permissions_count": len(permissions),
        "permission_ids": [str(perm["id"]) for perm in permissions_data],
        "permission_categories": list(
            {perm["category"] for perm in permissions_data if perm["category"]}
        ),
    }


    return PermissionsResponse(
        message=PERMISSIONS_RETRIEVED_SUCCESSFULLY_MESSAGE,
        permissions=permissions,
    )


@handle_api_exceptions("get permission by ID")
@router.get(
    "/{permission_id}",
    response_model=PermissionsResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=["access_control", "soc2_audit", "audit_required"],
#     table_name="permissions",
#     category="PERMISSION",
# )
async def get_permission_by_id(
    request: Request,
    permission_id: str,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Get permission by ID

    Args:
        permission_id (string): The ID of the permission to retrieve

    Returns:
        PermissionResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for permission retrieval
    request.state.audit_requested_id = permission_id
    request.state.audit_table = "permissions"
    request.state.audit_description = f"Retrieved permission by ID: {permission_id}"
    request.state.audit_risk_level = "low"

    # Validate role_id format using utility function
    validate_uuid_format(permission_id, "permission ID")

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user, permission_codes=SETTINGS_ROLES_MANAGE,
        action_description="get permission by ID"
    )

    try:
        permission = await get_permission_details_by_id(
            permission_id, user_context.organization_id
        )
    except DatabaseOperationError as e:
        logger.error(
            "Database error retrieving permission - Request ID: %s, Error: %s",
            request_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error during get permission by ID: {str(e)}",
        ) from e

    if not permission:
        logger.warning("Permission not found - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )

    permissions = format_permissions_data([permission])

    # Set audit data for successful retrieval
    request.state.raw_audit_new_data = {
        "permission_id": permission_id,
        "permission_name": permission["name"],
        "permission_code": permission["code"],
        "permission_category": permission["category"],
        "organization_id": user_context.organization_id,
    }

    return PermissionsResponse(
        message=PERMISSIONS_RETRIEVED_SUCCESSFULLY_MESSAGE,
        permissions=permissions,
    )


@handle_api_exceptions("create permission")
@router.post(
    "", response_model=PermissionsResponse, status_code=status.HTTP_201_CREATED
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
    permission_data: CreatePermissionRequest,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Create a new permission

    Returns:
        PermissionResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for permission creation
    request.state.audit_table = "permissions"
    request.state.audit_description = (
        f"Created new permission: {permission_data.name} (code: {permission_data.code})"
    )
    request.state.audit_risk_level = "medium"

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user, permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="create permission"
    )

    try:
        permission = await create_new_permission(
            permission_data=permission_data,
            organization_id=user_context.organization_id
        )
    except DatabaseOperationError as e:
        logger.error(
            "Database error creating permission - Request ID: %s, Error: %s",
            request_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error during create permission: {str(e)}",
        ) from e


    if not permission:
        logger.warning("Failed to create permission - Request ID: %s, ",request_id)
        logger.warning(
                "Permission Name: %s, Permission Code: %s, ",
                permission_data.name,permission_data.code
            )
        logger.warning("Organization ID: %s, ",user_context.organization_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create permission",
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
        "created_at": (
            permission["created_at"] if permission["created_at"] else None
        ),
    }


    return PermissionsResponse(
        message=PERMISSIONS_RETRIEVED_SUCCESSFULLY_MESSAGE,
        permissions=permissions,
    )


@router.delete(
    "/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
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
    request: Request, permission_id: uuid.UUID,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Delete a permission

    Args:
        permission_id (int): The ID of the permission to delete

    Returns:
        PermissionResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for permission deletion
    request.state.audit_requested_id = str(permission_id)
    request.state.audit_table = "permissions"
    request.state.audit_description = f"Deleted permission with ID: {permission_id}"
    request.state.audit_risk_level = "high"

    # Extract and validate user context from JWT token
    user_context = await check_permissions(
        current_user=current_user, permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="delete permission"
    )

    permission = await get_permission_details_by_id(permission_id, user_context.organization_id)
    if not permission:
        logger.warning("Permission not found - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
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
        logger.warning("Failed to delete permission - Request ID: %s, ",request_id)
        logger.warning("Permission ID: %s, ",permission_id)
        logger.warning("Organization ID: %s, ",user_context.organization_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to delete permission",
        )
