"""
Organisation Management API Module

This module provides CRUD operations for organisation management.
All endpoints include proper authentication, validation, and database operations.

"""

import uuid
from typing import Optional, List, Tuple
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, status, Depends, Body, Query, Request

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Local imports - app dependencies and schemas
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    require_permission,
    handle_api_exceptions,
    format_iso_datetime,
    validate_pagination_params,
    validate_uuid_format,
)
from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisations_filter_query,
    build_organisations_count_query,
    build_organisation_detail_query,
    build_organisation_filter_message,
    check_organisation_slug_unique,
    create_default_permissions_for_organisation,
    create_super_admin_role,
    assign_all_permissions_to_role,
)

# Schema imports
from apps.user_service.app.schemas.organisations import (
    OrganisationInfo,
    OrganisationListResponse,
    OrganisationResponse,
    OrganisationDetailResponse,
    CreateOrganisationWithUserRequest,
    CreateOrganisationWithUserResponse,
    OrganizationAdminUpdate,
)


from apps.user_service.app.app_instance import limiter

# Third-party imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_utils.common_query import MEMBER_INSERT_QUERY


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


def _process_organisations_data(organizations_data, count_result) -> tuple:
    """
    Process organisations data and count result.

    Args:
        organizations_data: Raw organization data from database
        count_result: Count query result

    Returns:
        tuple: (organizations_list, total_count)
    """
    organizations = [_create_organisation_info(org) for org in organizations_data]
    total_count = count_result["total_count"] if count_result else 0
    return organizations, total_count


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


async def _execute_organisation_queries(db_conn, name, org_status, page_size, offset):
    """
    Execute organisation queries and return processed results.

    Args:
        db_conn: Database connection
        name: Name filter
        org_status: Status filter
        page_size: Items per page
        offset: Query offset

    Returns:
        tuple: (organizations, total_count)
    """
    # Build queries
    organizations_query, query_params = build_organisations_filter_query(
        name=name, org_status=org_status, page_size=page_size, offset=offset
    )
    count_query, count_params = build_organisations_count_query(
        name=name, org_status=org_status
    )

    # Execute queries
    organizations_data = await db_conn.fetch(organizations_query, *query_params)
    count_result = await db_conn.fetchrow(count_query, *count_params)

    # Process results
    return _process_organisations_data(organizations_data, count_result)


async def _process_organisation_list_request(
    user_context, db_conn, query_params: OrganisationQueryParams
):
    """
    Process the complete organisation list request.

    Args:
        user_context: User context from JWT
        db_conn: Database connection
        query_params: Query parameters

    Returns:
        tuple: (organizations, total_count, page, page_size, message)
    """
    # Check permissions
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="access organization list",
    )

    # Validate and process query parameters
    page, page_size, offset, validated_name, validated_status = (
        await _validate_and_process_query_params(query_params)
    )

    # Execute queries and get results
    organizations, total_count = await _execute_organisation_queries(
        db_conn, validated_name, validated_status, page_size, offset
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
    "/list", response_model=OrganisationListResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def get_organisations_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
        db_conn: AsyncPG database connection
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
    logger.info(
        ("GET /organisation/list request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Page: %s, Page Size: %s, ",query_params.page,query_params.page_size),
        ("Name Filter: %s, Status Filter: %s",query_params.name,query_params.org_status)
    )

    # Extract user context
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Process the request
    organizations, total_count, page, page_size, message = (
        await _process_organisation_list_request(user_context, db_conn, query_params)
    )
    logger.debug(
        ("Organizations list processed - Request ID: %s, ",request_id),
        ("Organizations count: %s, Total count: %s, ",len(organizations),total_count),
        ("Page: %s, Page size: %s",page,page_size)
    )

    logger.info(
        ("GET /organisation/list request completed successfully - Request ID: %s, ",request_id),
        ("Organizations Count: %s, Total Count: %s, ",len(organizations),total_count),
        ("Page: %s, Page Size: %s, Status Code: 200",page,page_size)
    )

    return OrganisationListResponse(
        status_code=status.HTTP_200_OK,
        message=message,
        data=organizations,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


async def _create_supabase_user(supabase, body, organization_id):
    """
    Create user in Supabase Auth with organization metadata.

    Args:
        supabase: Supabase admin client
        body: Request body with user data
        organization_id: Organization ID to associate with user

    Returns:
        str: Created user ID

    Raises:
        HTTPException: For duplicate email or Supabase errors
    """
    try:
        supabase_response = supabase.auth.admin.create_user(
            {
                "email": body.email,
                "password": body.password,
                "email_confirm": True,  # Auto-confirm email for admin user
                "user_metadata": {
                    "organization_id": organization_id,
                    "full_name": body.full_name,
                    "phone": body.phone,
                    "is_super_admin": True,
                    "type": "",
                },
            }
        )
        return supabase_response.user.id

    except (ConnectionError, TimeoutError, ValueError) as supabase_error:
        print(f"Supabase user creation failed: {supabase_error}")
        if (
            "already_exists" in str(supabase_error).lower()
            or "duplicate" in str(supabase_error).lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already exists"
            ) from supabase_error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from supabase_error


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
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
        ("GET /organisation/%s request started - Request ID: %s, ",organisation_id,request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Target Organization ID: %s",organisation_id)
    )

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organization ID")
    logger.debug(
        ("Organization ID format validated - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted ofr Org - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Check permission using utility function
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="access organization details",
    )
    logger.debug(
        ("User permissions validated for organization access - Request ID: %s, ",request_id),
        (" Organization ID: %s",organisation_id)
    )

    # Get organization details using utility function
    organization_query = build_organisation_detail_query()
    organization_data = await db_conn.fetchrow(organization_query, organisation_id)
    logger.debug(
        ("Organization data retrieved from database - Request ID: %s, ",request_id),
        ("Target Organization ID: %s,",organisation_id),
        (" Organization found: %s",organization_data is not None)
    )

    # Check if organization exists
    if not organization_data:
        logger.warning(
            ("Organization not found - Request ID: %s, ",request_id),
            ("Target Organization ID: %s",organisation_id)
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Create organization info object using helper function
    org_info = _create_organisation_info(organization_data)
    logger.debug(
        ("Organization data formatted - Request ID: %s, ",request_id),
        ("Target Organization ID: %s, ",organisation_id),
        ("Organization Name: %s, ",org_info.name),
        ("Organization Slug: %s",org_info.slug)
    )

    logger.info(
        ("GET /organisation/%s request completed successfully - ",organisation_id),
        ("Request ID: %s, ",request_id),
        ("Target Organization ID: %s, ",organisation_id),
        ("Organization Name: %s, ",org_info.name),
        ("Organization Slug: %s, Status Code: 200",org_info.slug)
    )

    return OrganisationDetailResponse(
        status_code=status.HTTP_200_OK,
        message="Organization retrieved successfully",
        data=org_info,
    )


async def _create_organization_member(
    db_conn, user_id, organization_id, super_admin_role_id, body
):
    """
    Create organization member record.

    Args:
        db_conn: Database connection
        user_id: User ID
        organization_id: Organization ID
        super_admin_role_id: Super Admin role ID
        body: Request body with member data

    Returns:
        dict: Created member record
    """

    return await db_conn.fetchrow(
        MEMBER_INSERT_QUERY,
        user_id,
        organization_id,
        super_admin_role_id,
        body.email,
        body.full_name,
        body.phone,
        body.timezone,
    )


async def _create_organization_with_permissions(db_conn, body, organization_id):
    """
    Create organization with roles and permissions in database transaction.


    """
    # Create organization
    org_insert_query = """
        INSERT INTO public.organizations (
            id, name, slug, domain, logo_url, plan_type, max_users, timezone,
            status, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, 'active', NOW(), NOW()
        ) RETURNING id, name, slug, created_at;
    """
    org_result = await db_conn.fetchrow(
        org_insert_query,
        organization_id,
        body.name,
        body.slug,
        body.domain,
        body.logo_url,
        body.plan_type,
        body.max_users,
        body.timezone,
    )

    # Create Super Admin role
    super_admin_role_id = await create_super_admin_role(
        organisation_id=organization_id,
        db_conn=db_conn,
    )

    # Create default permissions
    permission_ids = await create_default_permissions_for_organisation(
        organisation_id=organization_id,
        db_conn=db_conn,
    )

    # Assign all permissions to Super Admin role
    await assign_all_permissions_to_role(
        role_id=super_admin_role_id,
        organisation_id=organization_id,
        permission_ids=permission_ids,
        db_conn=db_conn,
    )

    return org_result, super_admin_role_id


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
    db_conn=Depends(get_async_db_conn),
    supabase=Depends(get_supabase_admin_client),
    body: CreateOrganisationWithUserRequest = Body(...),
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
    logger.info(
        ("POST /organisation request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("New Organization Name: %s, New Organization Slug: %s, ",body.name,body.slug),
        ("Admin Email: %s",body.email)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Check permission using utility function
    # await require_permission(
    #     permission_code="organization.appscrip.manage",
    #     user_context=user_context,
    #     db_conn=db_conn,
    #     action_description="create new organizations",
    # )

    # Generate UUID for new organization
    organization_id = str(uuid.uuid4())
    logger.debug(
        ("Organization ID generated - Request ID: %s, ",request_id),
        ("New Organization ID: %s",organization_id)
    )
    print(f"Generated organization_id: {organization_id}")

    # Validate slug uniqueness using utility function
    await check_organisation_slug_unique(body.slug, db_conn)
    logger.debug(
        ("Organization slug uniqueness validated - Request ID: %s, ",request_id),
        ("Organization Slug: %s",body.slug)
    )

    # Create user in Supabase Auth
    user_id = await _create_supabase_user(supabase, body, organization_id)
    logger.debug(
        ("Supabase user created - Request ID: %s, ",request_id),
        ("User ID: %s, Email: %s",user_id,body.email)
    )
    print(f"Created Supabase user: {user_id}")

    # Create organization, role, permissions, and member in database transaction
    try:
        async with db_conn.transaction():
            # Create organization with permissions
            org_result, super_admin_role_id = (
                await _create_organization_with_permissions(
                    db_conn, body, organization_id
                )
            )
            logger.debug(
                ("Organization with permissions created - Request ID: %s, ",request_id),
                ("Organization ID: %s, ",org_result['id']),
                ("Super Admin Role ID: %s",super_admin_role_id)
            )
            print(f"Created organization: {org_result['id']}")
            print(f"Created Super Admin role: {super_admin_role_id}")

            # Create organization member
            member_result = await _create_organization_member(
                db_conn, user_id, organization_id, super_admin_role_id, body
            )
            logger.debug(
                ("Organization member created - Request ID: %s, ",request_id),
                ("Member ID: %s, User ID: %s",member_result['id'],user_id)
            )
            print(f"Created organization member: {member_result['id']}")

    except (ConnectionError, TimeoutError, ValueError) as db_error:
        logger.error(
            ("Database transaction failed - Request ID: %s, ",request_id),
            ("Error: %s",str(db_error))
        )
        print(f"Database transaction failed: {db_error}")
        # Try to delete the Supabase user if database transaction fails
        try:
            supabase.auth.admin.delete_user(user_id)
            logger.debug(
                ("Supabase user cleanup completed - Request ID: %s, ",request_id),
                ("User ID: %s",user_id)
            )
            print(f"Cleaned up Supabase user: {user_id}")
        except (ConnectionError, TimeoutError, ValueError) as cleanup_error:
            logger.error(
                ("Failed to cleanup Supabase user - Request ID: %s, ",request_id),
                ("User ID: %s, Error: %s",user_id,str(cleanup_error))
            )
            print(f"Failed to cleanup Supabase user: {cleanup_error}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create organization",
        ) from db_error

    logger.info(
        ("POST /organisation request completed successfully - Request ID: %s, ",request_id),
        ("Organization ID: %s, Organization Name: %s, ",organization_id,body.name),
        ("Organization Slug: %s, User ID: %s, Admin Email: %s, ",body.slug,user_id,body.email),
        ("Status Code: 201")
    )

    return CreateOrganisationWithUserResponse(
        status_code=status.HTTP_201_CREATED,
        message="Organisation and user created successfully",
        data={
            "organization_id": organization_id,
            "user_id": user_id,
            "organization_name": body.name,
            "user_email": body.email,
            "role_name": "Super Admin",
            "slug": body.slug,
            "plan_type": body.plan_type,
            "max_users": body.max_users,
        },
    )


def _build_organization_update_query(
    body: OrganizationAdminUpdate,  # OrganizationAdminUpdate or OrganizationUpdate
    organization_id: str,
) -> Tuple[str, List]:
    """
    Build a dynamic UPDATE statement for public.organizations.

    • Only includes fields present in `body` (exclude_unset) and not empty/None.
    • Always sets updated_at = NOW().
    • Returns a (query, params) tuple ready for asyncpg.execute/fetchrow.

    Parameters
    ----------
    body : OrganizationAdminUpdate | OrganizationUpdate
        Validated Pydantic model with optional fields.
    organization_id : str
        Primary-key of the row to update.

    Returns
    -------
    Tuple[str, List]
        SQL text and ordered parameter list.
        If nothing to update, ("" , []) is returned.
    """

    # 1️⃣ Collect only keys the client actually sent
    payload = body.dict(exclude_unset=True, exclude_none=True)

    # 2️⃣ Strip out empty strings so "" doesn't overwrite existing data
    payload = {
        k: v for k, v in payload.items() if not (isinstance(v, str) and v.strip() == "")
    }

    if not payload:  # nothing to change
        return "", []

    update_fields: List[str] = []
    update_params: List = []
    param_count = 0

    # 3️⃣ Build SET clauses in insertion order – safe against SQL injection
    for column, value in payload.items():
        param_count += 1
        update_fields.append(f"{column} = ${param_count}")
        update_params.append(value)

    # 4️⃣ Audit column (no bind-var needed)
    update_fields.append("updated_at = NOW()")

    # 5️⃣ WHERE … id = $N
    param_count += 1
    update_params.append(organization_id)

    update_query = f"""
        UPDATE public.organizations
           SET {', '.join(update_fields)}
         WHERE id = ${param_count}
         RETURNING id, name, slug, created_at;
    """

    return update_query, update_params


async def _update_organization(db_conn, body, organization_id):
    """
    Update organization record.
    """

    update_query, update_params = _build_organization_update_query(
        body, organization_id
    )
    if not update_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to update - no valid fields supplied.",
        )
    return await db_conn.fetchrow(update_query, *update_params)


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
    db_conn=Depends(get_async_db_conn),
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
    logger.info(
        ("PUT /organisation/%s request started - Request ID: %s, ",organisation_id,request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Target Organization ID: %s",organisation_id)
    )

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organisation ID")
    logger.debug(
        ("Organization ID format validated - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Get organization details using utility function
    organization_query = build_organisation_detail_query()
    organization_data = await db_conn.fetchrow(organization_query, organisation_id)
    logger.debug(
        ("Organization data retrieved for update - Request ID: %s, ",request_id),
        ("Target Organization ID: %s, ",organisation_id),
        ("Organization found: %s",organization_data is not None)
    )

    if not organization_data:
        logger.warning(
            ("Organization not found for update - Request ID: %s, ",request_id),
            ("Target Organization ID: %s",organisation_id)
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Check permission using utility function
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="update organizations",
    )
    logger.debug(
        ("User permissions validated for organization update - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    # Update organization
    await _update_organization(db_conn, body, organisation_id)
    logger.debug(
        ("Organization updated successfully - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    logger.info(
        ("PUT /organisation/%s request completed successfully - ",organisation_id),
        ("Request ID: %s, ",request_id),
        ("Target Organization ID: %s, Status Code: 200",organisation_id)
    )

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
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
        ("DELETE /organisation/%s request started - Request ID: %s, ",organisation_id,request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Target Organization ID: %s",organisation_id)
    )

    # Validate organization ID format using utility function
    validate_uuid_format(organisation_id, "organisation ID")
    logger.debug(
        ("Organization ID format validated - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Check permission using utility function
    await require_permission(
        permission_code="organization.appscrip.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="delete organizations",
    )
    logger.debug(
        ("User permissions validated for organization deletion - Request ID: %s, ",request_id),
        ("Target Organization ID: %s",organisation_id)
    )

    logger.info(
        ("DELETE /organisation/%s request completed successfully - ",organisation_id),
        ("Request ID: %s, ",request_id),
        ("Target Organization ID: %s, Status Code: 200",organisation_id)
    )

    return OrganisationResponse(
        message=f"Delete organisation {organisation_id} API is working",
        status="success",
    )
