"""Sessions Management API Module
This module provides endpoints for managing user sessions.
"""

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.services.session_service import SessionService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
    require_organization_creator,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE, SETTINGS_USERS_VIEW
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/sessions", tags=["Sessions Management"])

logger = get_logger("sessions-api")


@handle_api_exceptions("get sessions list")
@router.get(
    "",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get all sessions for the current organization",
    summary="Get all sessions for the current organization",
    responses={
        http_status.HTTP_200_OK: {"description": "Sessions retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_sessions_list(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    search: str | None = Query(
        None,
        description="Search term to filter sessions by user email or IP address (case-insensitive)",
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    session_status: str | None = Query(
        None, description="Filter by session status (active, inactive, terminated)"
    ),
    login_method: str | None = Query(
        None, description="Filter by login method (password, sso, mfa)"
    ),
):
    """Get all sessions for the current organization."""
    # Extract user context from JWT token
    user_context = await extract_user_context(current_user, db_connection)

    if user_context.organization_id:
        await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=SETTINGS_SYSTEM_MANAGE,
        )

    # Create SessionFilter from query params
    filters = SessionFilter(
        search=search,
        session_status=session_status,
        login_method=login_method,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    # Create service and delegate to service
    session_service = SessionService(user_context=user_context, db_connection=db_connection)
    result = await session_service.get_user_sessions(filters=filters)

    sessions = result["sessions"]
    total_count = result["total_count"]

    if not sessions:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=sessions,
        total=total_count,
        page=page,
        page_size=page_size,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get organization sessions")
@router.get(
    "/all",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Get all sessions for all users in the current organization",
    summary="Get all sessions for all users in the current organization",
    responses={
        http_status.HTTP_200_OK: {"description": "Sessions retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_organization_sessions(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_conn),
    search: str | None = Query(
        None,
        description="Search term to filter sessions by user email or IP address (case-insensitive)",
    ),
    page: int = Query(1, ge=1, description="The page number for pagination"),
    page_size: int = Query(20, ge=1, le=100, description="The number of items per page"),
    session_status: str | None = Query(
        None, description="Filter by session status (active, inactive, terminated)"
    ),
    login_method: str | None = Query(
        None, description="Filter by login method (password, sso, mfa)"
    ),
):
    """Get all sessions for all users in the current organization.
    Intended for org-level admins with settings management permission.
    """
    # Extract context and enforce permissions
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_USERS_VIEW,
    )

    # Create SessionFilter from query params
    filters = SessionFilter(
        search=search,
        session_status=session_status,
        login_method=login_method,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    # Create service and delegate to service
    session_service = SessionService(user_context=user_context, db_connection=db_connection)
    result = await session_service.get_organization_sessions(filters=filters)

    sessions = result["sessions"]
    total_count = result["total_count"]

    if not sessions:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=sessions,
        total=total_count,
        page=page,
        page_size=page_size,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("revoke session")
@router.delete(
    "/{session_id}",
    response_model=None,
    status_code=http_status.HTTP_200_OK,
    description="Revoke a specific session at Supabase level (admin operation)",
    summary="Revoke session",
    responses={
        http_status.HTTP_200_OK: {"description": "Session revoked successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("20/minute")
async def revoke_session(
    session_id: str,
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Revoke a specific session at Supabase DB level.

    This endpoint deletes a row from `auth.sessions` for the given `session_id`.
    """
    # Use the standard pattern: permission check returns a validated UserContext
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
    )
    await require_organization_creator(
        user_context=user_context,
        organization_id=user_context.organization_id,
        db_connection=db_connection,
    )

    session_service = SessionService(user_context=user_context, db_connection=db_connection)

    # Prevent using this endpoint to revoke the *current* session; use regular signout on client.
    if str(current_user.get("session_id")) == str(session_id):
        raise BadRequestException(
            message_key="sessions.errors.cannot_revoke_current_session",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Ensure target session belongs to the same organization as requester.
    target_org_id = await session_service.session_repository.get_session_organization_id(session_id)
    if not target_org_id:
        raise NotFoundException(
            message_key="auth.errors.session_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    if str(target_org_id) != str(user_context.organization_id):
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    await session_service.revoke_session_by_id(session_id=session_id)

    return success_response(
        request=request,
        message_key="sessions.success.session_revoked",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data={"revoked": True, "session_id": session_id},
    )
