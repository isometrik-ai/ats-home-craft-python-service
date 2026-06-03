"""Organization Management API Module.

This module provides CRUD operations for organization management.
All endpoints include proper authentication, validation, and database operations.
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call

# Logger import
from apps.user_service.app.dependencies.db import db_conn, db_uow

# Schema imports
from apps.user_service.app.schemas.ai_overview_settings import AiOverviewRefetchBody
from apps.user_service.app.schemas.organizations import (
    ApproveRejectDeleteRequestBody,
    CreateOrganizationWithUserResponse,
    DeleteRequestListResponse,
    DeleteRequestStatus,
    NewOrganizationBody,
    OrganizationAdminUpdate,
    OrganizationDetailResponse,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationStatus,
)

# Service import
from apps.user_service.app.services.organization_service import OrganizationService

# Local imports - app dependencies and schemas
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
    require_organization_creator,
    require_permission,
    require_super_admin,
    set_audit_old_data_from_user,
)

# Permission imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    SETTINGS_SYSTEM_MANAGE,
    USERS_MANAGEMENT_DELETE,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for organization endpoints
router = APIRouter(prefix="/organization", tags=["Organization Management"])

# Initialize logger for organization module
logger = get_logger("organization-api")


@handle_api_exceptions("get organizations list")
@router.get(
    "/list",
    response_model=OrganizationListResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get list of all organizations in the system",
    summary="Get list of all organizations in the system",
    responses={
        http_status.HTTP_200_OK: {"description": "Organizations list retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_organizations_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    name: str | None = Query(None, description="The name of the organization"),
    org_status: OrganizationStatus | None = Query(
        None, description="The status of the organization"
    ),
):
    """Get list of all organizations in the system (Requires: organization.appscrip.manage)"""
    user_context = await extract_user_context(current_user, db_connection)
    # Check permissions
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        db_connection=db_connection,
    )

    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organization_service.list_organizations(
        page=page, page_size=page_size, search=name, status=org_status
    )

    if not result.data:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            page=page,
            page_size=page_size,
            status_code=http_status.HTTP_200_OK,
            custom_code=CustomStatusCode.NO_CONTENT,
        )

    return list_response(
        request=request,
        items=result.data,
        total=result.total_count,
        message_key="success.retrieved",
        page=page,
        page_size=page_size,
        status_code=http_status.HTTP_200_OK,
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get delete request list")
@router.get(
    "/delete-request-list",
    response_model=DeleteRequestListResponse,
    description="Get list of organization delete requests (Super Admin only)",
    summary="Get list of organization delete requests",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "Delete requests list retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {
            "description": "Bad request - Invalid organization_id format"
        },
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden - Only super admins can access"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def get_delete_request_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    organization_id: str | None = Query(None, description="Optional organization ID to filter by"),
    status: str | None = Query(
        None,
        description=(
            f"Optional status to filter by ({', '.join([s.value for s in DeleteRequestStatus])})"
        ),
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """Get list of organization delete requests (Super Admin only).

    Only system super admins can view delete requests.
    Can filter by specific organization or status, or view all requests.
    Results are paginated.
    """
    # Validate user is a super admin
    await require_super_admin(current_user)

    # Extract user context (needed for service initialization)
    user_context = await extract_user_context(current_user, db_connection)

    # Create service and delegate to service
    organization_service = OrganizationService(
        user_context=user_context,
        db_connection=db_connection,
    )
    result = await organization_service.list_delete_requests(
        page=page,
        page_size=page_size,
        organization_id=organization_id,
        status=status,
    )

    if not result["data"]:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            page=page,
            page_size=page_size,
            status_code=http_status.HTTP_200_OK,
            custom_code=CustomStatusCode.NO_CONTENT,
        )

    return list_response(
        request=request,
        items=result["data"],
        total=result["total_count"],
        message_key="success.retrieved",
        page=page,
        page_size=page_size,
        status_code=http_status.HTTP_200_OK,
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get organization AI overview settings")
@router.get(
    "/ai-overview-settings",
    response_model=None,
    description="Get AI overview prompts and business background for the current organization",
    summary="Get organization AI overview settings",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "AI overview settings retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_organization_ai_overview_settings(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get stored AI overview prompts merged with platform defaults for the session org.

    Returns ``business_overview`` (company facts for AI background) and
    ``overview_prompts`` for lead, contact, and company record types.
    Requires: settings_management.edit
    """
    user_context = await extract_user_context(current_user, db_connection)
    if user_context.organization_id is None:
        raise ValidationException(
            message_key="organizations.errors.user_not_a_member_of_any_organization",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=user_context.organization_id,
    )
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    settings = await organization_service.get_ai_overview_settings(user_context.organization_id)
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=settings.model_dump(exclude_none=False),
    )


