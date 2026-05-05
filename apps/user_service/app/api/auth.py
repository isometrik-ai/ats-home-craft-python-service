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
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    PasswordResponse,
    RefreshSessionResponse,
    ResetPasswordRequest,
    SelectOrganizationRequest,
    SelectOrganizationResponse,
    SetPasswordRequest,
    SignupRequest,
    ValidateAccountRequest,
    ValidateAccountResponse,
    ValidateTokenResponse,
)
from apps.user_service.app.services.auth_service import AuthService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.http_exceptions import UnauthorizedException
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
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    data: SetPasswordRequest = Body(...),
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
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
):
    """Reset user password using token from email"""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.reset_password(
        user_id=current_user["sub"],
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
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
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


@handle_api_exceptions("validate_account")
@router.post(
    "/validate/account",
    response_model=ValidateAccountResponse,
    description="Validate user account credentials and check if 2FA is enabled.",
    summary="Validate user account credentials and check if 2FA is enabled.",
    status_code=http_status.HTTP_200_OK,
    responses={
        http_status.HTTP_200_OK: {"description": "User account validated and 2FA is enabled"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
async def validate_account(
    request: Request,
    data: ValidateAccountRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_anon),
):
    """Validate user account credentials and check if 2FA is enabled."""
    auth_service = AuthService(db_connection=db_connection, sb_client=sb_client)
    result = await auth_service.validate_account(
        trigger=data.trigger,
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


@handle_api_exceptions("select_organization")
@router.post(
    "/select-org",
    response_model=SelectOrganizationResponse,
    status_code=http_status.HTTP_200_OK,
    description="Select organization for the current user session",
    summary="Select organization for the current user session",
    responses={
        http_status.HTTP_200_OK: {"description": "Organization selected successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "User is not a member of the organization"},
        http_status.HTTP_409_CONFLICT: {
            "description": "Session already has an organization linked"
        },
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
async def select_organization(
    request: Request,
    data: SelectOrganizationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Select organization for the current user session.

    This endpoint allows a user to select an organization for their session.
    It validates that:
    1. The user is a member of the organization (or has active client_user when type=client)
    2. The session is not already linked with an organization
    3. Updates the session with the selected organization_id
    4. Returns isometrik details for the organization
    """
    auth_service = AuthService(db_connection=db_connection)

    user_id = current_user.get("sub")
    session_id = current_user.get("session_id")

    result = await auth_service.select_organization(
        user_id=user_id,
        session_id=session_id,
        organization_id=data.organization_id,
        user_type=data.user_type,
    )

    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result.model_dump(exclude_none=True),
    )


@handle_api_exceptions("validate_token")
@router.get(
    "/validate",
    response_model=ValidateTokenResponse,
    status_code=http_status.HTTP_200_OK,
    description="Validate authentication token and return organization_id",
    summary="Validate authentication token and return organization_id",
    responses={
        http_status.HTTP_200_OK: {"description": "Token validated successfully"},
        http_status.HTTP_401_UNAUTHORIZED: {
            "description": "Unauthorized - Invalid token or no organization associated"
        },
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
async def validate(
    request: Request,
    _current_user: dict = Depends(get_user_from_auth),
):
    """Validate authentication token and return organization_id.

    This endpoint validates the authentication token and returns the organization_id
    associated with the current session. This is useful as an external endpoint
    for authentication validation.

    Raises:
        UnauthorizedException: If organization_id is not associated with the session

    Returns:
        ValidateTokenResponse: Contains organization_id
    """
    # Get organization_id from request.state (already fetched by get_user_from_auth)
    audit_context = getattr(request.state, "audit_user_context", None)
    organization_id = audit_context.get("organization_id") if audit_context else None

    if not organization_id:
        raise UnauthorizedException(
            message_key="auth.errors.session_cannot_access_organization",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )

    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=ValidateTokenResponse(organization_id=organization_id),
    )
