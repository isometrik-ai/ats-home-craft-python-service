"""Sessions Management API Module
This module provides endpoints for managing user sessions.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import SessionItem
from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    extract_user_context,
    format_iso_datetime,
)
from libs.shared_db.postgres_db.user_service_operations.session_operations import (
    get_org_sessions_with_count,
    get_sessions_with_count,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE, SETTINGS_USERS_VIEW
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.response_factory import list_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/sessions", tags=["Sessions Management"])

logger = get_logger("sessions-api")


def _format_session_item(session_data: dict) -> SessionItem:
    """Format session data into SessionItem."""
    return SessionItem(
        id=str(session_data["id"]),
        user_id=str(session_data["user_id"]),
        organization_id=str(session_data["organization_id"]),
        ip_address=str(session_data["ip_address"]),
        user_agent=session_data["user_agent"],
        device_fingerprint=session_data["device_fingerprint"],
        risk_score=session_data["risk_score"],
        login_timestamp=(
            session_data["login_timestamp"]
            if isinstance(session_data["login_timestamp"], str)
            else format_iso_datetime(session_data["login_timestamp"]) or ""
        ),
        logout_timestamp=(
            session_data["logout_timestamp"]
            if isinstance(session_data["logout_timestamp"], str)
            else format_iso_datetime(session_data["logout_timestamp"]) or ""
        ),
        session_status=session_data["session_status"],
        login_method=session_data["login_method"],
        accessed_phi=session_data["accessed_phi"],
        phi_access_purpose=session_data["phi_access_purpose"],
    )


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
    """Get all sessions for the current organization"""
    # Extract user context from JWT token
    user_context = await extract_user_context(current_user)

    if user_context.organization_id:
        await check_permissions(
            current_user=current_user,
            permission_codes=SETTINGS_SYSTEM_MANAGE,
        )

    # Create SessionFilter from query_params
    filters = SessionFilter(
        search=search,
        session_status=session_status,
        login_method=login_method,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    # Get sessions and count in a single optimized database call
    result = await get_sessions_with_count(
        organization_id=user_context.organization_id,
        user_id=user_context.user_id,
        filters=filters,
    )

    sessions_data = result["data"]
    total_count = result["total_count"]

    # Format sessions data using utility functions
    sessions = [_format_session_item(session) for session in sessions_data]

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
    # Extract context and enforce permissions in a single helper call
    user_context = await check_permissions(
        current_user=current_user,
        permission_codes=SETTINGS_USERS_VIEW,
    )

    # Require organization_id – org-wide listing doesn't apply to personal accounts
    if not user_context.organization_id:
        raise BadRequestException(
            message_key="sessions.errors.bad_request",
            custom_code=CustomStatusCode.BAD_REQUEST,
        )

    # Fetch sessions data and count
    filters = SessionFilter(
        search=search,
        session_status=session_status,
        login_method=login_method,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    result = await get_org_sessions_with_count(
        organization_id=user_context.organization_id,
        filters=filters,
    )

    sessions_data = result["data"]
    total_count = result["total_count"]

    if not sessions_data:
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

    # Format sessions data using utility functions
    sessions = [_format_session_item(session) for session in sessions_data]

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
