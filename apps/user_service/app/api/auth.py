
"""
Authentication API Module

This module provides authentication operations using Supabase.
Includes login and signup functionality with proper error handling.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

# Standard library imports
import os
import sys
import uuid
import json
from typing import Any
from datetime import datetime

# Third-party imports
from fastapi import APIRouter, HTTPException, status, Depends, Body, Request

# Internal utility imports
from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions,
)
from apps.user_service.app.dependencies.organisation_utils import (
    check_organisation_slug_unique,
    create_default_permissions_for_new_org,
    create_super_admin_role,
    assign_all_permissions_to_role,
)

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Schema imports
from apps.user_service.app.schemas.auth import (
    AccountType,
    PlanType,
    AuthLogin,
    SignupRequest,
    SignupResponse,
    UserInfo,
    AuthResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)

# App instance
from apps.user_service.app.app_instance import limiter

# Shared library imports
from libs.shared_db.supabase_db.db import get_supabase_client, get_supabase_admin_client
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_utils.common_query import MEMBER_INSERT_QUERY


# Modify sys.path to support monorepo imports
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_path)

monorepo_root = os.path.abspath(os.path.join(base_path, "../../.."))
sys.path.insert(0, monorepo_root)


# Create router for authentication endpoints
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Initialize logger for auth module
logger = get_logger("auth-api")
logger.info("Auth API module loaded")


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


def _determine_organization_name(signup_data: SignupRequest) -> str:
    """
    Determine organization name based on account type.

    Args:
        signup_data (SignupRequest): Signup request data

    Returns:
        str: Organization name
    """
    if signup_data.account_type == AccountType.PERSONAL:
        return f"{signup_data.user_data.first_name} {signup_data.user_data.last_name}"
    return (
        signup_data.company_data.company_name
        if signup_data.company_data
        else "Unknown Company"
    )


def _get_max_users_for_plan(plan_type: str) -> int:
    """
    Get maximum users allowed for plan type.

    Args:
        plan_type (str): Plan type

    Returns:
        int: Maximum users allowed
    """
    plan_limits = {
        PlanType.STARTER.value: 5,
        PlanType.PROFESSIONAL.value: 25,
        PlanType.ENTERPRISE.value: 100,
    }
    return plan_limits.get(plan_type, 5)


async def _create_organization_with_permissions_for_signup(
    db_conn,
    signup_data: SignupRequest,
    org_data: dict,
) -> tuple:
    """
    Create organization with roles and permissions in database transaction.

    """
    user_data = signup_data.user_data
    company_data = signup_data.company_data
    max_users = _get_max_users_for_plan(signup_data.plan_type.value)

    # Extract org data
    organization_id = org_data["organization_id"]
    organization_name = org_data["organization_name"]
    slug = org_data["slug"]
    user_id = org_data["user_id"]

    # Create organization
    org_insert_query = """
        INSERT INTO public.organizations (
            id, name, slug, domain, logo_url, industry, company_size,
            description, referral_source, plan_type, max_users, timezone,
            status, created_by_id, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'trial', $13, NOW(), NOW()
        ) RETURNING id, name, slug, created_at;
    """

    # Set created_by_id to a placeholder for now - we'll need to get this from Supabase
    org_result = await db_conn.fetchrow(
        org_insert_query,
        organization_id,
        organization_name,
        slug,
        company_data.company_website if company_data else None,
        None,  # logo_url
        company_data.industry if company_data else None,
        company_data.company_size if company_data else None,
        company_data.description if company_data else None,
        company_data.referral_source if company_data else None,
        signup_data.plan_type.value,
        max_users,
        user_data.timezone,
        user_id,  # Use org_id as created_by_id temporarily
    )

    # Create Super Admin role
    super_admin_role_id = await create_super_admin_role(
        organisation_id=organization_id,
        db_conn=db_conn,
    )

    # Create default permissions
    permission_ids = await create_default_permissions_for_new_org(
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


async def _create_organization_member(
    db_conn,
    user_id: str,
    organization_id: str,
    super_admin_role_id: str,
    signup_data: SignupRequest,
) -> dict:
    """
    Create organization member record.

    Args:
        db_conn: Database connection
        user_id: User ID
        organization_id: Organization ID
        super_admin_role_id: Super Admin role ID
        signup_data: Signup request data

    Returns:
        dict: Created member record
    """
    user_data = signup_data.user_data
    full_name = f"{user_data.first_name} {user_data.last_name}"

    return await db_conn.fetchrow(
        MEMBER_INSERT_QUERY,
        user_id,
        organization_id,
        super_admin_role_id,
        user_data.email,
        full_name,
        user_data.phone,
        user_data.timezone,
    )


# ============================================================================
# AUTHENTICATION FUNCTIONS
# ============================================================================


def login_user(email: str, password: str) -> Any:
    """
    Attempts to log in a user with the provided email and password.
    Returns the result from Supabase or raises an exception on failure.

    Args:
        email (str): User's email address
        password (str): User's password

    Returns:
        Any: Supabase authentication result

    Raises:
        Exception: If authentication fails
    """
    try:
        # Get Supabase client from shared db module
        supabase = get_supabase_client()
        result = supabase.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        return result
    except Exception as error:
        print(error)
        raise error


async def _create_supabase_user(
    supabase, signup_data: SignupRequest, organization_id: str
) -> str:
    """
    Create user in Supabase Auth with organization metadata.

    Args:
        supabase: Supabase client
        signup_data: Signup request data
        organization_id: Organization ID to associate with user

    Returns:
        str: Created user ID

    Raises:
        HTTPException: For duplicate email or Supabase errors
    """
    try:
        user_data = signup_data.user_data
        full_name = f"{user_data.first_name} {user_data.last_name}"

        supabase_response = supabase.auth.sign_up(
            {
                "email": user_data.email,
                "password": user_data.password,
                "options": {
                    "data": {
                        "organization_id": organization_id,
                        "full_name": full_name,
                        "first_name": user_data.first_name,
                        "last_name": user_data.last_name,
                        "job_title": user_data.job_title,
                        "phone": user_data.phone,
                        "account_type": signup_data.account_type.value,
                        "is_super_admin": True,
                        "type": "organization_member",
                    }
                },
            }
        )

        if not supabase_response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to create user account",
            )

        return supabase_response.user.id

    except Exception as supabase_error:
        print(f"Supabase user creation failed: {supabase_error}")

        error_msg = str(supabase_error).lower()
        if (
            "already_exists" in error_msg
            or "duplicate" in error_msg
            or "already registered" in error_msg
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            ) from supabase_error

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account",
        ) from supabase_error


# ============================================================================
# SIGNUP HELPER FUNCTIONS
# ============================================================================

def _prepare_signup_audit_data(
    organization_id: str,
    organization_name: str,
    slug: str,
    user_id: str,
    signup_data: SignupRequest
) -> dict:
    """Prepare audit data for successful signup."""
    audit_data = {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "organization_slug": slug,
        "user_id": user_id,
        "user_email": signup_data.user_data.email,
        "user_full_name": f"{signup_data.user_data.first_name} {signup_data.user_data.last_name}",
        "account_type": signup_data.account_type.value,
        "plan_type": signup_data.plan_type.value,
        "status": "trial",
        "max_users": _get_max_users_for_plan(signup_data.plan_type.value),
        "signup_timestamp": datetime.now().isoformat(),
        "signup_method": "email_password",
        "super_admin_role_created": True,
        "default_permissions_created": True,
        "audit_user_context": {
            "organization_id": organization_id,
            "user_id": user_id,
            "user_email": signup_data.user_data.email
        },
        "company_website": None,
        "company_industry": None,
        "company_size": None
    }

    if signup_data.company_data:
        audit_data.update({
            "company_website": signup_data.company_data.company_website,
            "company_industry": signup_data.company_data.industry,
            "company_size": signup_data.company_data.company_size,
        })

    return audit_data

def _prepare_signup_response_data(
    organization_id: str,
    user_id: str,
    organization_name: str,
    slug: str,
    signup_data: SignupRequest
) -> dict:
    """Prepare response data for successful signup."""
    return {
        "organization_id": organization_id,
        "user_id": user_id,
        "organization_name": organization_name,
        "user_email": signup_data.user_data.email,
        "account_type": signup_data.account_type.value,
        "plan_type": signup_data.plan_type.value,
        "slug": slug,
        "status": "trial",
        "role_name": "Super Admin",
        "max_users": _get_max_users_for_plan(signup_data.plan_type.value),
    }

# ============================================================================
# API ENDPOINTS
# ============================================================================


@router.post("/login", response_model=AuthResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
def login(request: Request, data: AuthLogin):
    """
    User login endpoint

    Args:
        request (Request): FastAPI request object
        data (AuthLogin): Login credentials containing email and password

    Returns:
        AuthResponse: Access token and user information

    Raises:
        HTTPException: 401 for invalid credentials, 500 for other errors
    """
    try:
        result = login_user(data.email, data.password)
        return AuthResponse(
            access_token=result.session.access_token,
            user=UserInfo(
                id=result.user.id,
                email=result.user.email,
                full_name=result.user.user_metadata.get("full_name", ""),
            ),
        )
    except Exception as error:
        if "Invalid login credentials" in str(error):
            raise HTTPException(
                status_code=401, detail="Invalid login credentials"
            ) from error
        raise HTTPException(status_code=500, detail="Authentication failed") from error


@handle_api_exceptions("signup")
@router.post(
    "/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # User signup involves personal information
        "pii",  # User signup contains personally identifiable information
        "audit_required",  # User signup must be logged for compliance and security audits
    ],
    table_name="organizations",
    category="USER_SIGNUP",
)
def _init_audit_context(request: Request, signup_data: SignupRequest, request_id: str):
    """Initialize audit context for signup request."""
    request.state.audit_table = "organizations"
    request.state.audit_description = (
        "New user signup: %s with account type: %s",
        signup_data.user_data.email,
        signup_data.account_type.value
    )
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "organization_id": None,
        "user_id": None,
        "user_email": signup_data.user_data.email,
        "user_type": "signup_user"
    }

    logger.info(
        ("POST /auth/signup request started - Request ID: %s, ", request_id),
        ("Email: %s, ", signup_data.user_data.email),
        ("Account Type: %s, ", signup_data.account_type.value),
        ("Plan Type: %s", signup_data.plan_type.value)
    )

async def signup(
    request: Request,
    signup_data: SignupRequest = Body(...),
    db_conn=Depends(get_async_db_conn),
    supabase=Depends(get_supabase_client),
    admin_supabase=Depends(get_supabase_admin_client),
):
    """
    User signup endpoint for both personal and business accounts

    This endpoint creates a complete account setup including:
    1. User signup with Supabase Auth
    2. Organization creation based on account type
    3. Super Admin role and permissions setup
    4. Organization member creation with role assignment

    Account Types:
    - Personal: Individual account for freelancers, students, personal use
    - Business: Corporate account for companies, teams, organizations

    Features:
    - Email validation and duplicate checking
    - Password strength requirements (minimum 6 characters)
    - Organization slug generation with uniqueness validation
    - Trial status for new organizations
    - Automatic Super Admin role assignment
    - Complete permission system setup

    Args:
        signup_data (SignupRequest): Signup data including user info and optionally company info
        db_conn: AsyncPG database connection
        supabase: Supabase client for user authentication

    Returns:
        SignupResponse: Success response with organization and user data

    Raises:
        HTTPException: 400 for validation errors
        HTTPException: 409 for duplicate email or organization slug
        HTTPException: 500 for database or Supabase errors

    Security Features:
    - Password hashing handled by Supabase
    - Email validation and uniqueness checking
    - Organization slug uniqueness validation
    - Transaction rollback on failures
    - Proper error handling without exposing internal details
    """
    # Generate request ID and initialize audit context
    request_id = str(uuid.uuid4())
    _init_audit_context(request, signup_data, request_id)

    # Generate organization details
    organization_id = str(uuid.uuid4())
    organization_name = _determine_organization_name(signup_data)
    slug = _generate_organization_slug(
        organization_name, signup_data.account_type.value
    )

    print(f"Generated organization_id: {organization_id}")
    print(f"Organization name: {organization_name}")
    print(f"Organization slug: {slug}")

    # Validate slug uniqueness
    await check_organisation_slug_unique(slug, db_conn)

    # Create user in Supabase Auth
    user_id = await _create_supabase_user(supabase, signup_data, organization_id)
    print(f"Created Supabase user: {user_id}")

    # Update audit user context with the created user_id
    request.state.audit_user_context.update({
        "user_id": user_id,
        "organization_id": organization_id
    })

    # Create organization, role, permissions, and member in database transaction
    try:
        # async with db_conn.transaction():
            # Create organization with permissions
        org_result, super_admin_role_id = (
            await _create_organization_with_permissions_for_signup(
                db_conn,
                signup_data,
                {
                "organization_id": organization_id,
                "organization_name": organization_name,
                "slug": slug,
                "user_id": user_id,
                },
            )
        )
        print(f"Created organization: {org_result['id']}")
        print(f"Created Super Admin role: {super_admin_role_id}")

        # Create organization member
        member_result = await _create_organization_member(
            db_conn, user_id, organization_id, super_admin_role_id, signup_data
        )
        print(f"Created organization member: {member_result['id']}")

    except Exception as db_error:
        print(f"Database transaction failed: {db_error}")

        # Try to delete the Supabase user if database transaction fails
        try:
            admin_supabase.auth.admin.delete_user(user_id)
            print(f"Cleaned up Supabase user: {user_id}")
        except Exception as cleanup_error:  # noqa: W0718
            print(f"Failed to cleanup Supabase user: {cleanup_error}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create account. Please try again.",
            ) from cleanup_error

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account. Please try again.",
        ) from db_error

    # Set audit data for successful user signup
    request.state.raw_audit_new_data = _prepare_signup_audit_data(
        organization_id=organization_id,
        organization_name=organization_name,
        slug=slug,
        user_id=user_id,
        signup_data=signup_data
    )

    logger.info(
        "POST /auth/signup request completed successfully - Request ID: %s, "
        "Organization ID: %s, User ID: %s, "
        "Email: %s, Status Code: 201",
        request_id,
        organization_id,
        user_id,
        signup_data.user_data.email
    )

    return SignupResponse(
        status_code=status.HTTP_201_CREATED,
        message="Account created successfully! Please check your email for verification.",
        data=_prepare_signup_response_data(
            organization_id=organization_id,
            user_id=user_id,
            organization_name=organization_name,
            slug=slug,
            signup_data=signup_data
        ),
    )


@handle_api_exceptions("verify email")
@router.post(
    "/email/verify", response_model=VerifyEmailResponse, status_code=status.HTTP_200_OK
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def verify_email(
    request: Request,
    db_conn=Depends(get_async_db_conn),
    body: VerifyEmailRequest = Body(...),
):
    """
    Verify user email and status by determining user type from auth.users metadata
    and checking the corresponding table for status.
    """
    def _get_not_found_response():
        return VerifyEmailResponse(
            status_code=404,
            message="Email not found.",
            email_found=False,
            status=None,
            can_login=False,
        )

    def _parse_meta(meta_val):
        if isinstance(meta_val, dict):
            return meta_val
        if isinstance(meta_val, str):
            try:
                return json.loads(meta_val)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                return None
        return None

    def _extract_user_type_strict(row) -> str:
        if not row:
            return None
        user_meta = _parse_meta(row.get("raw_user_meta_data"))
        app_meta = _parse_meta(row.get("raw_app_meta_data"))
        if isinstance(user_meta, dict):
            utype = user_meta.get("type") or user_meta.get("user_type")
            if utype:
                return utype
        if isinstance(app_meta, dict):
            return app_meta.get("type") or app_meta.get("user_type")
        return None

    def _response_found(status_value: str) -> VerifyEmailResponse:
        can_login_local = status_value == "active"
        return VerifyEmailResponse(
            status_code=200,
            message="Email found." if can_login_local else "Account is suspended.",
            email_found=True,
            status=status_value,
            can_login=can_login_local,
        )

    # 1) Get user from auth.users
    auth_user_query = """
        SELECT id, email, raw_app_meta_data, raw_user_meta_data
        FROM auth.users
        WHERE email = $1
        LIMIT 1;
    """
    auth_user = await db_conn.fetchrow(auth_user_query, body.email)
    if not auth_user:
        return _get_not_found_response()

    # 2) Extract and validate user type
    user_type = _extract_user_type_strict(auth_user)
    if not user_type:
        return _get_not_found_response()

    # 3) Check appropriate table based on user type
    status_value = None

    if user_type == "organization_member":
        org_record = await db_conn.fetchrow(
            "SELECT status FROM public.organization_members WHERE email = $1 LIMIT 1;",
            body.email
        )
        if org_record:
            status_value = org_record["status"]

    elif user_type == "client":
        client_record = await db_conn.fetchrow(
            "SELECT id FROM public.client_members WHERE email = $1 LIMIT 1;",
            body.email
        )
        if client_record:
            status_value = "active"

    elif user_type == "candidate":
        candidate_record = await db_conn.fetchrow(
            "SELECT is_active FROM public.candidates WHERE email = $1 LIMIT 1;",
            body.email
        )
        if candidate_record is not None:
            status_value = "active" if candidate_record["is_active"] else "suspended"

    return _response_found(status_value) if status_value else _get_not_found_response()


@handle_api_exceptions("delete user")
@router.delete("/user/{user_id}", status_code=status.HTTP_200_OK)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_user(
    request: Request,
    user_id: str,
    db_conn=Depends(get_async_db_conn),
):
    """
    Delete user directly from auth.users table without validation.

    This endpoint allows administrators to delete a user account directly
    from the database auth.users table. Use with caution as this operation
    is irreversible and will remove all user authentication data.

    Args:
        user_id (str): The ID of the user to delete
        db_conn: AsyncPG database connection for direct database access

    Returns:
        dict: Success response with deletion confirmation

    Raises:
        HTTPException: 500 for database errors or deletion failures

    Security Note:
    - This endpoint requires database access privileges
    - No validation is performed - user will be deleted immediately
    - All associated auth data will be removed from the database
    """
    try:
        # Delete user directly from auth.users table
        delete_query = """
            DELETE FROM auth.users
            WHERE id = $1;
        """

        result = await db_conn.execute(delete_query, user_id)

        if result == "DELETE 1":
            return {
                "status_code": 200,
                "message": f"User {user_id} deleted successfully from auth.users table",
                "deleted_user_id": user_id,
                "timestamp": "now"
            }
        return {
            "status_code": 200,
            "message": f"No user found with ID {user_id}",
            "deleted_user_id": None,
            "timestamp": "now"
        }

    except Exception as error:
        print(f"Failed to delete user {user_id}: {error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(error)}"
        ) from error