@handle_api_exceptions("refetch organization AI overview settings")
@router.post(
    "/ai-overview-settings/refetch",
    response_model=None,
    description=(
        "Refetch business overview and/or individual overview prompts "
        "(lead, contact, company) for the current org"
    ),
    summary="Refetch organization AI overview fields",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "AI overview fields refetched successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("10/minute")
async def refetch_organization_ai_overview_settings(
    request: Request,
    body: AiOverviewRefetchBody = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Refetch selected AI overview fields using the org enrichment agents.

    Each field is refreshed independently — no chaining between overview fields.
    Response includes only the field(s) that were refetched (not the full settings snapshot).
    - ``business_overview`` — re-fetch from website (discovers website only if unset)
    - ``lead`` / ``contact`` / ``company`` — regenerate that prompt only (requires
      stored business_overview)

    Use GET ``/ai-overview-settings`` for the full current settings.

    Requires: settings_management.edit
    """
    user_context = await extract_user_context(current_user, db_connection)
    if user_context.organization_id is None:
        raise ValidationException(
            message_key="organizations.errors.user_not_a_member_of_any_organization",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=user_context.organization_id,
    )

    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organization_service.refetch_ai_overview_settings(list(body.fields))
    return success_response(
        request=request,
        message_key="organizations.success.organization_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("get organization by ID")
@router.get(
    "/{organization_id}",
    response_model=OrganizationDetailResponse,
    description="Get organization by ID",
    summary="Get organization by ID",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganizationDetailResponse,
            "description": "Organization retrieved successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_organization_by_id(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: UUID = Path(..., description="The UUID of the organization to get"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get organization by ID with complete details (Requires: settings_management.edit)"""

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organization_id),
    )
    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    data = await organization_service.get_organization_detail(str(organization_id))
    # Serialize with exclude_none=False to include null fields in response
    data_dict = data.model_dump(exclude_none=False)
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data_dict,
    )


@handle_api_exceptions("create organization")
@router.post(
    "/",
    response_model=CreateOrganizationWithUserResponse,
    description="Create a new organization",
    summary="Create a new organization",
    status_code=http_status.HTTP_201_CREATED,
    responses={
        http_status.HTTP_201_CREATED: {
            "model": CreateOrganizationWithUserResponse,
            "description": "Organization created successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Creating organization involves personal information
        "pii",  # Organization creation contains personally identifiable information
        "soc2_audit",  # Organization management is critical for SOC2 compliance
        "audit_required",  # Organization creation requires audit trail
    ],
    table_name="organizations",
    category="ORGANIZATION",
)
async def create_organization(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: NewOrganizationBody = Body(...),
):
    """Create a new organization with initial Super Admin user.

    Requires: settings_management.edit
    """
    # Set audit context for organization creation
    request.state.audit_table = "organizations"
    request.state.audit_description = f"Created new organization: {body.company_data.company_name}"
    request.state.audit_risk_level = "high"

    # Extract user context from JWT token
    user_context = await extract_user_context(current_user, db_connection)

    # Validate session_id is present
    session_id = current_user.get("session_id")

    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organization_service.create_organization(
        body=body,
        slug=None,
        session_id=session_id,
    )
    request.state.audit_requested_id = str(result.get("organization_id", "")) if result else ""

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": result["organization_id"],
    }

    return success_response(
        request=request,
        message_key="organizations.success.organization_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=result,
    )


