"""Organisation Management API Module.

This module provides CRUD operations for organisation management.
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
from apps.user_service.app.schemas.organisations import (
    CreateOrganisationWithUserResponse,
    NewOrganisationBody,
    OrganisationDetailResponse,
    OrganisationListResponse,
    OrganisationResponse,
    OrganizationAdminUpdate,
)

# Service import
from apps.user_service.app.services.organisation_service import OrganisationService

# Local imports - app dependencies and schemas
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
    require_permission,
)

# Permission imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for organisation endpoints
router = APIRouter(prefix="/organisation", tags=["Organisation Management"])

# Initialize logger for organisation module
logger = get_logger("organisation-api")


@handle_api_exceptions("get organisations list")
@router.get(
    "/list",
    response_model=OrganisationListResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get list of all organisations in the system",
    summary="Get list of all organisations in the system",
    responses={
        http_status.HTTP_200_OK: {"description": "Organisations list retrieved successfully"},
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
async def get_organisations_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    name: str | None = Query(None, description="The name of the organisation"),
    org_status: str | None = Query(None, description="The status of the organisation"),
):
    """Get list of all organisations in the system (Requires: organization.appscrip.manage)"""
    user_context = await extract_user_context(current_user, db_connection)
    # Check permissions
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
    )

    # Create service with user context and delegate to service
    organisation_service = OrganisationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organisation_service.list_organisations(
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


@handle_api_exceptions("get organisation by ID")
@router.get(
    "/{organisation_id}",
    response_model=OrganisationDetailResponse,
    description="Get organisation by ID",
    summary="Get organisation by ID",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganisationDetailResponse,
            "description": "Organisation retrieved successfully",
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
async def get_organisation_by_id(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organisation_id: UUID = Path(..., description="The UUID of the organisation to get"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get organisation by ID with complete details (Requires: settings_management.edit)"""

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organisation_id),
    )
    # Create service with user context and delegate to service
    organisation_service = OrganisationService(
        user_context=user_context, db_connection=db_connection
    )
    data = await organisation_service.get_organisation_detail(str(organisation_id))
    # Serialize with exclude_none=False to include null fields in response
    data_dict = data.model_dump(exclude_none=False)
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data_dict,
    )


@handle_api_exceptions("create organisation")
@router.post(
    "/",
    response_model=CreateOrganisationWithUserResponse,
    description="Create a new organisation",
    summary="Create a new organisation",
    status_code=http_status.HTTP_201_CREATED,
    responses={
        http_status.HTTP_201_CREATED: {
            "model": CreateOrganisationWithUserResponse,
            "description": "Organisation created successfully",
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
async def create_organisation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: NewOrganisationBody = Body(...),
):
    """Create a new organisation with initial Super Admin user.

    Requires: settings_management.edit
    """
    # Set audit context for organization creation
    request.state.audit_table = "organizations"
    request.state.audit_description = f"Created new organization: {body.company_data.company_name}"
    request.state.audit_risk_level = "high"

    # Extract user context from JWT token
    user_context = await extract_user_context(current_user, db_connection)

    # Create service with user context and delegate to service
    organisation_service = OrganisationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organisation_service.create_organisation(body=body, slug=None)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": result["organization_id"],
    }

    return success_response(
        request=request,
        message_key="organisations.success.organisation_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=result,
    )


@handle_api_exceptions("update organisation")
@router.put(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    description="Update an existing organisation",
    summary="Update an existing organisation",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganisationResponse,
            "description": "Organisation updated successfully",
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
async def update_organisation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    organisation_id: UUID = Path(..., description="The UUID of the organisation to update"),
    current_user: dict = Depends(get_user_from_auth),
    body: OrganizationAdminUpdate = Body(...),
):
    """Update an existing organisation (Requires: settings_management.edit)"""
    # Set audit context for organization update
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organisation_id
    request.state.audit_description = f"Updated organization: {organisation_id}"
    request.state.audit_risk_level = "medium"

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organisation_id),
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or str(organisation_id),
    }

    # Create service with user context and delegate to service
    organisation_service = OrganisationService(
        user_context=user_context, db_connection=db_connection
    )
    result = await organisation_service.update_organisation(
        organisation_id=str(organisation_id), update_data=body
    )

    request.state.audit_new_values = body.model_dump(exclude_unset=True, exclude_none=True)

    return success_response(
        request=request,
        message_key="organisations.success.organisation_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("delete organisation")
@router.delete(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    description="Delete an existing organisation",
    summary="Delete an existing organisation",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {
            "model": OrganisationResponse,
            "description": "Organisation deleted successfully",
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
async def delete_organisation_by_id(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    organisation_id: UUID = Path(..., description="The UUID of the organisation to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an existing organisation (Requires: settings_management.edit)"""
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organisation_id
    request.state.audit_description = f"Deleted organization: {organisation_id}"
    request.state.audit_risk_level = "high"

    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=str(organisation_id),
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or str(organisation_id),
    }

    # Create service with user context and delegate to service
    organisation_service = OrganisationService(
        user_context=user_context, db_connection=db_connection
    )
    await organisation_service.delete_organisation(str(organisation_id))

    return success_response(
        request=request,
        message_key="organisations.success.organisation_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
