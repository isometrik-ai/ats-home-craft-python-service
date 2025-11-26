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
from datetime import datetime
from typing import Optional

# Third-party imports
import jwt
from fastapi import APIRouter, HTTPException, status, Body, Request, Depends, Response
from supabase_auth.errors import AuthApiError

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
    ChangePasswordRequest,
    ChangePasswordResponse,
    Check2FAStatusRequest,
    Check2FAStatusResponse,
    PASSWORD_CONDITION_MESSAGE_EXTENDED

)

# App instance
from apps.user_service.app.app_instance import limiter

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_token, get_user_from_auth

# Email utilities
from libs.shared_utils.email_utils import (
    send_password_reset_confirmation_email,
    send_welcome_email,
    send_password_change_success_email,
    send_password_reset_success_email
)

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
    refresh_session
)
from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    get_verification_code_by_id,
)
from apps.user_service.app.api.verification_codes import (
    _validate_verification_record,
    _verify_code_and_update_record,
)
from apps.user_service.app.schemas.verification_codes import (
    VerificationType,
    VerifyVerificationCodeRequest,
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

EMAIL_NOT_FOUND_MESSAGE = "Email Is Not Registered! Please Signup First To Login."
INVALID_LOGIN_CREDS = "Invalid login credentials"
TWO_FA_VERIFICATION_FAILED = "2FA verification failed"
TWO_FA_REQUIRED = "2FA verification is required. Please provide verification_id and verification_code"

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


def _is_2fa_enabled(user_metadata: dict) -> tuple[bool, Optional[dict]]:
    """
    Check if 2FA is enabled in user metadata.

    Args:
        user_metadata: User metadata from auth.users

    Returns:
        Tuple of (is_enabled, verification_preference_dict)
    """
    verification_preference = user_metadata.get("verification_preference")
    if verification_preference and isinstance(verification_preference, dict):
        enabled = verification_preference.get("enabled", False)
        if enabled is True:
            return True, verification_preference
    return False, None


def _validate_2fa_credentials_required(
    verification_id: Optional[str],
    verification_code: Optional[str]
) -> None:
    """
    Validate that 2FA credentials are provided when required.

    Args:
        verification_id: Optional verification code ID
        verification_code: Optional verification code

    Raises:
        HTTPException: If credentials are missing
    """
    if not verification_id or not verification_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TWO_FA_REQUIRED
        )


async def _get_and_validate_verification_record(verification_id: str) -> dict:
    """
    Get and validate verification code record.

    Args:
        verification_id: Verification code ID

    Returns:
        Verification record dictionary

    Raises:
        HTTPException: If record not found or invalid
    """
    verification_record = await get_verification_code_by_id(verification_id)
    
    if not verification_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification code not found"
        )
    
    stored_given_input = verification_record.get("given_input")
    if not stored_given_input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TWO_FA_VERIFICATION_FAILED
        )
    
    return verification_record


def _validate_phone_match(stored_given_input: str, user_phone: Optional[str]) -> None:
    """
    Validate that stored phone matches user's phone.

    Args:
        stored_given_input: Stored phone from verification record
        user_phone: User's phone number

    Raises:
        HTTPException: If phones don't match
    """
    if user_phone and stored_given_input != user_phone:
        normalized_stored = stored_given_input.lstrip("+")
        normalized_user = user_phone.lstrip("+")
        if normalized_stored != normalized_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=TWO_FA_VERIFICATION_FAILED
            )


def _create_verification_request(
    verification_preference: dict,
    verification_id: str,
    verification_code: str,
    stored_given_input: str,
    email: str
) -> VerifyVerificationCodeRequest:
    """
    Create VerifyVerificationCodeRequest based on verification type.

    Args:
        verification_preference: Verification preference dict
        verification_id: Verification code ID
        verification_code: Verification code
        stored_given_input: Stored email or phone
        email: User email

    Returns:
        VerifyVerificationCodeRequest object

    Raises:
        HTTPException: If email doesn't match for EMAIL type
    """
    verification_method = verification_preference.get("type", "EMAIL").upper()
    
    if verification_method == "PHONE":
        return VerifyVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            verification_id=verification_id,
            verification_code=verification_code,
            phoneNumber=stored_given_input
        )
    else:
        # For email verification, verify that stored_given_input matches user's email
        if stored_given_input.lower() != email.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=TWO_FA_VERIFICATION_FAILED
            )
        return VerifyVerificationCodeRequest(
            type=VerificationType.EMAIL,
            verification_id=verification_id,
            verification_code=verification_code,
            email=stored_given_input
        )


async def _verify_2fa_code(
    verification_record: dict,
    verify_data: VerifyVerificationCodeRequest,
    verification_code: str,
    verification_id: str
) -> None:
    """
    Verify 2FA code and update record.

    Args:
        verification_record: Verification record dictionary
        verify_data: VerifyVerificationCodeRequest object
        verification_code: Verification code
        verification_id: Verification code ID

    Raises:
        HTTPException: If verification fails
    """
    try:
        _validate_verification_record(verification_record, verify_data)
    except HTTPException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TWO_FA_VERIFICATION_FAILED
        ) from e
    
    try:
        await _verify_code_and_update_record(
            verification_record,
            verification_code,
            verification_id
        )
    except HTTPException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=TWO_FA_VERIFICATION_FAILED
        ) from e


async def _check_and_verify_2fa(
    user_metadata: dict,
    verification_id: Optional[str],
    verification_code: Optional[str],
    email: str,
    user_phone: Optional[str] = None
) -> None:
    """
    Check if user has 2FA enabled and verify the code if required.

    Args:
        user_metadata: User metadata from auth.users
        verification_id: Optional verification code ID
        verification_code: Optional verification code
        email: User email for verification
        user_phone: Optional user phone number (for PHONE type verification)

    Raises:
        HTTPException: If 2FA is enabled but verification fails
    """
    is_enabled, verification_preference = _is_2fa_enabled(user_metadata)
    if not is_enabled:
        return
    
    _validate_2fa_credentials_required(verification_id, verification_code)
    
    verification_record = await _get_and_validate_verification_record(verification_id)
    stored_given_input = verification_record.get("given_input")
    
    # Validate phone match if PHONE type
    verification_method = verification_preference.get("type", "EMAIL").upper()
    if verification_method == "PHONE":
        _validate_phone_match(stored_given_input, user_phone)
    
    verify_data = _create_verification_request(
        verification_preference,
        verification_id,
        verification_code,
        stored_given_input,
        email
    )
    
    await _verify_2fa_code(
        verification_record,
        verify_data,
        verification_code,
        verification_id
    )


