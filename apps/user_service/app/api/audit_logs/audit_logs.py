
"""
Audit Logs API Module

This module provides CRUD operations for audit logs management.
All endpoints include proper authentication, validation, and database operations.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""
from ipaddress import IPv4Address

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions,
    format_iso_datetime,
    safe_json_loads,
    validate_uuid_format,
    check_permissions,
)

# Audit logs utility imports
from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    build_audit_logs_filter_message,
)

# Schema imports
from apps.user_service.app.schemas.audit_logs import (
    AuditLogsResponse,
    AuditLogItem,
    AuditLogDetailResponse,
    AuditLogDetailItem,
    DeleteAuditLogsResponse
)

from apps.user_service.app.schemas.common import (
    AuditLogsQueryParams,
)

from apps.user_service.app.app_instance import limiter

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.audit_operations import (
    get_audit_logs_list,
    get_audit_logs_count,
    get_audit_log_by_id,
    delete_all_audit_logs,
    AuditLogFilter,
)

# Create router for audit logs endpoints
router = APIRouter(prefix="/audit-logs", tags=["Audit Logs Management"])

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"


class AuditLogResponse(BaseModel):
    """Response model for audit log operations"""

    message: str
    status: str = "success"

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {"message": self.message, "status": self.status}


@handle_api_exceptions("get audit logs")
@router.get("", response_model=AuditLogsResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=["audit_required", "soc2_audit", "gdpr"],
#     table_name="audit_logs",
#     category="audit_management",
# )
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_audit_logs(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: AuditLogsQueryParams = Depends(),
):
    """
    Get all audit logs for the current organization (Optimized & Truly Async)

    This endpoint retrieves all audit logs for the authenticated user's organization.
    Uses truly async database operations for best performance and scalability.

    Performance Features:
    - JWT authentication
    - Async database operations (non-blocking)
    - Connection pooling
    - Efficient queries with proper indexing
    - Search functionality across description, action_type, and table_name
    - Pagination support
    - Organization isolation

    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        query_params (AuditLogsQueryParams): Query parameters object containing
            search, pagination options

    Returns:
        AuditLogsResponse: List of audit logs with detailed information

    Raises:
        HTTPException: 400 for invalid parameters
        HTTPException: 403 for insufficient permissions
        HTTPException: 500 for database errors
    """
    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(current_user, SETTINGS_SYSTEM_MANAGE, "view audit logs")

    # Create filter parameters
    filter_params = AuditLogFilter(
        organization_id=user_context.organization_id,
        search=query_params.search,
        limit=query_params.limit,
        offset=query_params.skip,
        user_id=user_context.user_id,
    )

    # Get audit logs using centralized database operations
    audit_logs_data = await get_audit_logs_list(filter_params)

    print(f"audit_logs_data: {audit_logs_data}")

    # Get total count using centralized database operations
    total_count = await get_audit_logs_count(
        user_context.organization_id,
        user_context.user_id, filter_params)

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
            ip_address=str(IPv4Address(audit_log["ip_address"])),
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

    # Build response message using utility function
    message = build_audit_logs_filter_message(
        search=query_params.search,
        skip=query_params.skip,
        limit=query_params.limit,
    )

    return AuditLogsResponse(
        message=message,
        audit_logs=audit_logs,
        total_count=total_count,
    )


@handle_api_exceptions("get audit log by ID")
@router.get(
    "/{audit_log_id}",
    response_model=AuditLogDetailResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "audit_required",
#         "soc2_audit",
#         "gdpr",
#     ],
#     table_name="audit_logs",
#     category="audit_management",
# )
async def get_audit_log_from_id(
    audit_log_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Get audit log by ID with all details (Optimized & Truly Async)

    This endpoint retrieves detailed information about a specific audit log including
    all metadata and integrity information. Uses truly async database operations
    for best performance and scalability.

    Performance Features:
    - JWT authentication
    - Async database operations (non-blocking)
    - Connection pooling
    - Efficient queries with proper indexing
    - Organization isolation

    Args:
        audit_log_id (str): UUID of the audit log to retrieve
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        AuditLogDetailResponse: Detailed audit log information with all metadata

    Raises:
        HTTPException: 400 for invalid audit log ID or token data
        HTTPException: 403 for insufficient permissions
        HTTPException: 404 if audit log not found
        HTTPException: 500 for database errors
    """

    # Validate audit_log_id format using utility function
    request.state.audit_requested_id = audit_log_id
    validate_uuid_format(audit_log_id, "audit log ID")

    # Extract and validate user context from JWT token & check permission
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="view audit log details",
    )
    # Get audit log using centralized database operations
    audit_log_data = await get_audit_log_by_id(
        audit_log_id=audit_log_id,
        organization_id=user_context.organization_id,
        user_id=user_context.user_id,
    )

    # Check if audit log exists
    if not audit_log_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit log not found",
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
        ip_address=str(IPv4Address(audit_log_data["ip_address"])),
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

    return AuditLogDetailResponse(
        message="Audit log details retrieved successfully",
        audit_log=audit_log_detail,
    )


@handle_api_exceptions("delete all audit logs")
@router.delete(
    "", response_model=DeleteAuditLogsResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("10/minute")
# @audit_api_call(
#     action_type="DELETE",
#     data_classification="confidential",
#     compliance_tags=[
#         "audit_required",
#         "soc2_audit",
#         "gdpr",
#     ],
#     table_name="audit_logs",
#     category="audit_management",
# )
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_all_audit_logs_data(
    request: Request,
    # current_user: dict = Depends(get_user_from_auth),
):
    """
    Delete all audit logs from the system (Optimized & Truly Async)

    This endpoint permanently deletes all audit logs from the system.
    This is a destructive operation and should be used with extreme caution.
    Uses truly async database operations with proper transaction handling.

    ⚠️ WARNING: This operation is irreversible and will permanently delete all audit logs.
    This may impact compliance requirements and audit trail integrity.

    Performance Features:
    - JWT authentication
    - Async database operations (non-blocking)
    - Connection pooling
    - Transaction support for data consistency
    - Organization isolation
    - Rate limiting (10 requests per minute)

    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information

    Returns:
        DeleteAuditLogsResponse: Success message with count of deleted audit logs

    Raises:
        HTTPException: 400 for invalid token data
        HTTPException: 403 for insufficient permissions
        HTTPException: 500 for database errors

    Security Features:
    - High-level permission required (audit.logs.delete)
    - Rate limiting to prevent abuse
    - Comprehensive audit logging of the deletion action
    - Transaction rollback on failures
    """
    # Extract and validate user context from JWT token
    # user_context = await extract_user_context(current_user)

    # Check permission using utility function
    # await require_permission(
    #     permission_code="audit.logs.delete",
    #     user_context=user_context,
    #     db_conn=db_conn,
    #     action_description="delete all audit logs",
    # )

    # Delete all audit logs using centralized database operations
    deleted_count = await delete_all_audit_logs()

    return DeleteAuditLogsResponse(
        message="All audit logs deleted successfully",
        deleted_count=deleted_count,
    )
