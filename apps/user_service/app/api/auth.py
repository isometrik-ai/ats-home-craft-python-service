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
import jwt
from typing import Any
from datetime import datetime

# Third-party imports
from fastapi import APIRouter, HTTPException, status, Depends, Body, Request

# Internal utility imports
from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions,
    extract_user_context,
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
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)
from apps.user_service.app.schemas.signup_wizard import (
    SignupWizardRequest,
    SignupWizardResponse,
)

# App instance
from apps.user_service.app.app_instance import limiter

# Shared library imports
from libs.shared_db.supabase_db.db import get_supabase_client, get_supabase_admin_client
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_utils.common_query import MEMBER_INSERT_QUERY
from libs.shared_middleware.jwt_auth import get_user_from_auth


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
        user_id,  # created_by_id
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


def _map_wizard_data_to_organization(wizard_data: SignupWizardRequest, user_id: str) -> dict:
    """
    Map signup wizard data to organization database fields.
    
    Args:
        wizard_data: Signup wizard request data
        user_id: User ID from bearer token
        
    Returns:
        dict: Mapped organization data
    """
    # Generate slug from firm name
    clean_name = wizard_data.firm_name.lower().strip()
    clean_name = "".join(c if c.isalnum() else "-" for c in clean_name)
    clean_name = "-".join(filter(None, clean_name.split("-")))
    unique_suffix = str(uuid.uuid4())[:8]
    slug = f"business-{clean_name}-{unique_suffix}"
    
    # Map firm size to max_users
    firm_size_mapping = {
        "Solo Practitioner": 1,
        "Small Firm (2-10 attorneys)": 10,
        "Mid-Size/Large Firm (11-100 attorneys)": 100,
        "Enterprise Firm (100+ attorneys)": 1000,
    }
    max_users = firm_size_mapping.get(wizard_data.firm_size.value, 10)
    
    # Handle enterprise features
    if wizard_data.enterprise_features:
        max_users = max(max_users, wizard_data.enterprise_features.expected_number_of_users)
    
    # Map practice areas to arrays
    primary_practice_areas = [area.value for area in wizard_data.primary_practice_areas]
    secondary_practice_areas = [area.value for area in wizard_data.secondary_practice_areas] if wizard_data.secondary_practice_areas else None
    specializations = [spec.value for spec in wizard_data.specializations] if wizard_data.specializations else None
    preferred_integrations = [integration.value for integration in wizard_data.preferred_integration] if wizard_data.preferred_integration else None
    
    # Map role
    role = wizard_data.team_setup.your_role.value if wizard_data.team_setup else None
    
    import json
    
    # Build settings JSON with additional wizard data
    settings = {
        "need_migration_assistance": wizard_data.need_migration_assistance,
        "need_help_importing_data": wizard_data.need_help_importing_data,
    }
    
    # Add compliance_security to settings if provided
    if wizard_data.compliance_security:
        settings["compliance_security"] = {
            "required_compliance_standards": [std.value for std in wizard_data.compliance_security.required_compliance_standards],
            "data_retention_period": wizard_data.compliance_security.data_retention_period,
            "auditing_frequency": wizard_data.compliance_security.auditing_frequency.value,
            "encryption_requirements": [req.value for req in wizard_data.compliance_security.encryption_requirements],
            "compliance_officer_email": wizard_data.compliance_security.compliance_officer_email,
            "additional_requirements": wizard_data.compliance_security.additional_requirements,
        }
    
    # Add enterprise_features to settings if provided
    if wizard_data.enterprise_features:
        settings["enterprise_features"] = {
            "expected_number_of_users": wizard_data.enterprise_features.expected_number_of_users,
            "preferred_go_live_date": wizard_data.enterprise_features.preferred_go_live_date,
            "support_service_options": [opt.value for opt in wizard_data.enterprise_features.support_service_options],
            "sla_requirements": wizard_data.enterprise_features.sla_requirements,
            "customization_options": [opt.value for opt in wizard_data.enterprise_features.customization_options],
            "custom_integration": [intg.value for intg in wizard_data.enterprise_features.custom_integration],
            "custom_reporting": [rpt.value for rpt in wizard_data.enterprise_features.custom_reporting],
            "primary_contact_information": {
                "contact_name": wizard_data.enterprise_features.primary_contact_information.contact_name,
                "contact_email": wizard_data.enterprise_features.primary_contact_information.contact_email,
                "contact_phone": wizard_data.enterprise_features.primary_contact_information.contact_phone,
            }
        }
    
    # Convert settings dict to JSON string for database storage
    settings_json = json.dumps(settings)
    
    return {
        "id": None,  # Will be set by caller
        "name": wizard_data.firm_name,
        "slug": slug,
        "domain": None,  # Not provided in wizard
        "logo_url": None,  # Not provided in wizard
        "settings": settings_json,  # JSON string for database storage
        "plan_type": "starter",  # Default plan
        "status": "active",
        "max_users": max_users,
        "timezone": wizard_data.timezone,
        "created_by_id": user_id,
        "industry": None,  # Not provided in wizard
        "company_size": wizard_data.firm_size.value,
        "description": None,  # Not provided in wizard
        "referral_source": None,  # Not provided in wizard
        "first_name": wizard_data.firstname,
        "last_name": wizard_data.lastname,
        "phone_number": wizard_data.phone_number,
        "preferred_language": wizard_data.prefred_lang,
        "firm_size": wizard_data.firm_size.value,
        "address": wizard_data.address,
        "city": wizard_data.city,
        "state": wizard_data.state,
        "zip_code": wizard_data.zip_code,
        "country": wizard_data.country,
        "primary_practice_areas": primary_practice_areas,
        "secondary_practice_areas": secondary_practice_areas,
        "specializations": specializations,
        "role": role,
        "preferred_integrations": preferred_integrations,
    }


