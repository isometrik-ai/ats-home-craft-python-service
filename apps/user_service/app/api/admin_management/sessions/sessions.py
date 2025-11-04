

"""
Sessions Management API Module

This module provides CRUD operations for user session management.

"""
from typing import Optional
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

# Utility imports
from apps.user_service.app.dependencies.common_utils import (
    format_iso_datetime,
    validate_pagination_params,
    check_permissions,
)

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    SessionItem,
    SessionQueryParams,
    SessionsResponse
)

from apps.user_service.app.schemas.auth import SessionFilter

from apps.user_service.app.app_instance import limiter


# Local imports
from libs.shared_utils.common_query import SETTINGS_SYSTEM_MANAGE
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.postgres_db.user_service_operations.session_operations import (
    get_sessions_with_count
)


# Create router for sessions endpoints
router = APIRouter(prefix="/sessions", tags=["Sessions Management"])

# Initialize logger for sessions module
logger = get_logger("sessions-api")

# Authentication description for API documentation
AUTH_DESCRIPTION = "Bearer token required for authentication"


class SessionResponse(BaseModel):
    """Response model for session operations"""

    message: str
    status: str = "success"

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {"message": self.message, "status": self.status}


def extract_session_id_from_token(current_user: dict) -> str:
    """
    Extract session ID from JWT token.
    This function extracts the session ID from the JWT token's 'session_id' claim.
    The session ID is used to track and manage user sessions.

    """
    session_id = current_user.get("session_id")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session ID not found in token",
        )

    # Validate that session_id is not None or empty string
    if session_id is None or session_id == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session ID is null or empty in token",
        )

    return session_id


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request.

    This function handles various proxy scenarios and extracts the real client IP.

    """
    # Check for forwarded headers (common with proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()

    # Check for real IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback to client host
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    """
    Extract user agent from request headers.

    """
    return request.headers.get("User-Agent", "unknown")


def get_device_fingerprint(request: Request) -> str:
    """
    Extract device fingerprint from request headers.

    """
    return request.headers.get("X-Device-Fingerprint")


def get_risk_score(request: Request) -> int:
    """
    Extract risk score from request headers or calculate based on context.

    """
    # Try to get from header first
    # risk_score_header = request.headers.get("X-Risk-Score")
    # if risk_score_header:
    #     try:
    #         return int(risk_score_header)
    #     except ValueError:
    #         pass

    # Calculate risk score based on context
    risk_score = 0

    # Check for suspicious headers
    if not request.headers.get("User-Agent"):
        risk_score += 20

    # Check for proxy/VPN indicators
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP"):
        risk_score += 10

    return min(risk_score, 100)


def get_login_method(request: Request) -> str:
    """
    Determine login method from request context.

    """
    # Check for MFA headers
    if request.headers.get("X-MFA-Token"):
        return "mfa"

    # Check for SSO headers
    if request.headers.get("X-SSO-Provider"):
        return "sso"

    # Default to password-based login
    return "password"


def build_session_filter_message(
    search: Optional[str] = None,
    session_status: Optional[str] = None,
    login_method: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """
    Build response message based on applied filters.

    """
    filters = []

    if search:
        filters.append(f"search='{search}'")
    if session_status:
        filters.append(f"status='{session_status}'")
    if login_method:
        filters.append(f"login_method='{login_method}'")

    filter_text = f" with filters: {', '.join(filters)}" if filters else ""

    return f"Sessions retrieved successfully (page {page}, {page_size} per page){filter_text}"


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


async def _fetch_sessions_data(
    user_context, query_params, page_size: int, offset: int
):
    """Fetch sessions data and count using centralized operations."""
    # Create SessionFilter from query_params
    filters = SessionFilter(
        search=query_params.search,
        session_status=query_params.session_status,
        login_method=query_params.login_method,
        limit=page_size,
        offset=offset,
    )

    # Get sessions and count in a single optimized database call
    result = await get_sessions_with_count(
        organization_id=user_context.organization_id,
        user_id=user_context.user_id,
        filters=filters
    )

    return result["data"], result["total_count"]


@router.get("", response_model=SessionsResponse, status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "hipaa",  # Session access tracking is critical for HIPAA compliance
#         "soc2_audit",  # Session management is essential for SOC2 compliance
#         "audit_required",  # Session list access must be logged for security audits
#     ],
#     table_name="user_sessions",
#     category="SESSION",
# )
async def get_sessions_details(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: SessionQueryParams = Depends(),
):
    """
    Get all sessions for the current organization (Optimized & Truly Async)


    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        query_params (SessionQueryParams): Query parameters object containing
            search, pagination, and filter options

    Returns:
        SessionsResponse: List of sessions with pagination information

    """
    # # Generate request ID for tracking
    # request_id = str(uuid.uuid4())

    # Extract and validate user context from JWT token
    user_context = await check_permissions(current_user=current_user,
        permission_codes=SETTINGS_SYSTEM_MANAGE,
        action_description="view sessions list"
    )

    if not user_context.organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a member of any organization",
        )

    # Set audit context for session list access
    request.state.audit_table = "user_sessions"
    request.state.audit_description = (
        f"Admin accessed session list with search: '{query_params.search or 'none'}'"
    )
    request.state.audit_risk_level = "medium"

    # Validate pagination parameters and calculate offset
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )

    # Fetch sessions data and count
    sessions_data, total_count = await _fetch_sessions_data(
        user_context, query_params, page_size, offset
    )

    # Format sessions data using utility functions
    sessions = [_format_session_item(session) for session in sessions_data]

    # Build response message using utility function
    message = build_session_filter_message(
        search=query_params.search,
        session_status=query_params.session_status,
        login_method=query_params.login_method,
        page=page,
        page_size=page_size,
    )

    # Set audit data for session list access
    request.state.raw_audit_new_data = {
        "total_sessions": total_count,
        "page": page,
        "page_size": page_size,
        "filters_applied": {
            "search": query_params.search,
            "session_status": query_params.session_status,
            "login_method": query_params.login_method,
        },
    }

    return SessionsResponse(
        message=message,
        sessions=sessions,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )
