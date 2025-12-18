"""Audit Logs API Module

This module provides CRUD operations for audit logs management.
All endpoints include proper authentication, validation, and database operations.
"""

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.schemas.audit_logs import (
    AuditLogDetailItem,
    AuditLogItem,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    format_iso_datetime,
    handle_api_exceptions,
    safe_json_loads,
)
from libs.shared_db.postgres_db.user_service_operations.audit_operations import (
    AuditLogFilter,
    delete_all_audit_logs,
    get_audit_log_by_id,
    get_audit_logs_count,
    get_audit_logs_list,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE
from libs.shared_utils.http_exceptions import NotFoundException
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
    search: str | None = Query(
        None,
        description="Search term to filter audit logs by description",
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """Get all audit logs for the current organization"""
    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(current_user, SETTINGS_SYSTEM_MANAGE)

    # Create filter parameters
    filter_params = AuditLogFilter(
        organization_id=user_context.organization_id,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
        user_id=user_context.user_id,
    )

    # Get audit logs using centralized database operations
    audit_logs_data = await get_audit_logs_list(filter_params)

    if not audit_logs_data:
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

    # Get total count using centralized database operations
    total_count = await get_audit_logs_count(
        user_context.organization_id, user_context.user_id, filter_params
    )

    # Format audit logs data using utility functions
    audit_logs = [
        AuditLogItem(
            id=str(audit_log["id"]),
            organization_id=str(audit_log["organization_id"]),
            user_id=str(audit_log["user_id"]),
            user_email=audit_log["user_email"],
            user_role=audit_log["user_role"],
            action_type=audit_log["action_type"],
            data_classification=audit_log["data_classification"],
            table_name=audit_log["table_name"],
            record_id=audit_log["record_id"],
            old_values=safe_json_loads(audit_log["old_values"], None),
            new_values=safe_json_loads(audit_log["new_values"], None),
            changed_fields=safe_json_loads(audit_log["changed_fields"], None),
            compliance_tags=audit_log["compliance_tags"],
            risk_level=audit_log["risk_level"],
            ip_address=audit_log["ip_address"],
            description=audit_log["description"],
            timestamp=(
                audit_log["timestamp"]
                if isinstance(audit_log["timestamp"], str)
                else format_iso_datetime(audit_log["timestamp"]) or ""
            ),
            status_code=audit_log.get("status_code"),
            category=audit_log.get("category"),
        )
        for audit_log in audit_logs_data
    ]

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
    audit_log_id: str = Path(..., description="The UUID of the audit log to get"),
):
    """Get audit log by ID"""
    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )
    # Get audit log using centralized database operations
    audit_log_data = await get_audit_log_by_id(
        audit_log_id=audit_log_id,
        organization_id=user_context.organization_id,
        user_id=user_context.user_id,
    )

    # Check if audit log exists
    if not audit_log_data:
        raise NotFoundException(
            message_key="audit_logs.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Format audit log data using utility functions
    audit_log_detail = AuditLogDetailItem(
        id=str(audit_log_data["id"]),
        organization_id=str(audit_log_data["organization_id"]),
        user_id=str(audit_log_data["user_id"]),
        user_email=audit_log_data["user_email"],
        user_role=audit_log_data["user_role"],
        action_type=audit_log_data["action_type"],
        data_classification=audit_log_data["data_classification"],
        table_name=audit_log_data["table_name"],
        record_id=audit_log_data["record_id"],
        old_values=safe_json_loads(audit_log_data["old_values"], None),
        new_values=safe_json_loads(audit_log_data["new_values"], None),
        changed_fields=safe_json_loads(audit_log_data["changed_fields"], None),
        compliance_tags=audit_log_data["compliance_tags"],
        risk_level=audit_log_data["risk_level"],
        ip_address=audit_log_data["ip_address"],
        description=audit_log_data["description"],
        timestamp=(
            audit_log_data["timestamp"]
            if isinstance(audit_log_data["timestamp"], str)
            else format_iso_datetime(audit_log_data["timestamp"]) or ""
        ),
        hash_signature=audit_log_data["hash_signature"],
        previous_hash=audit_log_data["previous_hash"],
        retention_date=(
            audit_log_data["retention_date"]
            if isinstance(audit_log_data["retention_date"], str)
            else format_iso_datetime(audit_log_data["retention_date"]) or None
        ),
        status_code=audit_log_data.get("status_code"),
        category=audit_log_data.get("category"),
    )

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
):
    """Delete all audit logs from the system"""
    # Delete all audit logs using centralized database operations
    await delete_all_audit_logs()

    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