@handle_api_exceptions("update organization")
@router.put(
    "/{organization_id}",
    response_model=OrganizationResponse,
    description="Update an existing organization",
    summary="Update an existing organization",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganizationResponse,
            "description": "Organization updated successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Updating organization involves personal information
        "pii",  # Organization updates contain personally identifiable information
        "soc2_audit",  # Organization management is critical for SOC2 compliance
        "audit_required",  # Organization updates require audit trail
    ],
    table_name="organizations",
    category="ORGANIZATION",
)
async def update_organization(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    organization_id: UUID = Path(..., description="The UUID of the organization to update"),
    current_user: dict = Depends(get_user_from_auth),
    body: OrganizationAdminUpdate = Body(...),
):
    """Update an existing organization (Requires: settings_management.edit)"""
    # Set audit context for organization update
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organization_id
    request.state.audit_description = f"Updated organization: {organization_id}"
    request.state.audit_risk_level = "medium"

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organization_id),
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or str(organization_id),
    }

    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organization_service.update_organization(
        organization_id=str(organization_id), update_data=body
    )

    # Set audit data from service result
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = body.model_dump(exclude_unset=True, exclude_none=True)

    # Remove old_data from response (only needed for audit logging)
    response_data = {
        "organization_id": result["organization_id"],
        "organization_name": result["organization_name"],
        "slug": result["slug"],
    }
    if "organization_memory" in result:
        response_data["organization_memory"] = result["organization_memory"]
    if "ai_overview_settings" in result:
        response_data["ai_overview_settings"] = result["ai_overview_settings"]

    return success_response(
        request=request,
        message_key="organizations.success.organization_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
        data=response_data,
    )


