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
import uuid
import json

# Third-party imports
from fastapi import APIRouter, HTTPException, status, Body, Request

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
    SignupResponse,
    UserInfo,
    AuthResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse
)

# App instance
from apps.user_service.app.app_instance import limiter

# Shared library imports
from libs.shared_middleware.jwt_auth import get_user_from_token

# Email utilities
from libs.shared_utils.email_utils import send_password_reset_confirmation_email

from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    get_auth_user_by_email,
    get_organization_member_status_by_email
)
from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    login_user,
    sign_up_supabase_user,
    reset_the_password_email,
    update_password_with_token,
    log_exception,
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
logger.info("Auth API module loaded")


# ============================================================================
# SIGNUP HELPER FUNCTIONS
# ============================================================================


def _prepare_signup_response_data(
    # organization_id: str,
    user_id: str,
    # organization_name: str,
    # slug: str,
    signup_data: SignupRequest
) -> dict:
    """Prepare response data for successful signup."""
    return {
        # "organization_id": organization_id,
        "user_id": user_id,
        # "organization_name": organization_name,
        # "user_email": signup_data.user_data.email,
        # "account_type": signup_data.account_type.value,
        # "plan_type": signup_data.plan_type.value,
        # "slug": slug,
        # "status": "trial",
        # "role_name": "Super Admin",
        # "max_users": _get_max_users_for_plan(signup_data.plan_type.value),
    }

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
            user=UserInfo(
                id=result.user.id,
                email=result.user.email,
                # full_name=result.user.user_metadata.get("full_name", ""),
                first_name=result.user.user_metadata.get("first_name", None),
                last_name=result.user.user_metadata.get("last_name", None),
            ),
        )
    except Exception as error:
        if "Invalid login credentials" in str(error):
            raise HTTPException(
                status_code=401, detail="Invalid login credentials"
            ) from error
        raise HTTPException(status_code=500, detail="Authentication failed") from error


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
            "detail": "Email not found in our system. Please check your email address and try again."
        }
    """
    logger.info("=== FORGOT PASSWORD DEBUG START ===")

    try:
        # First, check if email exists in auth.users table
        logger.info("Checking if email exists in auth.users...")
        user = await get_auth_user_by_email(data.email)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Email not found in our system. Please check your email address and try again."
            )

        # Send password reset email only if user exists
        await reset_the_password_email(data.email)
        logger.info("Password reset email sent successfully")
        return ForgotPasswordResponse(
            status_code=status.HTTP_200_OK,
            message="Password reset email sent successfully. Please check your email."
        )
    except HTTPException as error:
        raise error
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
async def reset_password(
    request: Request,
    data: ResetPasswordRequest):
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
        user = await get_user_from_token(data.token)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        if not _is_password_strong(data.new_password):
            raise HTTPException(
                status_code=400,
                detail="Password must be at least 6 characters long and "
                "contain at least one uppercase letter, one lowercase letter, "
                "one number, and one special character."
            )

        result = await update_password_with_token(user['sub'], data.new_password)
        logger.info(f"update_password_with_token result: {result}")
        if result.user:
            logger.info("Password updated successfully")

            # Send confirmation email to user
            user_email = user.get('email', '')
            user_name = user.get('user_metadata', {}).get('full_name', '') or user.get('email', '').split('@')[0]

            try:
                email_sent = send_password_reset_confirmation_email(user_email, user_name)
                if email_sent:
                    logger.info("Password reset confirmation email sent successfully to %s", user_email)
                else:
                    logger.warning("Failed to send password reset confirmation email to %s", user_email)
                    # Note: We don't fail the entire operation if email fails
                    # The password reset was successful, only the email notification failed
            except Exception as email_error:
                logger.error("Error sending password reset confirmation email: %s", str(email_error))
                # Note: We don't fail the entire operation if email fails

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

    except Exception as error:
        log_exception()
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
    request: Request,
    signup_data: SignupRequest = Body(...),
):
    """
    User signup endpoint for both personal and business accounts

    This endpoint creates a complete account setup including:
    1. User signup with Supabase Auth
    X 2. Organization creation based on account type
    X 3. Super Admin role and permissions setup
    X 4. Organization member creation with role assignment

    Account Types:
    - Personal: Individual account for freelancers, students, personal use
    - Business: Corporate account for companies, teams, organizations

    Features:
    - Email validation and duplicate checking
    - Password strength requirements (minimum 6 characters)
    X - Organization slug generation with uniqueness validation
    X - Trial status for new organizations
    X - Automatic Super Admin role assignment
    X - Complete permission system setup

    Args:
        signup_data (SignupRequest): Signup data including user info and optionally company info

    Returns:
        SignupResponse: Success response with organization and user data

    Raises:
        HTTPException: 400 for validation errors
        HTTPException: 409 for duplicate email or organization slug
        HTTPException: 500 for database or Supabase errors

    Security Features:
    - Password hashing handled by Supabase
    - Email validation and uniqueness checking
    X- Organization slug uniqueness validation
    - Transaction rollback on failures
    - Proper error handling without exposing internal details
    """
    # Generate request ID and initialize audit context
    request_id = str(uuid.uuid4())

    if not _is_password_strong(signup_data.user_data.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 6 characters long and "
            "contain at least one uppercase letter, one lowercase letter, "
            "one number, and one special character."
        )

    user_id = await sign_up_supabase_user(signup_data)
    print(f"Created Supabase user: {user_id}")

    logger.info(
        "POST /auth/signup request completed successfully - Request ID: %s, ",
        request_id
    )
    # logger.info("Organization ID: %s, User ID: %s, ",organization_id,user_id)
    logger.info("Email: %s, Status Code: 201",signup_data.user_data.email)

    return SignupResponse(
        status_code=status.HTTP_201_CREATED,
        message="Account created successfully! Please check your email for verification.",
        data=_prepare_signup_response_data(
            user_id=user_id,
            signup_data=signup_data
        )
    )


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

def _extract_user_type_strict(row) -> str|None:
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
            return _response_found(status_value)
    return _get_not_found_response()


@handle_api_exceptions("delete user")
@router.delete("/user/{user_id}", status_code=status.HTTP_200_OK)
# pylint: disable=unused-argument  # Required by @limiter.limit
async def delete_user(
    request: Request,
    user_id: str
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

        result = await delete_auth_user(user_id)

        if result is not None:
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
        logger.error("Failed to delete user %s: %s", user_id, error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(error)}"
        ) from error