async def _update_organization_with_wizard_data(db_conn, organization_id: str, org_data: dict) -> dict:
    """
    Update existing organization with wizard data.
    
    Args:
        db_conn: Database connection
        organization_id: Organization ID to update
        org_data: Organization data from wizard
        
    Returns:
        dict: Updated organization data
    """
    import asyncio
    import asyncpg
    
    
    # Set timeout and retry logic
    max_retries = 3
    base_timeout_seconds = 30
    
    for attempt in range(max_retries):
        try:
            # Set statement timeout for this query
            await db_conn.execute("SET LOCAL statement_timeout = '30s';")
            await db_conn.execute("SET LOCAL lock_timeout = '5s';")
            
            update_query = """
                UPDATE public.organizations SET
                    name = $2,
                    slug = $3,
                    settings = $4,
                    max_users = $5,
                    timezone = $6,
                    company_size = $7,
                    first_name = $8,
                    last_name = $9,
                    phone_number = $10,
                    preferred_language = $11,
                    firm_size = $12,
                    address = $13,
                    city = $14,
                    state = $15,
                    zip_code = $16,
                    country = $17,
                    primary_practice_areas = $18,
                    secondary_practice_areas = $19,
                    specializations = $20,
                    role = $21,
                    preferred_integrations = $22,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING id, name, slug, created_at, updated_at;
            """
            
            result = await db_conn.fetchrow(
                update_query,
                organization_id,
                org_data["name"],
                org_data["slug"],
                org_data["settings"],
                org_data["max_users"],
                org_data["timezone"],
                org_data["company_size"],
                org_data["first_name"],
                org_data["last_name"],
                org_data["phone_number"],
                org_data["preferred_language"],
                org_data["firm_size"],
                org_data["address"],
                org_data["city"],
                org_data["state"],
                org_data["zip_code"],
                org_data["country"],
                org_data["primary_practice_areas"],
                org_data["secondary_practice_areas"],
                org_data["specializations"],
                org_data["role"],
                org_data["preferred_integrations"],
            )
            
            return result
            
        except (asyncpg.PostgresError, TimeoutError) as e:
            if attempt < max_retries - 1:
                wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                await asyncio.sleep(wait_time)
            else:
                raise e


async def _create_organization_with_wizard_data(db_conn, organization_id: str, org_data: dict) -> dict:
    """
    Create new organization with wizard data.
    
    Args:
        db_conn: Database connection
        organization_id: Organization ID to create
        org_data: Organization data from wizard
        
    Returns:
        dict: Created organization data
    """
    import asyncio
    import asyncpg
    
    # Set timeout and retry logic
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # Set statement timeout for this query
            await db_conn.execute("SET LOCAL statement_timeout = '30s';")
            await db_conn.execute("SET LOCAL lock_timeout = '5s';")
            
            insert_query = """
                INSERT INTO public.organizations (
                    id, name, slug, domain, logo_url, settings, plan_type, status,
                    max_users, timezone, created_by_id, industry, company_size,
                    description, referral_source, first_name, last_name, phone_number,
                    preferred_language, firm_size, address, city, state, zip_code,
                    country, primary_practice_areas, secondary_practice_areas,
                    specializations, role, preferred_integrations, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                    $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28,
                    $29, $30, $31, NOW(), NOW()
                ) RETURNING id, name, slug, created_at, updated_at;
            """
            
            result = await db_conn.fetchrow(
                insert_query,
                organization_id,
                org_data["name"],
                org_data["slug"],
                org_data["domain"],
                org_data["logo_url"],
                org_data["settings"],
                org_data["plan_type"],
                org_data["status"],
                org_data["max_users"],
                org_data["timezone"],
                org_data["created_by_id"],
                org_data["industry"],
                org_data["company_size"],
                org_data["description"],
                org_data["referral_source"],
                org_data["first_name"],
                org_data["last_name"],
                org_data["phone_number"],
                org_data["preferred_language"],
                org_data["firm_size"],
                org_data["address"],
                org_data["city"],
                org_data["state"],
                org_data["zip_code"],
                org_data["country"],
                org_data["primary_practice_areas"],
                org_data["secondary_practice_areas"],
                org_data["specializations"],
                org_data["role"],
                org_data["preferred_integrations"],
            )
            
            return result
            
        except (asyncpg.PostgresError, TimeoutError) as e:
            if attempt < max_retries - 1:
                wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                await asyncio.sleep(wait_time)
            else:
                raise e


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


@router.post("/forgot-password", response_model=ForgotPasswordResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def forgot_password(request: Request, data: ForgotPasswordRequest, db_conn=Depends(get_async_db_conn)):
    """
    Send password reset email to user (only if email exists in system)
    
    This endpoint sends a password reset email containing a secure token. The user will receive
    an email with a link like:
    http://localhost:3000/#access_token=eyJhbGciOiJIUzI1NiIs...&expires_at=1758009136&expires_in=3600&refresh_token=4bz3ixdhgdbv&token_type=bearer&type=recovery
    
    To complete the password reset:
    1. User clicks the link in the email
    2. Frontend extracts the access_token from the URL hash
    3. Frontend calls POST /auth/reset-password with the token and new password
    
    Args:
        request (Request): FastAPI request object
        data (ForgotPasswordRequest): Email address for password reset
        db_conn: Database connection for email validation
        
    Returns:
        ForgotPasswordResponse: Success response if email exists
        
    Raises:
        HTTPException: 404 for email not found, 500 for system errors
        
    Example:
        Request:
        {
            "email": "user@example.com"
        }
        
        Response (200 OK):
        {
            "status_code": 200,
            "message": "Password reset email sent successfully. Please check your email."
        }
        
        Response (404 Not Found):
        {
            "detail": "Email not found in our system. Please check your email address and try again."
        }
    """
    logger.info("=== FORGOT PASSWORD DEBUG START ===")
    
    try:
        # First, check if email exists in auth.users table
        logger.info("Checking if email exists in auth.users...")
        auth_user_query = """
            SELECT id, email FROM auth.users
            WHERE email = $1
            LIMIT 1;
        """
        auth_user = await db_conn.fetchrow(auth_user_query, data.email)
        
        if not auth_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email not found in our system. Please check your email address and try again."
            )
                
        # Debug: Check environment variables
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
        
        # Get Supabase client
        logger.info("Getting Supabase client...")
        supabase = get_supabase_client()
        logger.info("Supabase client obtained successfully")
        
        # Send password reset email only if user exists
        supabase.auth.reset_password_email(data.email)
        logger.info("Password reset email sent successfully")
        
        return ForgotPasswordResponse(
            status_code=status.HTTP_200_OK,
            message="Password reset email sent successfully. Please check your email."
        )
        
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process password reset request. Please try again."
        ) from error
    finally:
        logger.info("=== FORGOT PASSWORD DEBUG END ===")


