"""
Organisation Management API Module

This module provides CRUD operations for organisation management.
All endpoints include proper authentication, validation, and database operations.

"""

import uuid
from typing import Optional
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, status, Depends, Body, Query, Request

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.app_instance import limiter

# Local imports - app dependencies and schemas
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    require_permission,
    handle_api_exceptions,
    validate_pagination_params,
    validate_uuid_format,
    check_permissions,
)

# Audit logging import
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisation_filter_message,
    create_organisation_with_super_admin
)

# Schema imports
from apps.user_service.app.schemas.organisations import (
    OrganisationInfo,
    OrganisationListResponse,
    OrganisationResponse,
    OrganisationDetailResponse,
    CreateOrganisationWithUserResponse,
    OrganizationAdminUpdate,
    NewOrganisationBody
)
from apps.user_service.app.schemas.auth import CompanyData, AccountType

# Third-party imports
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import log_exception
from libs.shared_middleware.jwt_auth import get_user_from_auth
# from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    get_list_of_organisations,
    get_organisations_count,
    get_organisation_details_by_id,
    update_organisation_details,
    check_organisation_slug_unique,
    delete_organisation
)

# Create router for organisation endpoints
router = APIRouter(prefix="/organisation", tags=["Organisation Management"])

# Initialize logger for organisation module
logger = get_logger("organisation-api")

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"
ORGANISATION_NOT_FOUND_MESSAGE = "Organisation not found"


@dataclass
class OrganisationQueryParams:
    """Query parameters for organisation listing and filtering."""

    page: int = 1
    page_size: int = 20
    name: Optional[str] = None
    org_status: Optional[str] = None


def get_organisation_query_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    name: Optional[str] = Query(None),
    org_status: Optional[str] = Query(None),
) -> OrganisationQueryParams:
    """
    Dependency function to extract and validate organisation query parameters.

    Args:
        page: Page number for pagination
        page_size: Number of items per page
        name: Filter by organization name
        org_status: Filter by organization status

    Returns:
        OrganisationQueryParams: Validated query parameters
    """
    return OrganisationQueryParams(
        page=page, page_size=page_size, name=name, org_status=org_status
    )


def _create_organisation_info(org_data: dict) -> OrganisationInfo:
    """
    Create OrganisationInfo object from database row.

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
        plan_type=org_data["plan_type"],
        status=org_data["status"],
        max_users=org_data["max_users"],
        timezone=org_data["timezone"] or "UTC",
        created_at=org_data["created_at"],
        updated_at=org_data["updated_at"],
        member_count=org_data["member_count"],
        address=org_data["settings"].get("address",None),
        preferred_integration=org_data["settings"].get("preferred_integration",None),
        need_help_importing_data=org_data["settings"].get("need_help_importing_data",None),
        need_migration_assistance=org_data["settings"].get("need_migration_assistance",None),
        compliance_security=org_data["settings"].get("compliance_security",None),
        enterprise_features=org_data["settings"].get("enterprise_features",None),
        team_setup=org_data["settings"].get("team_setup",None),
        description=org_data["description"],
        company_size=org_data["company_size"]
    )
    if org_data["settings"].get("practice_areas",None):
        prac_area = org_data["settings"].get("practice_areas")
        result.primary_practice_areas=prac_area.get("primary",None)
        result.secondary_practice_areas=prac_area.get("secondary",None)
        result.specializations=prac_area.get("specializations",None)
    else:
        result.primary_practice_areas=None
        result.secondary_practice_areas=None
        result.specializations=None
    return result


def _process_organisations_data(organizations_data, count_result: dict | int) -> tuple:
    """
    Process organisations data and count result.

    Args:
        organizations_data: Raw organization data from database
        count_result: Count query result

    Returns:
        tuple: (organizations_list, total_count)
    """
    organizations = [_create_organisation_info(org) for org in organizations_data]
    if isinstance(count_result, dict):
        total_count = count_result["total_count"]
    elif isinstance(count_result, int):
        total_count = count_result
    else:
        total_count = 0
    return organizations, total_count

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def _generate_organization_slug(name: str, account_type: str) -> str:
    """
    Generate organization slug from name and account type.

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


def _determine_organization_name(acc_type: AccountType, company_data: CompanyData) -> str:
    """
    Determine organization name based on account type.

    Args:
        acc_type (AccountType): Account type
        company_data (CompanyData): Company data

    Returns:
        str: Organization name
    """
    if acc_type == AccountType.PERSONAL:
        return f"{company_data.first_name} {company_data.last_name}"
    return (
        company_data.company_name
        if company_data
        else "Unknown Company"
    )


def _validate_and_process_query_params(query_params: OrganisationQueryParams):
    """
    Validate and process query parameters for organisation listing.

    Args:
        query_params: Query parameters to validate

    Returns:
        tuple: (page, page_size, offset, validated_name, validated_status)
    """
    # Validate pagination parameters
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )

    # Validate filter parameters
    validated_status = None
    if query_params.org_status:
        validate_organisation_status(query_params.org_status)
        validated_status = query_params.org_status

    validated_name = None
    if query_params.name:
        validated_name = validate_organisation_name_filter(query_params.name)

    return page, page_size, offset, validated_name, validated_status


async def _execute_organisation_queries(name, org_status, page_size, offset):
    """
    Execute organisation queries and return processed results.

    Args:
        name: Name filter
        org_status: Status filter
        page_size: Items per page
        offset: Query offset

    Returns:
        tuple: (organizations, total_count)
    """
    # Execute queries using database operations
    organizations_data = await get_list_of_organisations(
        search=name, status=org_status, limit=page_size, offset=offset
    )
    total_count = await get_organisations_count(
        search=name, status=org_status
    )

    # Process results
    return _process_organisations_data(organizations_data, {"total_count": total_count})


async def _process_organisation_list_request(
    user_context, query_params: OrganisationQueryParams
):
    """
    Process the complete organisation list request.

    Args:
        user_context: User context from JWT
        query_params: Query parameters

    Returns:
        tuple: (organizations, total_count, page, page_size, message)
    """
    # Check permissions
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        action_description="access organization list",
    )

    # Validate and process query parameters
    page, page_size, offset, validated_name, validated_status = (
        _validate_and_process_query_params(query_params)
    )

    # Execute queries and get results
    organizations, total_count = await _execute_organisation_queries(
        validated_name, validated_status, page_size, offset
    )

    # Build response message
    message = build_organisation_filter_message(
        name=validated_name,
        org_status=validated_status,
        page=page,
        page_size=page_size,
    )

    return organizations, total_count, page, page_size, message


@handle_api_exceptions("get organisations list")
@router.get(
    "/list",
    response_model=OrganisationListResponse,
    status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_organisations_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: OrganisationQueryParams = Depends(get_organisation_query_params),
):
    """
    Get list of all organizations in the system (Requires: organization.appscrip.manage)

    This endpoint retrieves all organizations in the system including:
    - Organization basic information (name, slug, domain, logo, etc.)
    - Plan type and status information

    Args:
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        query_params (OrganisationQueryParams): Query parameters for filtering and pagination

    Filter Features:
    - Name filtering: Case-insensitive partial match on organization name
    - Status filtering: Exact match on organization status (active, suspended, trial)
    - Filters are combined with AND logic
    - Proper validation and sanitization of filter inputs

    Returns:
        OrganisationListResponse: List of organizations with pagination info
    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Extract user context
    user_context = await extract_user_context(current_user)

    # Process the request
    organizations, total_count, page, page_size, message = (
        await _process_organisation_list_request(user_context, query_params)
    )


    return OrganisationListResponse(
        message=message,
        data=organizations,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("get organisation by ID")
@router.get(
    "/{organisation_id}",
    response_model=OrganisationDetailResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_organisation_by_id(
    organisation_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Get organization by ID with complete details (Requires: organization.appscrip.manage)

    Args:
        organisation_id (str): The UUID of the organisation to retrieve
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information

    Returns:
        OrganisationDetailResponse: Detailed organization information
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organization ID")

    # Extract and validate user context from JWT token
    # Check permission using utility function
    await check_permissions(
        current_user, SETTINGS_SYSTEM_MANAGE,"access organization details", organisation_id)

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)

    # Check if organization exists
    if not organization_data:
        logger.warning("Organization not found - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ORGANISATION_NOT_FOUND_MESSAGE,
        )

    # Create organization info object using helper function
    org_info = _create_organisation_info(organization_data)


    return OrganisationDetailResponse(
        message="Organization retrieved successfully",
        data=org_info,
    )


@handle_api_exceptions("create organisation")
@router.post(
    "/",
    response_model=CreateOrganisationWithUserResponse,
    status_code=status.HTTP_201_CREATED,
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
# pylint: disable=unused-argument  # Required by @limiter.limit
async def create_organisation(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: NewOrganisationBody = Body(...),
):
    """
    Create a new organisation with initial Super Admin user (Requires: organization.appscrip.manage)

    This endpoint creates a complete organization setup including:
    1. User signup with Supabase Auth
    2. Organization creation in database
    3. Super Admin role and permissions setup (including organization.appscrip.manage)
    4. Organization member creation with role assignment

    Args:
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        body (CreateOrganisationWithUserRequest): Organization and user creation data

    Returns:
        CreateOrganisationWithUserResponse: Success response with organization and user data
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for organization creation
    request.state.audit_table = "organizations"
    request.state.audit_description = f"Created new organization: {body.company_data.company_name}"
    request.state.audit_risk_level = "high"

    # Extract and validate user context from JWT token
    user_context = await extract_user_context(current_user)

    if user_context.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID is required"
        )

    if user_context.organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already belongs to an organization"
        )

    # Generate organization details
    organization_id = str(uuid.uuid4())
    organization_name = _determine_organization_name(AccountType.BUSINESS, body.company_data)
    slug = _generate_organization_slug(organization_name, AccountType.BUSINESS.value)

    # Validate slug uniqueness
    result = await check_organisation_slug_unique(slug)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation slug already exists"
        )

    # Create organization using database operations
    try:
        # Create organization
        org_data = {
            "organization_id": organization_id,
            "slug": slug,
            "name": body.company_data.company_name,
            "domain": body.company_data.company_website,
            "industry": body.company_data.industry,
            "company_size": body.company_data.company_size,
            "description": body.company_data.description,
            "referral_source": body.company_data.referral_source,
            "max_users": body.company_data.max_users,
            "logo_url": body.company_data.logo_url,
            "plan_type": body.plan_type.value,
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
            "team_setup": body.company_data.team_setup
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
    except HTTPException:
        # Re-raise HTTP exceptions (like 409 Conflict for duplicate slug)
        raise
    except (ConnectionError, TimeoutError, ValueError) as db_error:
        log_exception()
        logger.error("Database transaction failed - Request ID: %s, ",request_id)
        logger.error("Error: %s",str(db_error))

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create organization",
        ) from db_error


    return CreateOrganisationWithUserResponse(
        message="Organisation and user created successfully",
        data={
            "organization_id": organization_id,
            "user_id": user_context.user_id,
            "organization_name": organization_name,
            "user_email": user_context.email,
            "role_name": "admin",
            "slug": slug,
            "plan_type": body.plan_type.value,
            "max_users": body.company_data.max_users,
        },
    )


@handle_api_exceptions("update organisation")
@router.put(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    status_code=status.HTTP_200_OK,
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
# pylint: disable=unused-argument  # Required by @limiter.limit
async def update_organisation(
    organisation_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    body: OrganizationAdminUpdate = Body(...),
):
    """
    Update an existing organisation

    Args:
        organisation_id (str): The UUID of the organisation to update
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information
        body (OrganizationAdminUpdate): Organization update data

    Returns:
        OrganisationResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Set audit context for organization update
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = organisation_id
    request.state.audit_description = f"Updated organization: {organisation_id}"
    request.state.audit_risk_level = "medium"

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organisation ID")

    # Extract and validate user context from JWT token
    # Check permission using utility function
    user_context = await check_permissions(
        current_user, SETTINGS_SYSTEM_MANAGE,"update organization", organisation_id)

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id or organisation_id,
    }

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)

    if not organization_data:
        logger.warning("Organization not found for update - Request ID: %s, ",request_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ORGANISATION_NOT_FOUND_MESSAGE,
        )

    # Update organization using database operations
    update_data = body.model_dump(exclude_unset=True, exclude_none=True)
    await update_organisation_details(organisation_id, organization_data, update_data)

    # Update Isometrik application if organization name is being updated (non-blocking)
    if "name" in update_data:
        try:
            from libs.shared_utils.isometrik_service import (
                update_isometrik_application,
                get_isometrik_data_from_settings
            )
            
            # Get Isometrik application_id from organization settings
            isometrik_data = get_isometrik_data_from_settings(organization_data.get("settings"))
            application_id = isometrik_data.get("projectId") if isometrik_data else None  # projectId is the application ID
            
            await update_isometrik_application(
                organization_id=organisation_id,
                organization_name=update_data["name"],
                application_id=application_id
            )
            logger.info(
                "Successfully updated Isometrik application name for organization: %s (applicationId: %s)",
                organisation_id,
                application_id or organisation_id
            )
        except Exception as isometrik_error:
            # Log error but don't fail organization update
            logger.warning(
                "Failed to update Isometrik application for organization %s: %s",
                organisation_id,
                str(isometrik_error)
            )
            # Continue with organization update even if Isometrik fails

    request.state.audit_new_values = update_data

    return OrganisationResponse(
        message="Update organisation successfully",
        status="success",
    )


@handle_api_exceptions("delete organisation")
@router.delete(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    status_code=status.HTTP_200_OK,
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
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_organisation_by_id(
    organisation_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Delete an organisation

    Args:
        organisation_id (str): The UUID of the organisation to delete
        request (Request): FastAPI request object for rate limiting
        current_user (dict): Decoded JWT token containing user information

    Returns:
        OrganisationResponse: Success message indicating API is working
    """
    try:
        # # Generate request ID for tracking
        # request_id = str(uuid.uuid4())

        # Set audit context for organization deletion
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

        # Validate organization ID format using utility function
        validate_uuid_format(organisation_id, "organisation ID")

        current_user['user_metadata']['organization_id'] = organisation_id

        # Extract and validate user context from JWT token
        # Check permission using utility function
        await check_permissions(
            current_user, SETTINGS_SYSTEM_MANAGE,"delete organization", organisation_id)

        result = await delete_organisation(organisation_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ORGANISATION_NOT_FOUND_MESSAGE,
            )

        return OrganisationResponse(
            message=f"Delete organisation {organisation_id} API is working",
            status="success",
        )
    except HTTPException as http_error:
        raise http_error
    except Exception as db_error:
        log_exception()
        logger.error("Error: %s",str(db_error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete organization",
        ) from db_error
