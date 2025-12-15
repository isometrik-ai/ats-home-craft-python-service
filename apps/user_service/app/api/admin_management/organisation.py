"""Organisation Management API Module.

This module provides CRUD operations for organisation management.
All endpoints include proper authentication, validation, and database operations.
"""

import uuid
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call

# Local imports - app dependencies and schemas
from apps.user_service.app.dependencies.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
    require_permission,
)

# Logger import
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.organisation_utils import (
    create_organisation_with_super_admin,
)
from apps.user_service.app.schemas.auth import AccountType

# Schema imports
from apps.user_service.app.schemas.organisations import (
    CreateOrganisationWithUserResponse,
    NewOrganisationBody,
    OrganisationDetailResponse,
    OrganisationInfo,
    OrganisationListResponse,
    OrganisationResponse,
    OrganizationAdminUpdate,
)

# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    check_organisation_slug_unique,
    delete_organisation,
    get_list_of_organisations,
    get_organisation_details_by_id,
    get_organisations_count,
    update_organisation_details,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# Create router for organisation endpoints
router = APIRouter(prefix="/organisation", tags=["Organisation Management"])

# Initialize logger for organisation module
logger = get_logger("organisation-api")


def _create_organisation_info(org_data: dict) -> OrganisationInfo:
    """Create OrganisationInfo object from database row.

    Args:
        org_data (dict): Organisation data from database

    Returns:
        OrganisationInfo: Formatted organisation info object
    """
    result = OrganisationInfo(
        organization_id=str(org_data["id"]),
        name=org_data["name"],
        slug=org_data["slug"],
        domain=org_data["domain"],
        logo_url=org_data["logo_url"],
        status=org_data["status"],
        timezone=org_data["timezone"] or "UTC",
        created_at=org_data["created_at"],
        updated_at=org_data["updated_at"],
        member_count=org_data["member_count"],
        address=org_data["settings"].get("address", None),
        preferred_integration=org_data["settings"].get("preferred_integration", None),
        need_help_importing_data=org_data["settings"].get("need_help_importing_data", None),
        need_migration_assistance=org_data["settings"].get("need_migration_assistance", None),
        compliance_security=org_data["settings"].get("compliance_security", None),
        enterprise_features=org_data["settings"].get("enterprise_features", None),
        team_setup=org_data["settings"].get("team_setup", None),
        description=org_data["description"],
        company_size=org_data["company_size"],
        subscription=org_data["subscription"],
    )
    if org_data["settings"].get("practice_areas", None):
        prac_area = org_data["settings"].get("practice_areas")
        result.primary_practice_areas = prac_area.get("primary", None)
        result.secondary_practice_areas = prac_area.get("secondary", None)
        result.specializations = prac_area.get("specializations", None)
    else:
        result.primary_practice_areas = None
        result.secondary_practice_areas = None
        result.specializations = None
    return result


