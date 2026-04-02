"""Audit Logs API Module

This module provides CRUD operations for audit logs management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.audit_logs import AuditLogFilter
from apps.user_service.app.services.audit_log_service import AuditLogService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import check_user_access_async, get_user_from_auth
from libs.shared_utils.common_query import (
    AUDIT_LOGS_MANAGEMENT_VIEW_SYSTEM,
    SETTINGS_SYSTEM_MANAGE,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for audit logs endpoints
router = APIRouter(prefix="/audit-logs", tags=["Audit Logs Management"])


@handle_api_exceptions("get audit logs")
@router.get(
    "",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get all audit logs for the current organization",
    summary="Get all audit logs for the current organization",
    responses={
        http_status.HTTP_200_OK: {"description": "Audit logs retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_audit_logs(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    search: str | None = Query(
        None,
        description="Search term to filter audit logs by description",
    ),
    user_id: str | None = Query(
        None,
        description="Filter audit logs by user ID",
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """Get all audit logs for the current organization"""
    # Extract and validate user context from JWT token.
    user_context = await extract_user_context(current_user, db_connection)

    can_view_system_audit_logs = await check_user_access_async(
        permission_code=[AUDIT_LOGS_MANAGEMENT_VIEW_SYSTEM],
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
        db_connection=db_connection,
    )

    # If role does not have system-level visibility, force personal scope and ignore query param.
    effective_user_id = user_id if can_view_system_audit_logs else user_context.user_id

    # Create service and delegate to service
    audit_log_service = AuditLogService(user_context=user_context, db_connection=db_connection)

    filters = AuditLogFilter(
        search=search,
        user_id=effective_user_id,
        limit=page_size,
        offset=(page - 1) * page_size,
        organization_id=user_context.organization_id,
    )

    result = await audit_log_service.get_audit_logs(filter_params=filters)

    audit_logs = result["audit_logs"]
    total_count = result["total_count"]

    if not audit_logs:
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

    return list_response(
        request=request,
        items=audit_logs,
        total=total_count,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        page=page,
        page_size=page_size,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get audit log by ID")
@router.get(
    "/{audit_log_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get audit log by ID",
    summary="Get audit log by ID",
    responses={
        http_status.HTTP_200_OK: {"description": "Audit log retrieved successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Audit log not found"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_audit_log_from_id(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    audit_log_id: str = Path(..., description="The UUID of the audit log to get"),
):
    """Get audit log by ID"""
    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    # Create service and delegate to service
    audit_log_service = AuditLogService(user_context=user_context, db_connection=db_connection)
    audit_log_detail = await audit_log_service.get_audit_log_by_id(audit_log_id)

    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=audit_log_detail,
    )


@handle_api_exceptions("delete all audit logs")
@router.delete(
    "",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Delete all audit logs from the system",
    summary="Delete all audit logs from the system",
    responses={
        http_status.HTTP_200_OK: {"description": "Audit logs deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("10/minute")
async def delete_all_audit_logs_data(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
):
    """Delete all audit logs from the system"""
    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )

    # Create service and delegate to service
    audit_log_service = AuditLogService(user_context=user_context, db_connection=db_connection)
    await audit_log_service.delete_all_audit_logs()

    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
