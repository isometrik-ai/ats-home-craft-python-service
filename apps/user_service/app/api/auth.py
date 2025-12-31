"""Authentication API Module
This module provides authentication operations using Supabase.
Includes login and signup functionality with proper error handling.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import (
    supabase_anon,
    supabase_anon_client_with_headers,
    supabase_service,
)
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
    RefreshSessionResponse,
    ResetPasswordRequest,
    SetPasswordRequest,
    SignupRequest,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from apps.user_service.app.services.auth_service import AuthService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/auth", tags=["Authentication"])


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
async def login(
    request: Request,
    data: AuthLogin,
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_anon_client_with_headers),
):
    """User login endpoint with optional 2FA support."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)

    result = await auth_service.login(data=data)

    return success_response(
        request=request,
        message_key="auth.success.login_successful",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("refresh")
@router.put(
    "/refresh",
    response_model=RefreshSessionResponse,
    status_code=http_status.HTTP_200_OK,
    description="Refresh user session",
    summary="Refresh user session",
    responses={
        http_status.HTTP_200_OK: {"description": "Session refresh response"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("100/minute")
async def refresh(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_anon),
):
    """Refresh user session."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)

    access_token = request.headers.get("Access-Token")
    refresh_token = request.headers.get("Refresh-Token")

    result = await auth_service.refresh_session(
        access_token=access_token,
        refresh_token=refresh_token,
    )

    # Use different message based on whether token was refreshed
    message_key = (
        "auth.success.session_refreshed"
        if result.token_refreshed
        else "auth.success.token_not_expired"
    )

    return success_response(
        request=request,
        message_key=message_key,
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


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
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Set password for user Signed Up from Google or Magic Link."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.set_password(
        user_id=current_user["sub"],
        password=data.password,
    )
    return success_response(
        request=request,
        message_key="auth.success.password_set_successfully",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=result,
    )


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
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_anon),
):
    """Send password reset email to user (only if email exists in system)"""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.forgot_password(email=data.email)
    return success_response(
        request=request,
        message_key="auth.success.password_reset_email_sent",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


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
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Reset user password using token from email"""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.reset_password(
        token=data.token,
        new_password=data.new_password,
    )
    return success_response(
        request=request,
        message_key="auth.success.password_reset_successfully",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("change_password")
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
async def change_password(
    request: Request,
    data: ChangePasswordRequest = Body(...),
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Change user password endpoint."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.change_password(
        user_id=current_user.get("sub"),
        user_email=current_user.get("email"),
        current_password=data.current_password,
        new_password=data.new_password,
        user_metadata=current_user.get("user_metadata", {}),
    )
    return success_response(
        request=request,
        message_key="auth.success.password_changed_successfully",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


@handle_api_exceptions("signup")
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
async def signup(
    request: Request,
    signup_data: SignupRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_anon),
):
    """User signup endpoint for both personal and business accounts
    This endpoint creates a complete account setup including User signup with Supabase Auth
    """
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.signup(signup_data)
    return success_response(
        request=request,
        message_key="auth.success.user_signed_up",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_201_CREATED,
        data=result,
    )


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
async def verify_email(
    request: Request,
    body: VerifyEmailRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
):
    """Verify user email and status by determining user type from auth.users metadata
    and checking the corresponding table for status."""
    auth_service = AuthService(db_connection=db_connection)
    result = await auth_service.verify_email(body.email)
    return success_response(
        request=request,
        message_key="auth.success.email_verified",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )


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
    db_connection: asyncpg.Connection = Depends(db_uow),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Delete user directly from auth.users table without validation."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    user_id = current_user["sub"]
    await auth_service.delete_user(user_id)
    return success_response(
        request=request,
        message_key="auth.success.user_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("check_2fa_status")
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
async def check_2fa_status(
    request: Request,
    data: Check2FAStatusRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_anon),
):
    """Check if 2FA is enabled for a user account."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.check_2fa_status(
        email=data.email,
        password=data.password,
    )
    return success_response(
        request=request,
        message_key="success.ok",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result,
    )