@handle_api_exceptions("delete organization")
@router.delete(
    "/{organization_id}",
    response_model=OrganizationResponse,
    description="Delete an existing organization",
    summary="Delete an existing organization",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganizationResponse,
            "description": "Organization deleted successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Deleting organization involves personal information
        "pii",  # Organization deletion contains personally identifiable information
        "soc2_audit",  # Organization management is critical for SOC2 compliance
        "audit_required",  # Organization deletion requires audit trail
    ],
    table_name="organizations",
    category="ORGANIZATION",
)
async def delete_organization_by_id(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    organization_id: UUID = Path(..., description="The UUID of the organization to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an existing organization (Requires: settings_management.edit)"""
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organization_id
    request.state.audit_description = f"Deleted organization: {organization_id}"
    request.state.audit_risk_level = "high"

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organization_id),
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or str(organization_id),
    }

    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    await organization_service.delete_organization(str(organization_id))

    return success_response(
        request=request,
        message_key="organizations.success.organization_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("request organization deletion")
@router.post(
    "/request-to-delete/{organization_id}",
    description="Request to delete an organization",
    summary="Request to delete an organization (Organization Creator only)",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses={
        http_status.HTTP_201_CREATED: {"description": "Delete request created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": "Forbidden - Only creator can request deletion"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Organization not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="organization_delete_requests",
    category="ORGANIZATION",
)
async def request_organization_deletion(
    request: Request,
    organization_id: str = Path(..., description="The UUID of the organization"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Request to delete an organization.

    Only the organization creator can create a delete request.
    Cannot create duplicate pending request.
    """
    # Set audit context
    request.state.audit_table = "organization_delete_requests"
    request.state.audit_description = f"Created delete request for organization: {organization_id}"
    request.state.audit_risk_level = "high"

    # Extract user context
    user_context = await extract_user_context(current_user, db_connection)

    # Verify user is the organization creator
    await require_organization_creator(
        user_context=user_context,
        organization_id=organization_id,
        db_connection=db_connection,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": organization_id,
    }

    # Create service and delegate to service
    organization_service = OrganizationService(
        user_context=user_context,
        db_connection=db_connection,
    )
    result = await organization_service.create_delete_request(organization_id=organization_id)
    request.state.audit_requested_id = str(result.get("id", "")) if result else ""

    return success_response(
        request=request,
        message_key="organizations.success.delete_request_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data={
            "request_id": str(result["id"]),
            "organization_id": str(result["organization_id"]),
            "status": result["status"],
            "requested_at": (
                result["requested_at"].isoformat()
                if hasattr(result["requested_at"], "isoformat")
                else str(result["requested_at"])
            ),
        },
    )


@handle_api_exceptions("process delete request")
@router.patch(
    "/delete-request/{request_id}",
    description="Approve or reject an organization delete request (Super Admin only)",
    summary="Process (approve/reject) an organization delete request",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses={
        http_status.HTTP_200_OK: {"description": "Delete request processed successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request - Invalid format"},
        http_status.HTTP_403_FORBIDDEN: {
            "description": (
                "Forbidden - Only super admins can process requests or request already processed"
            )
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Organization or delete request not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="organization_delete_requests",
    category="ORGANIZATION",
)
async def process_delete_request(
    request: Request,
    request_id: str = Path(..., description="The UUID of the delete request"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ApproveRejectDeleteRequestBody = Body(...),
):
    """Process (approve/reject) an organization delete request.

    Only system super admins can approve or reject delete requests.
    Request must be in pending status (DeleteRequestStatus.PENDING).

    If approved:
    - Organization and all related data are permanently deleted
    - All organization members receive deletion notification emails

    If rejected:
    - Organization remains active
    - Requester receives rejection notification email with reason
    """
    # Set audit context
    request.state.audit_table = "organization_delete_requests"
    request.state.audit_description = (
        f"{'Approved' if body.is_accepted else 'Rejected'} delete request: {request_id}"
    )
    request.state.audit_risk_level = "high"

    # Validate user is a super admin
    await require_super_admin(current_user)

    # Extract user context
    user_context = await extract_user_context(current_user, db_connection)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service and delegate to service
    organization_service = OrganizationService(
        user_context=user_context,
        db_connection=db_connection,
    )
    result = await organization_service.process_delete_request(
        request_id=request_id,
        is_accepted=body.is_accepted,
        reason=body.reason,
    )

    return success_response(
        request=request,
        message_key=(
            "organizations.success.delete_request_approved"
            if body.is_accepted
            else "organizations.success.delete_request_rejected"
        ),
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("delete organization member")
@router.delete(
    "/member/{member_user_id}",
    description="Delete an organization member",
    summary="Delete an organization member",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses={
        http_status.HTTP_200_OK: {"description": "Member deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {
            "description": "Bad request - Organization owner cannot be deleted"
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Member not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden - Insufficient permissions"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="organization_members",
    category="ORG_MEMBER_REMOVE",
)
async def delete_organization_member(
    request: Request,
    member_user_id: UUID = Path(..., description="The UUID of the organization member to delete"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an organization member (Requires: users_management.delete)

    Cannot delete organization owner.
    Soft deletes organization member and hard deletes from all teams.
    """
    # Extract user context (needed for service initialization)
    user_context = await extract_user_context(current_user, db_connection)

    # Validate user has organization context
    if user_context.organization_id is None:
        raise ValidationException(
            message_key="organizations.errors.user_not_a_member_of_any_organization",
            custom_code=CustomStatusCode.INVALID_DATA,
        )

    # Bypass permission check if self delete, otherwise check permissions
    if current_user["sub"] != str(member_user_id):
        # Check permissions for deleting other users
        await require_permission(
            permission_code=USERS_MANAGEMENT_DELETE,
            user_context=user_context,
            db_connection=db_connection,
            organization_id=user_context.organization_id,
        )

    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = str(member_user_id)
    request.state.audit_description = f"Removed organization member: {member_user_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service with user context and delegate to service
    organization_service = OrganizationService(
        user_context=user_context, db_connection=db_connection
    )
    delete_audit = await organization_service.delete_organization_member(str(member_user_id))
    set_audit_old_data_from_user(request, delete_audit["current_user_data"])
    request.state.raw_audit_new_data = delete_audit["audit_new"]

    return success_response(
        request=request,
        message_key="organizations.success.member_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
