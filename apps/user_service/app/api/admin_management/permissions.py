# pylint: disable=import-error,no-name-in-module
# pylint: disable=logging-fstring-interpolation
"""
Permissions Management API Module

This module provides CRUD operations for permission management.
All endpoints include proper authentication, validation, and database operations.

"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    validate_uuid_format,
    handle_api_exceptions,
)
from apps.user_service.app.dependencies.permissions_utils import (
    get_permission_by_id_from_db,
    get_all_permission_from_db,
    create_permission_in_db,
)

from apps.user_service.app.dependencies.roles_utils import check_roles_manage_permission

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    PermissionsResponse,
    PermissionItem,
    CreatePermissionRequest,
)

from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import (
    get_user_from_auth,
)


# Create router for permissions endpoints
router = APIRouter(prefix="/permissions", tags=["Permissions Management"])

# Initialize logger for permissions module
logger = get_logger("permissions-api")
logger.info("Permissions API module loaded")

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"


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
    request: Request,  # pylint: disable=unused-argument
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
        db_conn: AsyncPG database connection (truly async)

    Returns:
        PermissionsResponse: List of permissions with id, name,
          code, category, description, and created_at
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        f"GET /permissions request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}"
    )

    # Set audit context for permissions listing
    request.state.audit_table = "permissions"
    request.state.audit_description = "Retrieved all permissions for organization"
    request.state.audit_risk_level = "low"

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        f"User context extracted and permissions validated - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    permissions_data = await get_all_permission_from_db(
        user_context.organization_id, db_conn
    )
    logger.debug(
        f"Permissions retrieved from database - Request ID: {request_id}, "
        f"Organization ID: {user_context.organization_id}, "
        f"Permissions count: {len(permissions_data) if permissions_data else 0}"
    )

    if not permissions_data:
        logger.warning(
            f"No permissions found - Request ID: {request_id}, "
            f"Organization ID: {user_context.organization_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No permissions found",
        )

    # Format permissions data efficiently
    permissions = [
        PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=(
                permission["created_at"].isoformat() if permission["created_at"] else ""
            ),
        )
        for permission in permissions_data
    ]
    logger.debug(
        f"Permissions data formatted - Request ID: {request_id}, "
        f"Formatted permissions count: {len(permissions)}"
    )

    # Set audit data for successful retrieval
    request.state.raw_audit_new_data = {
        "organization_id": user_context.organization_id,
        "permissions_count": len(permissions),
        "permission_ids": [str(perm["id"]) for perm in permissions_data],
        "permission_categories": list(
            set(perm["category"] for perm in permissions_data if perm["category"])
        ),
    }

    logger.info(
        f"GET /permissions request completed successfully - Request ID: {request_id}, "
        f"Organization ID: {user_context.organization_id}, "
        f"Permissions Count: {len(permissions)}, Status Code: 200"
    )

    return PermissionsResponse(
        status_code=status.HTTP_200_OK,
        message="Permissions retrieved successfully",
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
    request: Request,  # pylint: disable=unused-argument
    permission_id: str,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
    logger.info(
        f"GET /permissions/{permission_id} request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Permission ID: {permission_id}"
    )

    # Set audit context for permission retrieval
    request.state.audit_requested_id = permission_id
    request.state.audit_table = "permissions"
    request.state.audit_description = f"Retrieved permission by ID: {permission_id}"
    request.state.audit_risk_level = "low"

    # Validate role_id format using utility function
    validate_uuid_format(permission_id, "permission ID")
    logger.debug(
        f"Permission ID format validated - Request ID: {request_id}, "
        f"Permission ID: {permission_id}"
    )

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        f"User context extracted and permissions validated - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    permission = await get_permission_by_id_from_db(
        permission_id, user_context.organization_id, db_conn
    )
    logger.debug(
        f"Permission retrieved from database - Request ID: {request_id}, "
        f"Permission ID: {permission_id}, Permission found: {permission is not None}"
    )

    if not permission:
        logger.warning(
            f"Permission not found - Request ID: {request_id}, "
            f"Permission ID: {permission_id}, Organization ID: {user_context.organization_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )

    permissions = [
        PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=(
                permission["created_at"].isoformat() if permission["created_at"] else ""
            ),
        )
    ]
    logger.debug(
        f"Permission data formatted - Request ID: {request_id}, "
        f"Permission ID: {permission_id}, Permission Name: {permission['name']}, "
        f"Permission Code: {permission['code']}"
    )

    # Set audit data for successful retrieval
    request.state.raw_audit_new_data = {
        "permission_id": permission_id,
        "permission_name": permission["name"],
        "permission_code": permission["code"],
        "permission_category": permission["category"],
        "organization_id": user_context.organization_id,
    }

    logger.info(
        f"GET /permissions/{permission_id} request completed successfully - "
        f"Request ID: {request_id}, "
        f"Permission ID: {permission_id}, Permission Name: {permission['name']}, "
        f"Permission Code: {permission['code']}, Status Code: 200"
    )

    return PermissionsResponse(
        status_code=status.HTTP_200_OK,
        message="Permissions retrieved successfully",
        permissions=permissions,
    )


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
    request: Request,  # pylint: disable=unused-argument
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Create a new permission

    Returns:
        PermissionResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        f"POST /permissions request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Permission Name: {permission_data.name}, "
        f"Permission Code: {permission_data.code}"
    )

    # Set audit context for permission creation
    request.state.audit_table = "permissions"
    request.state.audit_description = (
        f"Created new permission: {permission_data.name} (code: {permission_data.code})"
    )
    request.state.audit_risk_level = "medium"

    # Extract and validate user context from JWT token
    user_context = await check_roles_manage_permission(current_user, db_conn)
    logger.debug(
        f"User context extracted and permissions validated - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}"
    )

    permission = await create_permission_in_db(
        permission_data, user_context.organization_id, db_conn
    )
    logger.debug(
        f"Permission created in database - Request ID: {request_id}, "
        f"Permission Name: {permission_data.name}, Permission Code: {permission_data.code}, "
        f"Permission created: {permission is not None}"
    )

    if not permission:
        logger.warning(
            f"Failed to create permission - Request ID: {request_id}, "
            f"Permission Name: {permission_data.name}, Permission Code: {permission_data.code}, "
            f"Organization ID: {user_context.organization_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create permission",
        )

    permissions = [
        PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=(
                permission["created_at"].isoformat() if permission["created_at"] else ""
            ),
        )
    ]
    logger.debug(
        f"Created permission data formatted - Request ID: {request_id}, "
        f"Permission ID: {permission['id']}, Permission Name: {permission['name']}, "
        f"Permission Code: {permission['code']}"
    )

    # Set audit data for successful creation
    request.state.raw_audit_new_data = {
        "permission_id": str(permission["id"]),
        "permission_name": permission["name"],
        "permission_code": permission["code"],
        "permission_category": permission["category"],
        "permission_description": permission["description"],
        "organization_id": user_context.organization_id,
        "created_at": (
            permission["created_at"].isoformat() if permission["created_at"] else None
        ),
    }

    logger.info(
        f"POST /permissions request completed successfully - Request ID: {request_id}, "
        f"Permission ID: {permission['id']}, Permission Name: {permission['name']}, "
        f"Permission Code: {permission['code']}, Status Code: 201"
    )

    return PermissionsResponse(
        status_code=status.HTTP_200_OK,
        message="Permissions retrieved successfully",
        permissions=permissions,
    )


@router.delete(
    "/{permission_id}",
    response_model=PermissionResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=["access_control", "soc2_audit", "audit_required"],
    table_name="permissions",
    category="PERMISSION",
)
async def delete_permission(
    request: Request, permission_id: int  # pylint: disable=unused-argument
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
    logger.info(
        f"DELETE /permissions/{permission_id} request started - Request ID: {request_id}, "
        f"Permission ID: {permission_id}"
    )

    # Set audit context for permission deletion
    request.state.audit_requested_id = str(permission_id)
    request.state.audit_table = "permissions"
    request.state.audit_description = f"Deleted permission with ID: {permission_id}"
    request.state.audit_risk_level = "high"

    # Set audit data for deletion (placeholder since this endpoint is not fully implemented)
    current_timestamp = datetime.utcnow().isoformat()
    request.state.raw_audit_old_data = {
        "permission_id": str(permission_id),
        "deletion_timestamp": current_timestamp,
    }

    request.state.raw_audit_new_data = {
        "permission_id": str(permission_id),
        "status": "DELETED",
        "deletion_timestamp": current_timestamp,
    }

    logger.debug(
        f"Delete permission request processed - Request ID: {request_id}, "
        f"Permission ID: {permission_id}"
    )

    logger.info(
        f"DELETE /permissions/{permission_id} request completed successfully - "
        f"Request ID: {request_id}, "
        f"Permission ID: {permission_id}, Status Code: 200"
    )

    return PermissionResponse(
        message=f"Delete permission {permission_id} API is working",
        status="success",
    )