def _generate_organization_slug(name: str, account_type: str) -> str:
    """Generate organization slug from name and account type.

    Args:
        name (str): Organization name
        account_type (str): Account type (personal/business)

    Returns:
        str: Generated slug
    """
    # Clean and format name
    clean_name = name.lower().strip()
    # Replace spaces and special characters with hyphens
    clean_name2 = "".join(c if c.isalnum() else "-" for c in clean_name)
    # Remove multiple consecutive hyphens
    clean_name3 = "-".join(filter(None, clean_name2.split("-")))

    # Add account type prefix
    prefix = "personal" if account_type == AccountType.PERSONAL else "business"

    return f"{prefix}-{clean_name3}"


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
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    name: str | None = Query(None, description="The name of the organisation"),
    org_status: str | None = Query(None, description="The status of the organisation"),
):
    """Get list of all organisations in the system (Requires: settings_management.edit)"""
    user_context = await extract_user_context(current_user)
    # Check permissions
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
    )

    # Execute queries using database operations
    organizations_data = await get_list_of_organisations(
        search=name,
        status=org_status,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    organizations = []
    total_count = 0
    if not organizations_data:
        message_key = "success.no_data"
        custom_code = CustomStatusCode.NO_CONTENT
    else:
        message_key = "success.retrieved"
        custom_code = CustomStatusCode.SUCCESS
        organizations = [_create_organisation_info(org) for org in organizations_data]
        total_count = await get_organisations_count(search=name, status=org_status)

    return list_response(
        request=request,
        items=organizations,
        total=total_count,
        message_key=message_key,
        page=page,
        page_size=page_size,
        status_code=http_status.HTTP_200_OK,
        custom_code=custom_code,
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
    organisation_id: UUID = Path(..., description="The UUID of the organisation to get"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get organisation by ID with complete details (Requires: settings_management.edit)"""

    # Extract and validate user context from JWT token
    # Check permissions
    await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=organisation_id,
    )

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)

    if organization_data:
        message_key = "success.retrieved"
        custom_code = CustomStatusCode.SUCCESS
        data = _create_organisation_info(organization_data)
    else:
        message_key = "success.no_data"
        custom_code = CustomStatusCode.NO_CONTENT
        data = {}

    return success_response(
        request=request,
        message_key=message_key,
        custom_code=custom_code,
        status_code=http_status.HTTP_200_OK,
        data=data,
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

    # Extract and validate user context from JWT token
    user_context = await extract_user_context(current_user)

    if user_context.user_id is None:
        raise ForbiddenException(
            message_key="organisations.errors.forbidden",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    if user_context.organization_id is not None:
        raise ConflictException(
            message_key="organisations.errors.conflict",
            custom_code=CustomStatusCode.CONFLICT,
        )

    # Generate organization details
    organization_id = str(uuid.uuid4())
    organization_name = body.company_data.company_name
    slug = _generate_organization_slug(organization_name, AccountType.BUSINESS.value)

    # Validate slug uniqueness
    is_slug_unique = await check_organisation_slug_unique(slug)
    if not is_slug_unique:
        raise ConflictException(
            message_key="organisations.errors.slug_conflict",
            custom_code=CustomStatusCode.CONFLICT,
        )

    # Create organization using database operations
    org_data = {
        "organization_id": organization_id,
        "slug": slug,
        "name": body.company_data.company_name,
        "domain": body.company_data.company_website,
        "industry": body.company_data.industry,
        "company_size": body.company_data.company_size,
        "description": body.company_data.description,
        "referral_source": body.company_data.referral_source,
        "logo_url": body.company_data.logo_url,
        "status": "active",
        "user_id": user_context.user_id,
        "email": user_context.email,
        "address": body.company_data.address,
        "primary_practice_areas": body.company_data.primary_practice_areas,
        "secondary_practice_areas": body.company_data.secondary_practice_areas,
        "specializations": body.company_data.specializations,
        "preferred_integration": body.company_data.preferred_integration,
        "need_help_importing_data": body.company_data.need_help_importing_data,
        "need_migration_assistance": body.company_data.need_migration_assistance,
        "compliance_security": body.company_data.compliance_security,
        "enterprise_features": body.company_data.enterprise_features,
        "team_setup": body.company_data.team_setup,
    }
    if body.user_data is not None:
        for key, value in body.user_data.model_dump().items():
            org_data[key] = value
    await create_organisation_with_super_admin(org_data)
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": organization_id,
    }
    return success_response(
        request=request,
        message_key="organisations.success.organisation_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data={
            "organization_id": organization_id,
            "user_id": user_context.user_id,
            "organization_name": organization_name,
            "user_email": user_context.email,
            "role_name": "admin",
            "slug": slug,
        },
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

    # Extract and validate user context from JWT token
    # Check permissions
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=organisation_id,
    )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or organisation_id,
    }

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)

    if not organization_data:
        raise NotFoundException(
            message_key="organisations.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    # Update organization using database operations
    update_data = body.model_dump(exclude_unset=True, exclude_none=True)
    await update_organisation_details(organisation_id, organization_data, update_data)

    request.state.audit_new_values = update_data

    return success_response(
        request=request,
        message_key="organisations.success.organisation_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
        data={
            "organization_id": organisation_id,
            "organization_name": organization_data["name"],
            "slug": organization_data["slug"],
        },
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
    organisation_id: UUID = Path(..., description="The UUID of the organisation to delete"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete an existing organisation (Requires: settings_management.edit)"""
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organisation_id
    request.state.audit_description = f"Deleted organization: {organisation_id}"
    request.state.audit_risk_level = "high"

    user_context = await extract_user_context(current_user)
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or organisation_id,
    }

    current_user["user_metadata"]["organization_id"] = organisation_id

    # Check permissions
    await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        organization_id=organisation_id,
    )

    result = await delete_organisation(organisation_id)
    if not result:
        raise NotFoundException(
            message_key="organisations.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    return success_response(
        request=request,
        message_key="organisations.success.organisation_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
