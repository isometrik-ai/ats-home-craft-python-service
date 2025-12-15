"""Authentication API Module
This module provides authentication operations using Supabase.
Includes login and signup functionality with proper error handling.
"""

import os

# Standard library imports
import re
import sys
from datetime import datetime
from typing import Any

# Third-party imports
import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi import status as http_status

from apps.user_service.app.api.verification_codes import (
    _validate_verification_record,
    _verify_code_and_update_record,
)

# App instance
from apps.user_service.app.app_instance import limiter

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Internal utility imports
from apps.user_service.app.dependencies.common_utils import handle_api_exceptions

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Schema imports
from apps.user_service.app.schemas.auth import (
    AuthLogin,
    AuthResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    Check2FAStatusRequest,
    Check2FAStatusResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    PasswordResponse,
    ResetPasswordRequest,
    SetPasswordRequest,
    SignupRequest,
    UserInfo,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from apps.user_service.app.schemas.verification_codes import (
    VerificationType,
    VerifyVerificationCodeRequest,
)
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
    get_organization_member_status_by_email,
)
from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    get_verification_code_by_id,
)
from libs.shared_db.supabase_db.admin_operations.user import (
    delete_auth_user,
    update_password_with_link_identity,
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    login_user,
    refresh_session,
    reset_the_password_email,
    sign_up_supabase_user,
    update_password_with_token,
)

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_auth, get_user_from_token

# Email utilities
from libs.shared_utils.email_utils import (
    send_password_change_success_email,
    send_password_reset_success_email,
    send_welcome_email,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

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
    verification_id: str, email: str, verification_code: str
) -> None:
    """Validate verification code for signup (cross-security check).

    Args:
        verification_id: Verification code ID
        email: Email to validate
        verification_code: Verification code to validate

    Raises:
        BadRequestException: If verification code is invalid
        ConflictException: If verification code is already verified
    """
    verification_record = await get_verification_code_by_id(verification_id)

    if not verification_record:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_not_found",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    if not verification_record.get("verified", False):
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_not_verified",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    stored_given_input = verification_record.get("given_input")
    if stored_given_input != email:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    stored_code = verification_record.get("verification_code")
    if verification_code != stored_code:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )


def _extract_session(session: Any | None) -> Any | None:
    """Extract session object if available.

    Args:
        session (Any | None): Session object (type depends on auth provider)

    Returns:
        Any | None: Session object if available, None otherwise
    """
    if session and hasattr(session, "access_token"):
        return session
    return None


async def _get_session_after_signup(signup_result: Any, email: str, password: str) -> Any | None:
    """Get session after signup, trying signup session first, then login if needed.

    Args:
        signup_result (Any): Result from sign_up_supabase_user (type depends on auth provider)
        email (str): User email
        password (str): User password

    Returns:
        Any | None: Session object if available, None otherwise
    """
    session = _extract_session(signup_result.session)
    if session:
        return session

    login_result = await login_user(email, password)
    if login_result:
        return _extract_session(login_result.session)
    return None


def _validate_password_strength(password: str) -> None:
    """Validate password strength and raise exception if weak.

    Args:
        password: Password to validate

    Raises:
        BadRequestException: If password is weak
    """
    password_pattern = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{6,}$")

    if not bool(password_pattern.match(password)):
        raise BadRequestException(
            message_key="auth.errors.password_strength",
            custom_code=CustomStatusCode.INVALID_DATA,
        )


# ============================================================================
# API ENDPOINTS
# ============================================================================


def _is_2fa_enabled(user_metadata: dict) -> tuple[bool, dict | None]:
    """Check if 2FA is enabled in user metadata.

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
    verification_id: str | None, verification_code: str | None
) -> None:
    """Validate that 2FA credentials are provided when required.

    Args:
        verification_id: Verification code ID
        verification_code: Verification code

    Raises:
        BadRequestException: If credentials are missing
    """
    if not verification_id or not verification_code:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )


async def _get_and_validate_verification_record(verification_id: str) -> dict:
    """Get and validate verification code record.

    Args:
        verification_id: Verification code ID

    Returns:
        Verification record dictionary

    Raises:
        BadRequestException: If record not found or invalid
    """
    verification_record = await get_verification_code_by_id(verification_id)
    stored_given_input = verification_record.get("given_input")

    if not verification_record or not stored_given_input:
        raise BadRequestException(
            message_key="verification_codes.errors.verification_code_invalid",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    return verification_record


def _validate_phone_match(stored_given_input: str, user_phone: str | None) -> None:
    """Validate that stored phone matches user's phone.

    Args:
        stored_given_input: Stored phone from verification record
        user_phone: User's phone number

    Raises:
        BadRequestException: If phones don't match
    """
    if user_phone and stored_given_input != user_phone:
        normalized_stored = stored_given_input.lstrip("+")
        normalized_user = user_phone.lstrip("+")
        if normalized_stored != normalized_user:
            raise BadRequestException(
                message_key="auth.errors.verification_code_not_matched_phone",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )


def _create_verification_request(
    verification_preference: dict,
    verification_id: str,
    verification_code: str,
    stored_given_input: str,
    email: str,
) -> VerifyVerificationCodeRequest:
    """Create VerifyVerificationCodeRequest based on verification type.

    Args:
        verification_preference: Verification preference dict
        verification_id: Verification code ID
        verification_code: Verification code
        stored_given_input: Stored email or phone
        email: User email

    Returns:
        VerifyVerificationCodeRequest object

    Raises:
        BadRequestException: If email doesn't match for EMAIL type
    """
    verification_method = verification_preference.get("type", "EMAIL").upper()

    if verification_method == "PHONE":
        return VerifyVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            verification_id=verification_id,
            verification_code=verification_code,
            phoneNumber=stored_given_input,
        )
    # For email verification, verify that stored_given_input matches user's email
    if stored_given_input.lower() != email.lower():
        raise BadRequestException(
            message_key="auth.errors.verification_code_not_matched_email",
            custom_code=CustomStatusCode.INVALID_DATA,
        )
    return VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=verification_id,
        verification_code=verification_code,
        email=stored_given_input,
    )


async def _check_and_verify_2fa(
    user_metadata: dict,
    verification_id: str | None,
    verification_code: str | None,
    email: str,
    user_phone: str | None = None,
) -> None:
    """Check if user has 2FA enabled and verify the code if required.

    Args:
        user_metadata: User metadata from auth.users
        verification_id: Verification code ID
        verification_code: Verification code
        email: User email for verification
        user_phone: User phone number (for PHONE type verification)

    Raises:
        BadRequestException: If 2FA is enabled but verification fails
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
        email,
    )

    await _validate_verification_record(verification_record, verify_data)

    await _verify_code_and_update_record(verification_record, verification_code, verification_id)