@router.post("/reset-password", response_model=ResetPasswordResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def reset_password(request: Request, data: ResetPasswordRequest):
    """
    Reset user password using token from email
    
    This endpoint is used to complete the password reset process. The token should be extracted
    from the password reset email URL that the user received after calling POST /auth/forgot-password.
    
    The email URL format is:
    http://localhost:3000/#access_token=eyJhbGciOiJIUzI1NiIs...&expires_at=1758009136&expires_in=3600&refresh_token=4bz3ixdhgdbv&token_type=bearer&type=recovery
    
    Frontend should extract the access_token from the URL hash and send it as the 'token' parameter.
    
    Args:
        request (Request): FastAPI request object
        data (ResetPasswordRequest): Reset token (access_token from email URL) and new password
        
    Returns:
        ResetPasswordResponse: Success response
        
    Raises:
        HTTPException: 400 for invalid token/password, 500 for other errors
        
    Example:
        Request:
        {
            "token": "eyJhbGciOiJIUzI1NiIsImtpZCI6IjllaFhpRHlFNXFGK2lwVHYiLCJ0eXAiOiJKV1QifQ...",
            "new_password": "newpassword123"
        }
        
        Response (200 OK):
        {
            "status_code": 200,
            "message": "Password reset successfully. You can now login with your new password."
        }
        
        Response (400 Bad Request):
        {
            "detail": "Invalid or expired reset token. Please request a new password reset."
        }
    """
    logger.info("=== PASSWORD RESET DEBUG START ===")
    logger.info(f"Request received for password reset")
    logger.info(f"Token length: {len(data.token) if data.token else 'None'}")
    logger.info(f"Token preview: {data.token[:50] if data.token else 'None'}...")
    logger.info(f"New password length: {len(data.new_password) if data.new_password else 'None'}")
    
    try:
        # Debug: Check environment variables
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
        supabase_service_key = os.getenv("SUPABASE_SERVICE_KEY")
        jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
        
        logger.info(f"Environment check:")
        logger.info(f"  SUPABASE_URL: {'SET' if supabase_url else 'NOT SET'}")
        logger.info(f"  SUPABASE_ANON_KEY: {'SET' if supabase_anon_key else 'NOT SET'}")
        logger.info(f"  SUPABASE_SERVICE_KEY: {'SET' if supabase_service_key else 'NOT SET'}")
        logger.info(f"  SUPABASE_JWT_SECRET: {'SET' if jwt_secret else 'NOT SET'}")
        
        # Get Supabase admin client
        logger.info("Getting Supabase admin client...")
        supabase_admin = get_supabase_admin_client()
        logger.info("Supabase admin client obtained successfully")
        
        # Method 1: Try JWT token verification approach
        logger.info("Attempting JWT token verification...")
        try:
            if not jwt_secret:
                logger.error("JWT secret not configured")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="JWT secret not configured"
                )
            
            # Decode and verify the JWT token
            logger.info("Decoding JWT token...")
            decoded_token = jwt.decode(
                data.token, 
                jwt_secret, 
                algorithms=["HS256"],
                options={"verify_exp": True}
            )
            logger.info(f"JWT token decoded successfully: {decoded_token}")
            
            # Extract user ID from the token
            user_id = decoded_token.get("sub")  # 'sub' is the user ID in JWT
            logger.info(f"Extracted user ID from token: {user_id}")
            
            if not user_id:
                logger.error("No user ID found in token")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid token: missing user ID"
                )
            
            # Update the password using admin client with the specific user ID
            logger.info(f"Updating password for user ID: {user_id}")
            result = supabase_admin.auth.admin.update_user_by_id(
                user_id,
                {"password": data.new_password}
            )
            logger.info(f"Password update result: {result}")
            
            if result.user:
                logger.info("Password updated successfully")
                return ResetPasswordResponse(
                    status_code=status.HTTP_200_OK,
                    message="Password reset successfully. You can now login with your new password."
                )
            else:
                logger.error("Password update failed - no user in result")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to update password. Please try again."
                )
                
        except jwt.ExpiredSignatureError as jwt_error:
            logger.error(f"JWT token expired: {jwt_error}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset token has expired. Please request a new password reset."
            ) from jwt_error
        except jwt.InvalidTokenError as jwt_error:
            logger.error(f"Invalid JWT token: {jwt_error}")
            logger.info("JWT verification failed, trying alternative approach...")
            
            # Method 2: Use Supabase's verify_otp method for password reset
            try:
                logger.info("Attempting Supabase verify_otp method...")
                supabase_client = get_supabase_client()
                
                # First verify the token to get user info
                logger.info("Calling verify_otp with token...")
                result = supabase_client.auth.verify_otp({
                    "token": data.token,
                    "type": "recovery"
                })
                
                logger.info(f"verify_otp result: {result}")
                
                if result.user:
                    # Now update the password using admin client
                    user_id = result.user.id
                    logger.info(f"Token verified, updating password for user: {user_id}")
                    
                    admin_result = supabase_admin.auth.admin.update_user_by_id(
                        user_id,
                        {"password": data.new_password}
                    )
                    
                    if admin_result.user:
                        logger.info("Password updated successfully via verify_otp + admin")
                        return ResetPasswordResponse(
                            status_code=status.HTTP_200_OK,
                            message="Password reset successfully. You can now login with your new password."
                        )
                    else:
                        logger.error("Admin password update failed after verify_otp")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Failed to update password. Please try again."
                        )
                else:
                    logger.error("verify_otp failed - no user in result")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid or expired reset token. Please request a new password reset."
                    )
                    
            except Exception as verify_error:
                logger.error(f"verify_otp failed: {verify_error}")
                logger.info("verify_otp failed, trying admin API approach...")
                
                # Method 3: Try to extract user info from token and use admin API
                try:
                    # Try to decode without verification to get user info
                    logger.info("Attempting to decode token without verification...")
                    unverified_token = jwt.decode(
                        data.token, 
                        options={"verify_signature": False}
                    )
                    logger.info(f"Unverified token content: {unverified_token}")
                    
                    user_id = unverified_token.get("sub")
                    email = unverified_token.get("email")
                    
                    if user_id:
                        logger.info(f"Found user ID in unverified token: {user_id}")
                        
                        # Update password using admin client
                        logger.info(f"Updating password for user ID: {user_id}")
                        result = supabase_admin.auth.admin.update_user_by_id(
                            user_id,
                            {"password": data.new_password}
                        )
                        logger.info(f"Password update result: {result}")
                        
                        if result.user:
                            logger.info("Password updated successfully via admin API")
                            return ResetPasswordResponse(
                                status_code=status.HTTP_200_OK,
                                message="Password reset successfully. You can now login with your new password."
                            )
                        else:
                            logger.error("Admin API password update failed")
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Failed to update password. Please try again."
                            )
                    elif email:
                        # Try using email with verify_otp
                        logger.info(f"Found email in token, trying verify_otp with email: {email}")
                        try:
                            result = supabase_client.auth.verify_otp({
                                "token": data.token,
                                "type": "recovery"
                            })
                            
                            if result.user:
                                # Update password using admin client
                                admin_result = supabase_admin.auth.admin.update_user_by_id(
                                    result.user.id,
                                    {"password": data.new_password}
                                )
                                
                                if admin_result.user:
                                    logger.info("Password updated successfully via email verify_otp")
                                    return ResetPasswordResponse(
                                        status_code=status.HTTP_200_OK,
                                        message="Password reset successfully. You can now login with your new password."
                                    )
                        except (ValueError, TypeError, ConnectionError) as email_verify_error:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid reset token. Please request a new password reset."
                            ) from email_verify_error
                    else:
                        logger.error("No user ID or email found in unverified token")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid reset token. Please request a new password reset."
                        )
                        
                except Exception as decode_error:
                    logger.error(f"Token decoding failed: {decode_error}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid or expired reset token. Please request a new password reset."
                    ) from verify_error
        
    except Exception as error:
    
        
        if isinstance(error, HTTPException):
            raise error
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password. Please try again."
        ) from error
    finally:
        logger.info("=== PASSWORD RESET DEBUG END ===")


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
async def signup(
    request: Request,  # pylint: disable=unused-argument
    signup_data: SignupRequest = Body(...),
    db_conn=Depends(get_async_db_conn),
    supabase=Depends(get_supabase_client),
    admin_supabase=Depends(get_supabase_admin_client),  # Add this dependency
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
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "POST /auth/signup request started - Request ID: %s, "
        "Email: %s, "
        "Account Type: %s, "
        "Plan Type: %s",
        request_id,
        signup_data.user_data.email,
        signup_data.account_type.value,
        signup_data.plan_type.value
    )
 
    # Set audit context for user signup
    request.state.audit_table = "organizations"
    request.state.audit_description = "New user signup: %s with account type: %s" % (signup_data.user_data.email, signup_data.account_type.value)
    request.state.audit_risk_level = "medium"
 
    # Set audit user context for signup (required by audit decorator)
    # For signup, we use the email from signup data and generate a temporary context
    request.state.audit_user_context = {
        "organization_id": None,  # Will be set after organization creation
        "user_id": None,  # Will be set after user creation
        "user_email": signup_data.user_data.email,
        "user_type": "signup_user",
    }
 
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
    request.state.audit_user_context.update(
        {"user_id": user_id, "organization_id": organization_id}
    )
 
    # Create organization, role, permissions, and member in database transaction
    try:
        # async with db_conn.transaction():
        # Create organization with permissions
        org_data = {
            "organization_id": organization_id,
            "organization_name": organization_name,
            "slug": slug,
            "user_id": user_id,
        }
        org_result, super_admin_role_id = (
            await _create_organization_with_permissions_for_signup(
                db_conn,
                signup_data,
                org_data,
            )
        )

        # Create organization member
        member_result = await _create_organization_member(
            db_conn, user_id, organization_id, super_admin_role_id, signup_data
        )

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
    request.state.raw_audit_new_data = {
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
        "company_website": (
            signup_data.company_data.company_website
            if signup_data.company_data
            else None
        ),
        "company_industry": (
            signup_data.company_data.industry if signup_data.company_data else None
        ),
        "company_size": (
            signup_data.company_data.company_size if signup_data.company_data else None
        ),
        "signup_timestamp": datetime.now().isoformat(),
        "signup_method": "email_password",
        "super_admin_role_created": True,
        "default_permissions_created": True,
        "audit_user_context": {
            "organization_id": organization_id,
            "user_id": user_id,
            "user_email": signup_data.user_data.email,
        },
    }
 
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
        data={
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
        },
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


