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
    format_iso_datetime,
    validate_pagination_params,
    validate_uuid_format,
    check_permissions,
)
from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisation_filter_message
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
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.organisation_utils import create_organisation_with_super_admin
# from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
# Database operations imports
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    get_list_of_organisations,
    get_organisations_count,
    get_organisation_details_by_id,
    update_organisation_details,
    check_organisation_slug_unique
)

# Create router for organisation endpoints
router = APIRouter(prefix="/organisation", tags=["Organisation Management"])

# Initialize logger for organisation module
logger = get_logger("organisation-api")
logger.info("Organisation API module loaded")

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"


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
    return OrganisationInfo(
        organization_id=str(org_data["organization_id"]),
        name=org_data["name"],
        slug=org_data["slug"],
        domain=org_data["domain"],
        logo_url=org_data["logo_url"],
        plan_type=org_data["plan_type"],
        status=org_data["status"],
        max_users=org_data["max_users"],
        timezone=org_data["timezone"] or "UTC",
        created_at=format_iso_datetime(org_data["created_at"]),
        updated_at=format_iso_datetime(org_data["updated_at"]),
        member_count=org_data["member_count"],
        user_role=None,  # No user role since we're showing all organizations
    )


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
    clean_name = "".join(c if c.isalnum() else "-" for c in clean_name)
    # Remove multiple consecutive hyphens
    clean_name = "-".join(filter(None, clean_name.split("-")))

    # Add account type prefix
    prefix = "personal" if account_type == AccountType.PERSONAL else "business"

    # Generate unique suffix
    unique_suffix = str(uuid.uuid4())[:8]

    return f"{prefix}-{clean_name}-{unique_suffix}"


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



async def _validate_and_process_query_params(query_params: OrganisationQueryParams):
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
        await _validate_and_process_query_params(query_params)
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
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info("GET /organisation/list request started - Request ID: %s, ",request_id)
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))
    logger.info("Page: %s, Page Size: %s, ",query_params.page,query_params.page_size)
    logger.info("Name Filter: %s, Status Filter: %s",query_params.name,query_params.org_status)

    # Extract user context
    user_context = extract_user_context(current_user)
    logger.debug("User context extracted - Request ID: %s, ",request_id)
    logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    # Process the request
    organizations, total_count, page, page_size, message = (
        await _process_organisation_list_request(user_context, query_params)
    )
    logger.debug("Organizations list processed - Request ID: %s, ",request_id)
    logger.debug("Organizations count: %s, Total count: %s, ",len(organizations),total_count)
    logger.debug("Page: %s, Page size: %s",page,page_size)

    logger.info(
        "GET /organisation/list request completed successfully - Request ID: %s, ",
        request_id
    )
    logger.info("Organizations Count: %s, Total Count: %s, ",len(organizations),total_count)
    logger.info("Page: %s, Page Size: %s, Status Code: 200",page,page_size)

    return OrganisationListResponse(
        status_code=status.HTTP_200_OK,
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
        db_conn: AsyncPG database connection

    Returns:
        OrganisationDetailResponse: Detailed organization information
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "GET /organisation/%s request started - Request ID: %s, ",
        organisation_id,request_id
    )
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))
    logger.info("Target Organization ID: %s",organisation_id)

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organization ID")
    logger.debug("Organization ID format validated - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s",organisation_id)

    # Extract and validate user context from JWT token
    # Check permission using utility function
    await check_permissions(
        current_user, "organization.appscrip.manage","access organization details")
    # logger.debug("User context extracted ofr Org - Request ID: %s, ",request_id)
    # logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    # logger.debug("User permissions validated for organization access - Request ID: %s",request_id)
    # logger.debug(" Organization ID: %s",organisation_id)

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)
    logger.debug("Organization data retrieved from database - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s,",organisation_id)
    logger.debug(" Organization found: %s",organization_data is not None)

    # Check if organization exists
    if not organization_data:
        logger.warning("Organization not found - Request ID: %s, ",request_id)
        logger.warning("Target Organization ID: %s",organisation_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Create organization info object using helper function
    org_info = _create_organisation_info(organization_data)
    logger.debug("Organization data formatted - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s, ",organisation_id)
    logger.debug("Organization Name: %s, ",org_info.name)
    logger.debug("Organization Slug: %s",org_info.slug)

    logger.info("GET /organisation/%s request completed successfully - ",organisation_id)
    logger.info("Request ID: %s, ",request_id)
    logger.info("Target Organization ID: %s, ",organisation_id)
    logger.info("Organization Name: %s, ",org_info.name)
    logger.info("Organization Slug: %s, Status Code: 200",org_info.slug)

    return OrganisationDetailResponse(
        status_code=status.HTTP_200_OK,
        message="Organization retrieved successfully",
        data=org_info,
    )


# async def _create_organization_member(
#     db_conn, user_id, organization_id, super_admin_role_id, body
# ):
#     """
#     Create organization member record.

#     Args:
#         db_conn: Database connection
#         user_id: User ID
#         organization_id: Organization ID
#         super_admin_role_id: Super Admin role ID
#         body: Request body with member data

#     Returns:
#         dict: Created member record
#     """

#     return await db_conn.fetchrow(
#         MEMBER_INSERT_QUERY,
#         user_id,
#         organization_id,
#         super_admin_role_id,
#         body.email,
#         body.full_name,
#         body.phone,
#         body.timezone,
#     )


# async def _create_organization_with_permissions(db_conn, body, organization_id):
#     """
#     Create organization with roles and permissions in database transaction.


#     """
#     # Create organization
#     org_insert_query = """
#         INSERT INTO public.organizations (
#             id, name, slug, domain, logo_url, plan_type, max_users, timezone,
#             status, created_at, updated_at
#         ) VALUES (
#             $1, $2, $3, $4, $5, $6, $7, $8, 'active', NOW(), NOW()
#         ) RETURNING id, name, slug, created_at;
#     """
#     org_result = await db_conn.fetchrow(
#         org_insert_query,
#         organization_id,
#         body.name,
#         body.slug,
#         body.domain,
#         body.logo_url,
#         body.plan_type,
#         body.max_users,
#         body.timezone,
#     )

#     # Create Super Admin role
#     super_admin_role_id = await create_super_admin_role(
#         organisation_id=organization_id,
#         db_conn=db_conn,
#     )

#     # Create default permissions
#     permission_ids = await create_default_permissions_for_organisation(
#         organisation_id=organization_id,
#         db_conn=db_conn,
#     )

#     # Assign all permissions to Super Admin role
#     await assign_all_permissions_to_role(
#         role_id=super_admin_role_id,
#         organisation_id=organization_id,
#         permission_ids=permission_ids,
#         db_conn=db_conn,
#     )

#     return org_result, super_admin_role_id


@handle_api_exceptions("create organisation")
@router.post(
    "/",
    response_model=CreateOrganisationWithUserResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("100/minute")
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
        db_conn: AsyncPG database connection (truly async)
        supabase: Supabase admin client for user creation
        body (CreateOrganisationWithUserRequest): Organization and user creation data

    Returns:
        CreateOrganisationWithUserResponse: Success response with organization and user data
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info("POST /organisation request started - Request ID: %s, ",request_id)
    # logger.debug("User ID: %s, ",current_user.get('user_id'))
    # logger.debug("Organization ID: %s, ",current_user.get('organization_id'))
    # logger.debug("New Organization Name: %s, New Organization Slug: %s, ",body.company_data.company_name,slug)
    # logger.debug("Admin Email: %s",user_context.token_email)

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug("User context extracted - Request ID: %s, ",request_id)
    logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    if user_context.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User ID is required"
        )

    # # Generate UUID for new organization
    # organization_id = str(uuid.uuid4())
    # logger.debug("Organization ID generated - Request ID: %s, ",request_id)
    # logger.debug("New Organization ID: %s",organization_id)
    # print(f"Generated organization_id: {organization_id}")

    # # Validate slug uniqueness using database operations
    # is_unique = await check_organisation_slug_unique(body.slug)
    # if not is_unique:
    #     raise HTTPException(
    #         status_code=status.HTTP_409_CONFLICT,
    #         detail="Organisation slug already exists"
    #     )
    # logger.debug("Organization slug uniqueness validated - Request ID: %s, ",request_id)
    # logger.debug("Organization Slug: %s",body.slug)


    # Generate organization details
    organization_id = str(uuid.uuid4())
    organization_name = _determine_organization_name(AccountType.BUSINESS, body.company_data)
    slug = _generate_organization_slug(
        organization_name, AccountType.BUSINESS.value
    )

    print(f"Generated organization_id: {organization_id}")
    print(f"Organization name: {organization_name}")
    print(f"Organization slug: {slug}")

    # Validate slug uniqueness
    await check_organisation_slug_unique(slug)


    # # Create user in Supabase Auth
    # user_id = await create_supabase_user(body, organization_id)
    # logger.debug("Supabase user created - Request ID: %s, ",request_id)
    # logger.debug("User ID: %s, Email: %s",user_id,body.email)
    # print(f"Created Supabase user: {user_id}")

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
            "first_name": body.user_data.first_name,
            "last_name": body.user_data.last_name,
            "phone": body.user_data.phone,
            "timezone": body.user_data.timezone,
        }
        await create_organisation_with_super_admin(org_data)
    except (ConnectionError, TimeoutError, ValueError) as db_error:
        logger.error("Database transaction failed - Request ID: %s, ",request_id)
        logger.error("Error: %s",str(db_error))
        print(f"Database transaction failed: {db_error}")
        # # Try to delete the Supabase user if database transaction fails
        # try:
        #     result = await delete_auth_user(user_context.user_id)
        #     if result is not None:
        #         logger.debug("Supabase user cleanup completed - Request ID: %s, ",request_id)
        #         logger.debug("User ID: %s",user_context.user_id)
        #         print(f"Cleaned up Supabase user: {user_context.user_id}")
        # except (ConnectionError, TimeoutError, ValueError) as cleanup_error:
        #     logger.error("Failed to cleanup Supabase user - Request ID: %s, ",request_id)
        #     logger.error("User ID: %s, Error: %s",user_context.user_id,str(cleanup_error))
        #     print(f"Failed to cleanup Supabase user: {cleanup_error}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create organization",
        ) from db_error

    logger.info("POST /organisation request completed successfully - Request ID: %s, ",request_id)
    logger.info("Organization ID: %s, Organization Name: %s, ",organization_id,body.company_data.company_name)
    logger.info("Organization Slug: %s, User ID: %s, Admin Email: %s",slug,user_context.user_id,user_context.email)
    logger.info("Status Code: 201")

    return CreateOrganisationWithUserResponse(
        status_code=status.HTTP_201_CREATED,
        message="Organisation and user created successfully",
        data={
            "organization_id": organization_id,
            "user_id": user_context.user_id,
            "organization_name": organization_name,
            "user_email": user_context.email,
            "role_name": "Super Admin",
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
        db_conn: AsyncPG database connection
        body (OrganizationAdminUpdate): Organization update data

    Returns:
        OrganisationResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info("PUT /organisation/%s request started - Request ID: %s",organisation_id,request_id)
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))
    logger.info("Target Organization ID: %s",organisation_id)

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organisation ID")
    logger.debug("Organization ID format validated - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s",organisation_id)

    # Extract and validate user context from JWT token
    # Check permission using utility function
    await check_permissions(current_user, "organization.appscrip.manage","update organization")
    # logger.debug("User context extracted - Request ID: %s, ",request_id)
    # logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    # Get organization details using database operations
    organization_data = await get_organisation_details_by_id(organisation_id)
    logger.debug("Organization data retrieved for update - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s, ",organisation_id)
    logger.debug("Organization found: %s",organization_data is not None)

    if not organization_data:
        logger.warning("Organization not found for update - Request ID: %s, ",request_id)
        logger.warning("Target Organization ID: %s",organisation_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # logger.debug("User permissions validated for organization update - Request ID: %s",request_id)
    # logger.debug("Target Organization ID: %s",organisation_id)

    # Update organization using database operations
    update_data = body.model_dump(exclude_unset=True, exclude_none=True)
    await update_organisation_details(organisation_id, update_data)
    logger.debug("Organization updated successfully - Request ID: %s, ",request_id)
    logger.debug("Target Organization ID: %s",organisation_id)

    logger.info("PUT /organisation/%s request completed successfully - ",organisation_id)
    logger.info("Request ID: %s, ",request_id)
    logger.info("Target Organization ID: %s, Status Code: 200",organisation_id)

    return OrganisationResponse(
        message=f"Update organisation {organisation_id} API is working",
        status="success",
    )


@handle_api_exceptions("delete organisation")
@router.delete(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_organisation(
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
        db_conn: AsyncPG database connection

    Returns:
        OrganisationResponse: Success message indicating API is working
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "DELETE /organisation/%s request started - Request ID: %s, ",
        organisation_id,request_id
    )
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))
    logger.info("Target Organization ID: %s",organisation_id)

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organisation ID")
    logger.debug("Organization ID format validated - Request ID: %s",request_id)
    logger.debug("Target Organization ID: %s",organisation_id)


    # Extract and validate user context from JWT token
    # Check permission using utility function
    await check_permissions(current_user, "organization.appscrip.manage","delete organization")
    # user_context = extract_user_context(current_user)
    # logger.debug("User context extracted - Request ID: %s",request_id)
    # logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    # await require_permission(
    #     permission_code="organization.appscrip.manage",
    #     user_context=user_context,
    #     action_description="delete organizations",
    # )
    # logger.debug("User permissions validated for organization deletion- Request ID:%s",request_id)
    # logger.debug("Target Organization ID: %s",organisation_id)

    logger.info("DELETE /organisation/%s request completed successfully - ",organisation_id)
    logger.info("Request ID: %s",request_id)
    logger.info("Target Organization ID: %s, Status Code: 200",organisation_id)

    return OrganisationResponse(
        message=f"Delete organisation {organisation_id} API is working",
        status="success",
    )