@handle_api_exceptions("login")
@router.post(
    "/login",
    response_model=AuthResponse,
    status_code=http_status.HTTP_200_OK,
    description="Login endpoint with optional 2FA support",
    summary="Login endpoint with optional 2FA support",
    responses={
        http_status.HTTP_200_OK: {"description": "Login successful"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def login(request: Request, data: AuthLogin):
    """User login endpoint with optional 2FA support"""
    try:
        all_user = await get_auth_user_by_email(data.email)
        if all_user is None:
            raise NotFoundException(
                message_key="auth.errors.email_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        user_metadata = all_user.user_metadata or {}
        user_phone = getattr(all_user, "phone", None)  # Get phone from auth.users
        await _check_and_verify_2fa(
            user_metadata=user_metadata,
            verification_id=data.verification_id,
            verification_code=data.verification_code,
            email=data.email,
            user_phone=user_phone,
        )

        user_agent = request.headers.get("User-Agent")
        device_signature = request.headers.get("X-Device-Signature")

        result = await login_user(
            email=data.email,
            password=data.password,
            user_agent=user_agent,
            device_signature=device_signature,
        )

        if not result:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        if not hasattr(result, "session") or result.session is None:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        if not hasattr(result, "user") or result.user is None:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        session = result.session
        user = result.user
        user_metadata = getattr(user, "user_metadata", {}) or {}

        # Validate required session attributes
        if not hasattr(session, "access_token") or not session.access_token:
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        return success_response(
            request=request,
            message_key="auth.success.login_successful",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=AuthResponse(
                access_token=session.access_token,
                refresh_token=getattr(session, "refresh_token", None),
                expires_in=getattr(session, "expires_in", None),
                expires_at=getattr(session, "expires_at", None),
                user=UserInfo(
                    id=getattr(user, "id", None),
                    email=getattr(user, "email", None),
                    first_name=user_metadata.get("first_name", None),
                    last_name=user_metadata.get("last_name", None),
                    phone=user_metadata.get("phone", None),
                    timezone=user_metadata.get("timezone", None),
                    org_setup_status_completed=bool(user_metadata.get("organization_id", False)),
                    organization_id=user_metadata.get("organization_id", None),
                ),
            ),
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error logging in: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("refresh")
@router.put(
    "/refresh",
    response_model=AuthResponse,
    status_code=http_status.HTTP_200_OK,
    description="Refresh user session",
    summary="Refresh user session",
    responses={
        http_status.HTTP_200_OK: {"description": "Session refreshed successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def refresh(request: Request):
    """Refresh user session"""
    try:
        access_token = request.headers.get("Access-Token").strip()
        refresh_token = request.headers.get("Refresh-Token", None).strip()
        decoded = jwt.decode(
            access_token,
            os.getenv("SUPABASE_JWT_SECRET"),
            algorithms=["HS256"],
            audience="authenticated",
        )
        if datetime.fromtimestamp(decoded.get("exp")) >= datetime.now():
            raise BadRequestException(
                message_key="auth.errors.token_not_expired",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        res = await refresh_session(refresh_token)

        user_metadata = res.user.user_metadata or {}
        return success_response(
            request=request,
            message_key="auth.success.session_refreshed",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=AuthResponse(
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
                ),
            ),
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error refreshing session: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("set password")
@router.post(
    "/set-password",
    status_code=http_status.HTTP_202_ACCEPTED,
    response_model=PasswordResponse,
    description="Set password for user Signed Up from Google or Magic Link.",
    summary="Set password for user Signed Up from Google or Magic Link.",
    responses={
        http_status.HTTP_202_ACCEPTED: {"description": "Password set successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def set_password(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    data: SetPasswordRequest = Body(...),
):
    """Set password for user Signed Up from Google or Magic Link."""
    try:
        _validate_password_strength(data.password)

        result = await update_password_with_link_identity(current_user["sub"], data.password)
        if result:
            return success_response(
                request=request,
                message_key="auth.success.password_set_successfully",
                custom_code=CustomStatusCode.SUCCESS,
                status_code=http_status.HTTP_202_ACCEPTED,
            )
        raise BadRequestException(
            message_key="auth.errors.failed_to_set_password",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error setting password: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("forgot password")
@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=http_status.HTTP_200_OK,
    description="Send password reset email to user (only if email exists in system)",
    summary="Send password reset email to user (only if email exists in system)",
    responses={
        http_status.HTTP_200_OK: {"description": "Password reset email sent successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def forgot_password(
    request: Request,
    data: ForgotPasswordRequest = Body(...),
):
    """Send password reset email to user (only if email exists in system)"""
    try:
        user = await get_auth_user_by_email(data.email)
        if not user:
            raise NotFoundException(
                message_key="auth.errors.email_not_found_in_system",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Send password reset email only if user exists
        await reset_the_password_email(data.email)
        return success_response(
            request=request,
            message_key="auth.success.password_reset_email_sent",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error sending password reset email: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("reset password")
@router.post(
    "/reset-password",
    response_model=PasswordResponse,
    status_code=http_status.HTTP_200_OK,
    description="Reset user password using token from email",
    summary="Reset user password using token from email",
    responses={
        http_status.HTTP_200_OK: {"description": "Password reset successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def reset_password(
    request: Request,
    data: ResetPasswordRequest = Body(...),
):
    """Reset user password using token from email"""
    try:
        user = get_user_from_token(data.token)
        if not user:
            raise NotFoundException(
                message_key="auth.errors.email_not_found_in_system",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        _validate_password_strength(data.new_password)

        result = await update_password_with_token(user["sub"], data.new_password)
        if result.user:
            # Send password reset success email to user
            user_email = user.get("email", "")
            user_metadata = user.get("user_metadata", {})
            user_name = (
                user_metadata.get("full_name", "")
                or (
                    f"{user_metadata.get('first_name', '')} {user_metadata.get('last_name', '')}"
                ).strip()
                or user_email.split("@")[0]
            )

            send_password_reset_success_email(email=user_email, user_name=user_name)
            return success_response(
                request=request,
                message_key="auth.success.password_reset_successfully",
                custom_code=CustomStatusCode.SUCCESS,
                status_code=http_status.HTTP_200_OK,
            )
        raise BadRequestException(
            message_key="auth.errors.failed_to_update_password",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error resetting password: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=http_status.HTTP_201_CREATED,
    description="Signup endpoint for both personal and business accounts",
    summary="Signup endpoint for both personal and business accounts",
    responses={
        http_status.HTTP_201_CREATED: {"description": "User signed up successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_409_CONFLICT: {"description": "Duplicate email"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
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
    request: Request,
    signup_data: SignupRequest = Body(...),
):
    """User signup endpoint for both personal and business accounts
    This endpoint creates a complete account setup including User signup with Supabase Auth
    """
    try:
        _validate_password_strength(signup_data.password)

        await _validate_verification_code_for_signup(
            verification_id=signup_data.verification_id,
            email=signup_data.email,
            verification_code=signup_data.verification_code,
        )

        signup_result = await sign_up_supabase_user(signup_data)

        session = await _get_session_after_signup(
            signup_result=signup_result,
            email=signup_data.email,
            password=signup_data.password,
        )

        if not session:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        send_welcome_email(email=signup_data.email, first_name=signup_data.first_name)

        return success_response(
            request=request,
            message_key="auth.success.user_signed_up",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_201_CREATED,
            data=AuthResponse(
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
            ),
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error signing up user: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("verify email")
@router.post(
    "/email/verify",
    response_model=VerifyEmailResponse,
    status_code=http_status.HTTP_200_OK,
    description="Verify user email and status by determining user type from auth.users metadata.",
    summary="Verify user email and status by determining user type from auth.users metadata.",
    responses={
        http_status.HTTP_200_OK: {"description": "Email verified and active"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
@handle_api_exceptions("verify email")
async def verify_email(
    request: Request,
    body: VerifyEmailRequest = Body(...),
):
    """Verify user email and status by determining user type from auth.users metadata
    and checking the corresponding table for status."""
    try:
        auth_user = await get_auth_user_by_email(body.email)
        if not auth_user:
            raise NotFoundException(
                message_key="auth.errors.email_not_found_in_system",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        status_value = await get_organization_member_status_by_email(body.email)
        if status_value is None:
            raise NotFoundException(
                message_key="auth.errors.email_not_found_in_system",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if status_value != "active":
            raise ForbiddenException(
                message_key="auth.errors.account_not_active",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        return success_response(
            request=request,
            message_key="auth.success.email_verified",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error verifying email: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@handle_api_exceptions("delete user")
@router.delete(
    "/user",
    status_code=http_status.HTTP_204_NO_CONTENT,
    description="Delete user directly from auth.users table without validation",
    summary="Delete user directly from auth.users table without validation",
    responses={
        http_status.HTTP_204_NO_CONTENT: {"description": "User deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="auth.users",
    category="USER_DELETE",
)
async def delete_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete user directly from auth.users table without validation."""
    try:
        user_id = current_user["sub"]

        result = await delete_auth_user(user_id)

        if result is not None:
            return success_response(
                request=request,
                message_key="auth.success.user_deleted",
                custom_code=CustomStatusCode.SUCCESS,
                status_code=http_status.HTTP_200_OK,
            )
        raise NotFoundException(
            message_key="auth.errors.user_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Failed to delete user %s: %s", user_id, error)
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    status_code=http_status.HTTP_200_OK,
    description="Change user password endpoint. Requires authentication.",
    summary="Change user password endpoint. Requires authentication.",
    responses={
        http_status.HTTP_200_OK: {"description": "Password changed successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
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
    request: Request,
    data: ChangePasswordRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
):
    """Change user password endpoint."""
    user_id = current_user.get("sub")
    user_email = current_user.get("email")

    if not user_id or not user_email:
        raise BadRequestException(
            message_key="auth.errors.failed_to_set_password",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Validate new password strength
    _validate_password_strength(data.new_password)

    await login_user(user_email, data.current_password)

    if data.current_password == data.new_password:
        raise BadRequestException(
            message_key="auth.errors.new_password_must_be_different_from_current_password",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )
    await update_password_with_link_identity(user_id, data.new_password)

    user_metadata = current_user.get("user_metadata", {})
    if user_metadata.get("first_name"):
        user_name = user_metadata.get("first_name")
    elif user_metadata.get("full_name"):
        user_name = user_metadata.get("full_name")
    else:
        user_name = user_email.split("@")[0]

    send_password_change_success_email(email=user_email, user_name=user_name)
    return success_response(
        request=request,
        message_key="auth.success.password_changed_successfully",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@router.post(
    "/verify/account",
    response_model=Check2FAStatusResponse,
    description="Check if 2FA is enabled for a user account.",
    summary="Check if 2FA is enabled for a user account.",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "2FA is enabled"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
@handle_api_exceptions("check_2fa_status")
async def check_2fa_status(
    request: Request,
    data: Check2FAStatusRequest = Body(...),
):
    """Check if 2FA is enabled for a user account."""
    try:
        all_user = await get_auth_user_by_email(data.email)
        if all_user is None:
            raise NotFoundException(
                message_key="auth.errors.email_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        await login_user(data.email, data.password)

        user_metadata = all_user.user_metadata or {}
        is_enabled, _ = _is_2fa_enabled(user_metadata)
        return success_response(
            request=request,
            message_key="success.ok",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=Check2FAStatusResponse(two_fa_enabled=is_enabled),
        )

    except HTTPException as error:
        raise error
    except Exception as error:
        logger.error("Error checking 2FA status: %s", str(error))
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        ) from error