@handle_api_exceptions("signup wizard")
@router.post(
    "/signup-wizard", response_model=SignupWizardResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("50/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Signup wizard involves personal information
        "pii",  # Contains personally identifiable information
        "audit_required",  # Must be logged for compliance and security audits
    ],
    table_name="organizations",
    category="SIGNUP_WIZARD",
)
async def signup_wizard(
    request: Request,
    wizard_data: SignupWizardRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Signup wizard endpoint for updating organization with detailed firm information.
    
    This endpoint:
    1. Validates bearer token and extracts user information
    2. Checks if organization exists with user's created_by_id
    3. Updates existing organization or creates new one with wizard data
    4. Maps wizard fields to database structure
    
    Args:
        wizard_data (SignupWizardRequest): Complete signup wizard data
        current_user (dict): Authenticated user from bearer token
        db_conn: Database connection
        
    Returns:
        SignupWizardResponse: Success response with organization data
        
    Raises:
        HTTPException: 401 for authentication errors
        HTTPException: 400 for validation errors
        HTTPException: 500 for database errors
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        "POST /auth/signup-wizard request started - Request ID: %s, "
        "Firm Name: %s, Firm Size: %s, Contact: %s %s",
        request_id,
        wizard_data.firm_name,
        wizard_data.firm_size.value,
        wizard_data.firstname,
        wizard_data.lastname
    )
    
    # Extract user context from JWT token
    user_context = extract_user_context(current_user)
    user_id = user_context.user_id
    
    # Set audit context
    request.state.audit_table = "organizations"
    request.state.audit_description = "Signup wizard organization update: %s (%s)" % (
        wizard_data.firm_name, 
        wizard_data.firm_size.value
    )
    request.state.audit_risk_level = "medium"
    
    # Set audit user context
    request.state.audit_user_context = {
        "organization_id": user_context.organization_id,
        "user_id": user_id,
        "user_email": user_context.email,
        "user_type": "organization_member",
    }
    
    # Check if organization exists with this user's created_by_id
    existing_org_query = """
        SELECT id, name, slug FROM public.organizations 
        WHERE created_by_id = $1
        LIMIT 1;
    """
    existing_org = await db_conn.fetchrow(existing_org_query, user_id)
    
    # Map wizard data to database fields
    org_data = _map_wizard_data_to_organization(wizard_data, user_id)
    
    if existing_org:
        # Update existing organization
        organization_id = existing_org["id"]
        updated_org = await _update_organization_with_wizard_data(
            db_conn, organization_id, org_data
        )
        action = "updated"
    else:
        # Create new organization
        organization_id = str(uuid.uuid4())
        updated_org = await _create_organization_with_wizard_data(
            db_conn, organization_id, org_data
        )
        action = "created"
    
    # Set audit data for successful operation
    request.state.raw_audit_new_data = {
        "request_id": request_id,
        "organization_id": organization_id,
        "action": action,
        "firm_name": wizard_data.firm_name,
        "firm_size": wizard_data.firm_size.value,
        "contact_name": f"{wizard_data.firstname} {wizard_data.lastname}",
        "country": wizard_data.country,
        "primary_practice_areas": [area.value for area in wizard_data.primary_practice_areas],
        "secondary_practice_areas": [area.value for area in wizard_data.secondary_practice_areas] if wizard_data.secondary_practice_areas else None,
        "specializations": [spec.value for spec in wizard_data.specializations] if wizard_data.specializations else None,
        "has_enterprise_features": wizard_data.enterprise_features is not None,
        "operation_timestamp": datetime.now().isoformat(),
        "audit_user_context": {
            "organization_id": organization_id,
            "user_id": user_id,
            "user_email": user_context.email,
        },
    }
    
    logger.info(
        "POST /auth/signup-wizard request completed successfully - Request ID: %s, "
        "Organization ID: %s, Action: %s, Status Code: 200",
        request_id,
        organization_id,
        action
    )
    
    return SignupWizardResponse(
        status_code=status.HTTP_200_OK,
        message=f"Organization {action} successfully with signup wizard data",
        data={},
        validation_passed=True
    )