@router.post("/login", response_model=AuthResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
async def login(request: Request, data: AuthLogin):
    """
    User login endpoint with optional 2FA support

    Args:
        request (Request): FastAPI request object
        data (AuthLogin): Login credentials containing email, password, and optional 2FA fields

    Returns:
        AuthResponse: Access token and user information

    Raises:
        HTTPException: 
            - 400 for invalid credentials or 2FA verification failure
            - 500 for other errors
    """
    try:
        all_user = await get_auth_user_by_email(data.email)
        if all_user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=EMAIL_NOT_FOUND_MESSAGE
            )
        
        # Check if user has 2FA enabled and verify if needed
        user_metadata = all_user.user_metadata or {}
        user_phone = getattr(all_user, 'phone', None)  # Get phone from auth.users
        await _check_and_verify_2fa(
            user_metadata=user_metadata,
            verification_id=data.verification_id,
            verification_code=data.verification_code,
            email=data.email,
            user_phone=user_phone
        )
        
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
                phone=result.user.user_metadata.get("phone", None),
                timezone=result.user.user_metadata.get("timezone", None),
                org_setup_status_completed=bool(result.user.user_metadata.get("organization_id", False)),
                organization_id=result.user.user_metadata.get("organization_id", None),
            ),
        )
    except HTTPException:
        raise
    except AuthApiError as error:
        # AuthApiError from Supabase for invalid credentials
        # login_user already handles "Email not confirmed" as HTTPException 403
        # So any AuthApiError here is likely invalid credentials
        if error.status == 400 and error.message == INVALID_LOGIN_CREDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_LOGIN_CREDS
            ) from error
        elif hasattr(error, 'status') and hasattr(error, 'message'):
            raise HTTPException(
                status_code=error.status,
                detail=error.message
            ) from error
        # For any other AuthApiError, treat as invalid credentials (most common case)
        logger.warning("AuthApiError during login (treating as invalid credentials): %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_LOGIN_CREDS
        ) from error
    except Exception as error:
        log_exception()
        error_str = str(error).lower()
        # Check for invalid credentials in error message
        if INVALID_LOGIN_CREDS in error_str or \
           ("invalid" in error_str and "credential" in error_str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_LOGIN_CREDS
            ) from error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        ) from error


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

        user_metadata = res.user.user_metadata or {}
        return AuthResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            expires_in=res.session.expires_in,
            expires_at=res.session.expires_at,
            user=UserInfo(
                id=res.user.id,
                email=res.user.email,
                first_name=user_metadata.get("first_name"),
                last_name=user_metadata.get("last_name"),
                phone=user_metadata.get("phone"),
                timezone=user_metadata.get("timezone"),
                org_setup_status_completed=bool(user_metadata.get("organization_id")),
                organization_id=user_metadata.get("organization_id"),
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

            # Send password reset success email to user
            user_email = user.get('email', '')
            user_metadata = user.get('user_metadata', {})
            user_name = user_metadata.get('full_name', '') or \
                       f"{user_metadata.get('first_name', '')} {user_metadata.get('last_name', '')}".strip() or \
                       user_email.split('@')[0]

            try:
                email_sent = send_password_reset_success_email(email=user_email, user_name=user_name)
                if not email_sent:
                    logger.warning("Failed to send password reset success email to %s", user_email)
                    # Note: We don't fail the entire operation if email fails
                    # The password reset was successful, only the email notification failed
            except Exception as email_error:
                logger.error("Error sending password reset success email: %s", str(email_error))
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
        verification_id=signup_data.verification_id,
        email=signup_data.email,
        verification_code=signup_data.verification_code
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
            phone=signup_result.user.user_metadata.get("phone", None),
            timezone=signup_result.user.user_metadata.get("timezone", None),
        ),
    )


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

    Returns email_found=True if email exists in auth.users, regardless of user type or status.
    """
    try:
        # 1) Get user from auth.users using centralized operation
        auth_user = await get_auth_user_by_email(body.email)
        if not auth_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message": EMAIL_NOT_FOUND_MESSAGE
                }
            )

        # 2) Get organization member status by email
        status_value = await get_organization_member_status_by_email(body.email)
        if status_value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message": EMAIL_NOT_FOUND_MESSAGE
                }
            )
        elif status_value != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Account is not active. Please contact support."
                }
            )
        else:
            # User exists in auth.users but not in organization_members table
            return VerifyEmailResponse(
                message="Email verified and active.",
                email_found=True,
                status="active",
                can_login=True
            )

        # 4) User exists in auth.users but is not organization_member or has no user type
        # Still return email_found=True since email exists
    except HTTPException as error:
        raise error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to verify email"
            }
        ) from error


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


# ============================================================================
# CHANGE PASSWORD API
# ============================================================================

def _handle_password_update_error(error: Exception) -> None:
    """
    Handle errors during password update and raise appropriate HTTPException.

    Args:
        error: The exception that occurred during password update

    Raises:
        HTTPException: Appropriate error based on error message
    """
    error_message = str(error).lower()
    logger.error("Error updating password: %s", str(error))

    # Check for specific Supabase errors
    if "user not allowed" in error_message or "not allowed" in error_message:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is restricted. Please contact support if you believe this is an error."
        ) from error
    elif "auth" in error_message or "authentication" in error_message:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service error. Please try again later."
        ) from error
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update password"
        ) from error


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    status_code=status.HTTP_200_OK
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="auth.users",
    category="PASSWORD_CHANGE",
)
@handle_api_exceptions("change_password")
async def change_password(
    request: Request,  # pylint: disable=unused-argument
    data: ChangePasswordRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Change user password endpoint.

    Requires authentication. Validates current password before updating to new password.

    Args:
        data: ChangePasswordRequest containing current_password and new_password
        current_user: Authenticated user from JWT token

    Returns:
        ChangePasswordResponse: Success message

    Raises:
        HTTPException: 400 for invalid current password or validation errors
        HTTPException: 500 for server errors
    """
    user_id = current_user.get("sub")
    user_email = current_user.get("email")

    if not user_id or not user_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user information"
        )

    # Validate new password strength
    _validate_password_strength(data.new_password)

    # Step :1 ) verify current password matches database password (must pass before proceeding)
    try:
        await login_user(user_email, data.current_password)
    except HTTPException as e:
        # if e.status_code == 401 or e.status_code == 403:
        if e.status_code == 400 and e.detail == INVALID_LOGIN_CREDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            ) from e
        else:
            raise e

    # Step :2 ) Check if new password is same as current password
    if data.current_password == data.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password"
        )
    # Check if new password is same as current password in database
    # Attempt login with new password - if it succeeds, new password = current password

    # Step :3 ) Update password
    try:
        result = await update_password_with_link_identity(user_id, data.new_password)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password"
            )
    except HTTPException:
        raise
    except Exception as e:
        _handle_password_update_error(e)

    # Send password change success email
    try:
        user_metadata = current_user.get("user_metadata", {})
        if user_metadata.get("first_name"):
            user_name = user_metadata.get("first_name")
        elif user_metadata.get("full_name"):
            user_name = user_metadata.get("full_name")
        else:
            user_name = user_email.split('@')[0]

        email_sent = send_password_change_success_email(email=user_email, user_name=user_name)
        if not email_sent:
            logger.warning("Failed to send password change success email to %s", user_email)
    except Exception as email_error:
        logger.error("Error sending password change success email: %s", str(email_error))
        # Don't fail the operation if email fails

    return ChangePasswordResponse(
        message="Password changed successfully"
    )


# ============================================================================
# CHECK 2FA STATUS API
# ============================================================================

async def _validate_credentials_for_2fa_check(email: str, password: str) -> None:
    """
    Validate user credentials for 2FA status check.
    
    Args:
        email: User email
        password: User password
        
    Raises:
        HTTPException: If credentials are invalid
    """
    try:
        await login_user(email, password)
    except HTTPException as e:
        # Re-raise HTTPException as-is (e.g., invalid credentials)
        raise e
    except AuthApiError as error:
        # AuthApiError from Supabase for invalid credentials
        if error.status == 400 and error.message == INVALID_LOGIN_CREDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_LOGIN_CREDS
            ) from error
        if hasattr(error, 'status') and hasattr(error, 'message'):
            raise HTTPException(
                status_code=error.status,
                detail=error.message
            ) from error
        # For any other AuthApiError, treat as invalid credentials
        logger.warning("AuthApiError during 2FA status check (treating as invalid credentials): %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_LOGIN_CREDS
        ) from error
    except Exception as error:
        error_str = str(error).lower()
        # Check for invalid credentials in error message
        if INVALID_LOGIN_CREDS in error_str or \
           ("invalid" in error_str and "credential" in error_str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_LOGIN_CREDS
            ) from error
        # For other errors, log and re-raise as generic error
        log_exception()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate credentials"
        ) from error


@router.post(
    "/verify/account",
    response_model=Check2FAStatusResponse,
    status_code=status.HTTP_200_OK
)
@limiter.limit("10/minute")
# pylint: disable=unused-argument  # Required by @limiter.limit
@handle_api_exceptions("check_2fa_status")
async def check_2fa_status(
    request: Request,  # pylint: disable=unused-argument
    data: Check2FAStatusRequest = Body(...),
):
    """
    Check if 2FA is enabled for a user account.

    This endpoint validates the user's credentials (email and password) and
    returns whether 2FA is enabled for their account.

    Args:
        request (Request): FastAPI request object
        data (Check2FAStatusRequest): Email and password for validation

    Returns:
        Check2FAStatusResponse: Response containing two_fa_enabled boolean

    Raises:
        HTTPException:
            - 400: Email not registered or invalid credentials
            - 500: Internal server error
    """
    try:
        # Step 1: Check if user account exists
        all_user = await get_auth_user_by_email(data.email)
        if all_user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=EMAIL_NOT_FOUND_MESSAGE
            )

        # Step 2: Validate email and password are correct
        await _validate_credentials_for_2fa_check(data.email, data.password)

        # Step 3: Check if 2FA is enabled
        user_metadata = all_user.user_metadata or {}
        is_enabled, _ = _is_2fa_enabled(user_metadata)

        return Check2FAStatusResponse(
            two_fa_enabled=is_enabled
        )

    except HTTPException:
        raise
    except Exception as error:
        log_exception()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check 2FA status"
        ) from error
