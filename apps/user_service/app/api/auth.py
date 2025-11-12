"""
Authentication API Module

This module provides authentication operations using Supabase.
Includes login and signup functionality with proper error handling.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

# Standard library imports
import re
import os
import sys
import jwt
from datetime import datetime

# Third-party imports
from fastapi import APIRouter, HTTPException, status, Body, Request, Depends, Response

# Internal utility imports
from apps.user_service.app.dependencies.common_utils import (
    handle_api_exceptions
)

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Schema imports
from apps.user_service.app.schemas.auth import (
    AuthLogin,
    SignupRequest,
    UserInfo,
    AuthResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
    ResetPasswordRequest,
    PasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    SetPasswordRequest,
    PASSWORD_CONDITION_MESSAGE_EXTENDED

)

# App instance
from apps.user_service.app.app_instance import limiter

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_token, get_user_from_auth

# Email utilities
from libs.shared_utils.email_utils import send_password_reset_confirmation_email, send_welcome_email

from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
    get_organization_member_status_by_email
)
from libs.shared_db.supabase_db.admin_operations.user import (
    delete_auth_user,
    update_password_with_link_identity
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    login_user,
    sign_up_supabase_user,
    reset_the_password_email,
    update_password_with_token,
    log_exception,
    supabase_user_oauth,
    get_oauth_link_url,
    refresh_session
)
from libs.shared_db.supabase_db.admin_operations.session import get_session_by_id_admin
from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    get_verification_code_by_id,
)

# Modify sys.path to support monorepo imports
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, base_path)

monorepo_root = os.path.abspath(os.path.join(base_path, "../../.."))
sys.path.insert(0, monorepo_root)


# Create router for authentication endpoints
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Initialize logger for auth module
logger = get_logger("auth-api")


# ============================================================================
# SIGNUP HELPER FUNCTIONS
# ============================================================================

async def _validate_verification_code_for_signup(
    verification_id: str,
    email: str,
    verification_code: str
) -> None:
    """
    Validate verification code for signup (cross-security check).

    Args:
        verification_id: Verification code ID
        email: Email to validate
        verification_code: Verification code to validate

    Raises:
        HTTPException: If validation fails
    """
    verification_record = await get_verification_code_by_id(verification_id)

    if not verification_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification code not found"
        )

    if not verification_record.get("verified", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code must be verified before signup. Please verify your email first."
        )

    stored_given_input = verification_record.get("given_input")
    if stored_given_input != email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email '{email}' does not match the verification record. Expected: '{stored_given_input}'"
        )

    stored_code = verification_record.get("verification_code")
    if verification_code != stored_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code"
        )


def _extract_session(session):
    """
    Extract session object if available.

    Args:
        session: Session object

    Returns:
        Session object if available, None otherwise
    """
    if session and hasattr(session, 'access_token'):
        return session
    return None


async def _get_session_after_signup(
    signup_result,
    email: str,
    password: str
):
    """
    Get session after signup, trying signup session first, then login if needed.

    Args:
        signup_result: Result from sign_up_supabase_user
        email: User email
        password: User password

    Returns:
        Session object if available, None otherwise
    """
    session = _extract_session(signup_result.session)
    if session:
        return session

    try:
        login_result = await login_user(email, password)
        return _extract_session(login_result.session)
    except Exception as login_error:
        logger.warning("Could not get session after signup for %s: %s", email, str(login_error))

    return None


def _send_welcome_email_safely(email: str, first_name: str) -> None:
    """
    Send welcome email safely without failing the signup operation.

    Args:
        email: User email
        first_name: User first name
    """
    try:
        email_sent = send_welcome_email(email=email, first_name=first_name)
        if not email_sent:
            logger.warning("Failed to send welcome email to %s", email)
    except Exception as email_error:
        logger.error("Error sending welcome email: %s", str(email_error))


def _validate_password_strength(password: str) -> None:
    """
    Validate password strength and raise exception if weak.

    Args:
        password: Password to validate

    Raises:
        HTTPException: If password is weak
    """
    if not _is_password_strong(password):
        raise HTTPException(
            status_code=400,
            detail=PASSWORD_CONDITION_MESSAGE_EXTENDED
        )




def _is_password_strong(password: str) -> bool:
    """
    Check if password is strong.
    Checks This Conditions:
    1. At least 6 characters
    2. At least one uppercase letter
    3. At least one lowercase letter
    4. At least one number
    5. At least one special character

    Args:
        password (str): Password to check

    Returns:
        bool: True if password is strong, False otherwise
    """
    password_pattern = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{6,}$')

    return bool(password_pattern.match(password))


# ============================================================================
# API ENDPOINTS
# ============================================================================


@router.post("/login", response_model=AuthResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def login(request: Request, data: AuthLogin):
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
        result = await login_user(data.email, data.password)
        return AuthResponse(
            access_token=result.session.access_token,
            refresh_token=result.session.refresh_token,
            expires_in=result.session.expires_in,
            expires_at=result.session.expires_at,
            user=UserInfo(
                id=result.user.id,
                email=result.user.email,
                first_name=result.user.user_metadata.get("first_name", None),
                last_name=result.user.user_metadata.get("last_name", None),
                timezone=result.user.user_metadata.get("timezone", None),
            ),
        )
    except HTTPException:
        raise
    except Exception as error:
        log_exception()
        if "Invalid login credentials" in str(error):
            raise HTTPException(
                status_code=401, detail="Invalid login credentials"
            ) from error
        raise HTTPException(status_code=500, detail="Authentication failed"+str(error)) from error


@router.put("/refresh",response_model=AuthResponse,status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def refresh(request: Request):
    """
    Refresh user session
    """
    try:
        access_token = request.headers.get("Access-Token").strip()
        refresh_token = request.headers.get("Refresh-Token",None).strip()
        try:
            decoded = jwt.decode(access_token,os.getenv("SUPABASE_JWT_SECRET"),algorithms=["HS256"],audience="authenticated")
            if datetime.fromtimestamp(decoded.get("exp")) >= datetime.now():
                raise HTTPException(status_code=400, detail="Token is not expired")
        except jwt.ExpiredSignatureError:
            res = await refresh_session(refresh_token)
        res = await refresh_session(refresh_token)
        # print("\n\nres:\n", res,end="\n\n")
        return AuthResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            expires_in=res.session.expires_in,
            expires_at=res.session.expires_at,
            user=UserInfo(
                id=res.user.id,
                email=res.user.email,
                first_name=res.user.user_metadata.get("first_name", None),
                last_name=res.user.user_metadata.get("last_name", None),
                timezone=res.user.user_metadata.get("timezone", None),
            )
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        log_exception()
        raise HTTPException(status_code=500, detail="Authentication failed "+str(error)) from error


@router.post(
    "/set-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PasswordResponse,
    description="Set password for user Signed Up from Google or Magic Link.")
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def set_password(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    data: SetPasswordRequest = Body(...)):
    """
    Set password for user Signed Up from Google or Magic Link.
    """
    try:
        if not _is_password_strong(data.password):
            raise HTTPException(
                status_code=400,
                detail=PASSWORD_CONDITION_MESSAGE_EXTENDED
            )
        result = await update_password_with_link_identity(current_user['sub'], data.password)
        if result:
            return PasswordResponse(
                message="Password set successfully"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set password"
        )
    except HTTPException as error:
        raise error
    except Exception:
        log_exception()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set password")


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK
)
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def forgot_password(request: Request, data: ForgotPasswordRequest):
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
            "detail": "Email not found in our system. Please check your Inbox and try again."
        }
    """

    try:
        # First, check if email exists in auth.users table
        user = await get_auth_user_by_email(data.email)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Email not found in our system.Please check your email and try again."
            )

        # Send password reset email only if user exists
        await reset_the_password_email(data.email)
        return ForgotPasswordResponse(
            message="Password reset email sent successfully. Please check your email."
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process password reset request. Please try again."
        ) from error


@router.post("/reset-password", response_model=PasswordResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def reset_password(
    request: Request,
    data: ResetPasswordRequest):
    """
    Reset user password using token from email

    This endpoint is used to complete the password reset process.
    The token should be extracted from the password reset email URL
    that the user received after calling POST /auth/forgot-password.

    The email URL format is:
    http://localhost:3000/#access_token=eyJhbGciOiJIUzI1NiIs...&expires_at=1758009136&expires_in=3600&refresh_token=4bz3ixdhgdbv&token_type=bearer&type=recovery

    Frontend should extract the access_token from the URL hash and send it as the 'token' parameter.

    Args:
        request (Request): FastAPI request object
        data (ResetPasswordRequest): Reset token (access_token from email URL) and new password

    Returns:
        PasswordResponse: Success response

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

    try:
        user = get_user_from_token(data.token)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        if not _is_password_strong(data.new_password):
            raise HTTPException(
                status_code=400,
                detail=PASSWORD_CONDITION_MESSAGE_EXTENDED
            )

        result = await update_password_with_token(user['sub'], data.new_password)
        if result.user:

            # Send confirmation email to user
            user_email = user.get('email', '')
            user_name = user.get(
                'user_metadata', {}).get(
                'full_name', '') or user.get('email', '').split('@')[0]

            try:
                email_sent = send_password_reset_confirmation_email(user_email, user_name)
                if not email_sent:
                    logger.warning("Failed to send password reset confirmation email to %s",
                        user_email)
                    # Note: We don't fail the entire operation if email fails
                    # The password reset was successful, only the email notification failed
            except Exception as email_error:
                logger.error("Error sending password reset confirmation email:%s", str(email_error))
                # Note: We don't fail the entire operation if email fails

            return PasswordResponse(
                message="Password reset successfully. You can now login with your new password."
            )
        logger.error("Password update failed - no user in result")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to update password. Please try again."
        )

    except Exception as error:
        log_exception()
        if isinstance(error, HTTPException):
            raise error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password. Please try again."
        ) from error

    # 2. Organization creation based on account type
    # 3. Super Admin role and permissions setup
    # 4. Organization member creation with role assignment

    # Account Types:
    # - Personal: Individual account for freelancers, students, personal use
    # - Business: Corporate account for companies, teams, organizations

    # - Organization slug generation with uniqueness validation
    # - Trial status for new organizations
    # - Automatic Super Admin role assignment
    # - Complete permission system setup

    # X- Organization slug uniqueness validation
@router.post(
    "/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
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
@handle_api_exceptions("signup")
async def signup(
    request: Request, # pylint: disable=unused-argument
    signup_data: SignupRequest = Body(...),
):
    """
    ## User signup endpoint for both personal and business accounts
    ## This endpoint creates a complete account setup including User signup with Supabase Auth

    ### Features:
    - Email validation and duplicate checking
    - Password strength requirements (minimum 6 characters)

    ### Args:
        signup_data (SignupRequest): Signup data including user credentials and info

    ### Returns:
        SignupResponse: Success response with user data

    ### Raises:
        HTTPException: 400 for validation errors
        HTTPException: 409 for duplicate email
        HTTPException: 500 for database or Supabase errors

    ### Security Features:
    - Password hashing handled by Supabase
    - Email validation and uniqueness checking
    - Transaction rollback on failures
    - Proper error handling without exposing internal details
    """

    _validate_password_strength(signup_data.password)

    await _validate_verification_code_for_signup(
        verification_id=signup_data.verificationId,
        email=signup_data.email,
        verification_code=signup_data.verificationCode
    )

    signup_result = await sign_up_supabase_user(signup_data)

    session = await _get_session_after_signup(
        signup_result=signup_result,
        email=signup_data.email,
        password=signup_data.password
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create session after signup"
        )

    _send_welcome_email_safely(
        email=signup_data.email,
        first_name=signup_data.first_name
    )

    return AuthResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        expires_at=session.expires_at,
        user=UserInfo(
            id=signup_result.user.id,
            email=signup_result.user.email,
            first_name=signup_result.user.user_metadata.get("first_name", None),
            last_name=signup_result.user.user_metadata.get("last_name", None),
            timezone=signup_result.user.user_metadata.get("timezone", None),
        ),
    )


def _get_not_found_response():
    data = VerifyEmailResponse(
        message="Email not found.",
        email_found=False,
        status=None,
        can_login=False,
    )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=data.model_dump())

def _extract_user_type_strict(row) -> str|None:
    if not row:
        return None
    user_meta = row.user_metadata
    app_meta = row.app_metadata
    if isinstance(user_meta, dict):
        utype = user_meta.get("type") or user_meta.get("user_type")
        if utype:
            return utype
    if isinstance(app_meta, dict):
        return app_meta.get("type") or app_meta.get("user_type")
    return None

@handle_api_exceptions("verify email")
@router.post(
    "/email/verify", response_model=VerifyEmailResponse, status_code=status.HTTP_200_OK
)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def verify_email(
    request: Request,
    body: VerifyEmailRequest = Body(...),
):
    """
    Verify user email and status by determining user type from auth.users metadata
    and checking the corresponding table for status.
    """

    # 1) Get user from auth.users using centralized operation
    auth_user = await get_auth_user_by_email(body.email)
    if not auth_user:
        return _get_not_found_response()


    # 2) Extract and validate user type
    user_type = _extract_user_type_strict(auth_user)
    if not user_type:
        return _get_not_found_response()

    if user_type == "organization_member":
        status_value = await get_organization_member_status_by_email(body.email)
        if status_value:
            can_login_local = status_value == "active"
            return VerifyEmailResponse(
                message="Email found." if can_login_local else "Account is suspended.",
                email_found=True,
                status=status_value,
                can_login=can_login_local,
            )
    return _get_not_found_response()


@handle_api_exceptions("delete user")
@router.delete("/user", status_code=status.HTTP_204_NO_CONTENT)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Delete user directly from auth.users table without validation.

    This endpoint allows administrators to delete a user account directly
    from the database auth.users table. Use with caution as this operation
    is irreversible and will remove all user authentication data.

    Args:
        user_id (str): The ID of the user to delete

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
        user_id = current_user['sub']

        result = await delete_auth_user(user_id)

        if result is not None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user found with ID {user_id}")
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Failed to delete user %s: %s", user_id, error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(error)}"
        ) from error


@router.get("/link-user-oauth-url/{provider}", status_code=status.HTTP_200_OK)
async def get_oauth_link_url_endpoint(
    provider: str,
    current_user: dict = Depends(get_user_from_auth)
):
    """
    Generate Google OAuth URL for linking to existing email/password user.
    User calls this after signing up with email/password.
    """
    try:
        # Extract user info from JWT token
        user_id = current_user['sub']
        user_email = current_user['email']

        # Check if user already has linked provider
        providers = current_user.get('app_metadata', {}).get('providers', [])
        if provider in providers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{provider} account is already linked to this user"
            )

        result = await get_oauth_link_url(user_id, user_email, provider)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in get_oauth_link_url: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate {provider} OAuth URL"
        )

@router.get("/oauth-connect/{provider}", status_code=status.HTTP_200_OK)
async def oauth_connect(provider: str):
    """
    Generate OAuth URL for frontend redirect.
    """
    try:
        return await supabase_user_oauth(provider)
    except Exception as e:
        logger.error("Error in oauth_connect: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate {provider} OAuth URL"
        )

@router.get("/oauth-callback", status_code=status.HTTP_200_OK)
async def oauth_callback(request: Request):
    """
    Handle OAuth callback for both linking identity and general OAuth flow.
    """
    try:
        # Get parameters
        code = request.query_params.get("code")

        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing authorization code"
            )

        # Exchange code for session
        session_result = await get_session_by_id_admin(code)

        if not session_result.session:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to exchange authorization code"
            )

        # # If user_id is provided, this is a linking flow
        # if user_id:
        #     return await link_google_identity_to_existing_user(user_id, provider, session_result)

        # Otherwise, this is a general OAuth flow
        return {
            "success": True,
            "message": "OAuth authentication successful",
            "data": session_result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error in OAuth callback: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process OAuth callback"
        )
