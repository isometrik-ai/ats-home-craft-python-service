"""Users Management API Module
This module provides CRUD operations for user management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.users import (
    UpdateUserEmailRequest,
    UpdateUserProfileRequest,
    UserListResponse,
)
from apps.user_service.app.services.user_service import UserService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
    set_audit_old_data_from_user,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_USERS_MANAGE
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/users", tags=["Users Management"])

logger = get_logger("users-api")


@handle_api_exceptions("get users list")
@router.get(
    "/list",
    response_model=UserListResponse,
    status_code=http_status.HTTP_200_OK,
    description="Get users list",
    summary="Get users list",
    responses={
        http_status.HTTP_200_OK: {"description": "Users list retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_users_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    search: str | None = Query(
        None, description="Search term to filter Users by name (case-insensitive)"
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
):
    """List all users in the current organization (paginated, sequential)"""
    # Check permissions
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.get_users_list(
        organization_id=user_context.organization_id,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    users = result["users"]
    total_count = result["total_count"]

    if not users:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=users,
        total=total_count,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("get user profile")
@router.get(
    "/profile",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get user profile",
    summary="Get user profile",
    responses={
        http_status.HTTP_200_OK: {"description": "User profile retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_user_profile(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
):
    """Retrieve the authenticated user's profile."""
    user_context = await extract_user_context(current_user)

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.get_user_profile_with_metadata(
        user_context.user_id, user_context.organization_id
    )

    # Handle case where user has no organization
    if not user_context.organization_id:
        return success_response(
            request=request,
            message_key="users.success.user_profile_retrieved",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            data=[],
        )

    # Set audit data from service
    request.state.raw_audit_new_data = result["audit_data"]

    return success_response(
        request=request,
        message_key="users.success.user_profile_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result["profile_data"],
    )


@handle_api_exceptions("update user email")
@router.put(
    "/{user_id}/email",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Update user email",
    summary="Update user email",
    responses={
        http_status.HTTP_200_OK: {"description": "User email updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Updating user email involves personal information
        "pii",  # Email updates contain personally identifiable information
        "audit_required",  # Email updates must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_EMAIL_UPDATE",
)
async def update_user_email(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: UpdateUserEmailRequest = Body(...),
    user_id: str = Path(..., description="The ID of the user to update"),
):
    """Update user email."""
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.update_user_email(user_id, user_context.organization_id, body.email)

    # Set old values for audit comparison
    set_audit_old_data_from_user(request, result["current_user_data"])

    return success_response(
        request=request,
        message_key="users.success.email_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("ban user")
@router.post(
    "/ban/{user_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Ban user",
    summary="Ban user",
    responses={
        http_status.HTTP_200_OK: {"description": "User banned successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Banning user involves personal information
        "pii",  # User banning contains personally identifiable information
        "audit_required",  # User banning must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_BAN",
)
async def ban_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    user_id: str = Path(..., description="The ID of the user to ban"),
):
    """Ban a user for a specified duration."""
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Set audit context
    request.state.audit_risk_level = "high"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin banned user: {user_id}"

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.ban_user(user_id, user_context.organization_id)

    # Set audit data from service
    set_audit_old_data_from_user(request, result["current_user_data"])
    request.state.raw_audit_new_data = result["audit_data"]

    return success_response(
        request=request,
        message_key="users.success.user_banned",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("unban user")
@router.post(
    "/unban/{user_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Unban user",
    summary="Unban user",
    responses={
        http_status.HTTP_200_OK: {"description": "User unbanned successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Unbanning user involves personal information
        "pii",  # User unbanning contains personally identifiable information
        "audit_required",  # User unbanning must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_UNBAN",
)
async def unban_user(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    user_id: str = Path(..., description="The ID of the user to unban"),
):
    """Unban a user by user ID."""
    user_context = await check_permissions(current_user, SETTINGS_USERS_MANAGE)

    # Set audit context
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin unbanned user: {user_id}"
    request.state.audit_risk_level = "medium"

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.unban_user(user_id, user_context.organization_id)

    # Set audit data from service
    set_audit_old_data_from_user(request, result["current_user_data"])
    request.state.raw_audit_new_data = result["audit_data"]

    return success_response(
        request=request,
        message_key="users.success.user_unbanned",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("update user profile")
@router.put(
    "/update",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Update user profile",
    summary="Update user profile",
    responses={
        http_status.HTTP_200_OK: {"description": "User profile updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "gdpr",  # Updating user profile involves personal information
        "pii",  # User profile updates contain personally identifiable information
        "audit_required",  # User updates must be logged for compliance and security audits
    ],
    table_name="organization_members",
    category="USER_UPDATE",
)
async def update_user_profile(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: UpdateUserProfileRequest = Body(...),
):
    """Update authenticated user's own profile information."""
    user_context = await extract_user_context(current_user)

    # Set audit context
    request.state.audit_risk_level = "low"
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_context.user_id
    request.state.audit_description = f"User updating their own profile: {user_context.user_id}"

    # Create service and delegate all business logic to service
    user_service = UserService(user_context=user_context, db_connection=db_connection)
    result = await user_service.update_user_profile(
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
        body=body,
    )

    # Set audit data from service
    set_audit_old_data_from_user(request, result["current_user_data"])
    request.state.raw_audit_new_data = result["audit_data"]

    return success_response(
        request=request,
        message_key="users.success.user_profile_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )
