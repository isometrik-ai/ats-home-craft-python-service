

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
    extract_user_context,
    format_iso_datetime,
    validate_pagination_params,
)

# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    CreateSessionResponse,
    SessionItem,
    SessionQueryParams,
    SessionsResponse,
    UpdateSessionRequest,
    UpdateSessionResponse
)

from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError

from apps.user_service.app.schemas.auth import SessionFilter

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_db.postgres_db.user_service_operations.session_operations import (
    create_session,
    get_session_by_id,
    update_session,
    check_session_exists,
    get_sessions_list,
    get_sessions_count
)


# Create router for sessions endpoints
router = APIRouter(prefix="/sessions", tags=["Sessions Management"])

# Initialize logger for sessions module
logger = get_logger("sessions-api")
logger.info("Sessions API module loaded")

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
    return session_id




async def get_client_ip(request: Request) -> str:
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


async def get_user_agent(request: Request) -> str:
    """
    Extract user agent from request headers.

    """
    return request.headers.get("User-Agent", "unknown")


async def get_device_fingerprint(request: Request) -> str:
    """
    Extract device fingerprint from request headers.

    """
    return request.headers.get("X-Device-Fingerprint")


async def get_risk_score(request: Request) -> int:
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


async def get_login_method(request: Request) -> str:
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




async def _extract_session_data_from_request(request: Request) -> dict:
    """Extract all session data from request headers."""
    return {
        "ip_address": await get_client_ip(request),
        "user_agent": await get_user_agent(request),
        "device_fingerprint": await get_device_fingerprint(request),
        "risk_score": await get_risk_score(request),
        "login_method": await get_login_method(request),
    }




@router.post(
    "", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "hipaa",  # Session tracking is critical for HIPAA compliance and audit trails
        "soc2_audit",  # Session management is essential for SOC2 compliance
        "audit_required",  # Session creation must be logged for security audits
    ],
    table_name="user_sessions",
    category="SESSION",
)
async def start_session(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Create a new user session (Optimized & Truly Async)

    Args:
        request (Request): The FastAPI request object (contains headers with session data)
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)

    Returns:
        CreateSessionResponse: Created session information with all details

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info("POST /sessions request started - Request ID: %s, ",request_id)
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug("User context extracted - Request ID: %s, ",request_id)
    logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    try:
        # Extract session ID from JWT token
        session_id = extract_session_id_from_token(current_user)
        logger.debug("Session ID extracted from token - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, ",session_id)

        # Set audit context for session creation
        request.state.audit_table = "user_sessions"
        request.state.audit_requested_id = session_id
        request.state.audit_description = (
            f"Created new session for user: {user_context.email}"
        )
        request.state.audit_risk_level = "medium"
        logger.debug("Audit context set for session creation - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, User Email: %s",session_id,user_context.email)
    except HTTPException as e:
        # Set audit data for failed attempt
        request.state.raw_audit_new_data = {
            "user_id": user_context.user_id,
            "organization_id": user_context.organization_id,
            "error": str(e.detail)
        }
        raise

    # Check if session already exists
    try:
        session_exists = await check_session_exists(session_id, user_context.organization_id)
        logger.debug("Session existence check completed - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, Session exists: %s",session_id,session_exists)

        if session_exists:
            logger.warning("Session already exists - Request ID: %s, ",request_id)
            logger.warning("Session ID: %s, ",session_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Session already exists",
            )
    except DatabaseOperationError as e:
        # Set audit data for database error
        request.state.raw_audit_new_data = {
            "session_id": session_id,
            "user_id": user_context.user_id,
            "organization_id": user_context.organization_id,
            "error": str(e)
        }
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

    # Extract client information from request headers
    session_data = await _extract_session_data_from_request(request)
    logger.debug("Session data extracted from request - Request ID: %s, ",request_id)
    logger.debug("IP Address: %s, ",session_data['ip_address'])
    logger.debug("User Agent: %s, ",session_data['user_agent'][:50])
    logger.debug("Risk Score: %s, ",session_data['risk_score'])
    logger.debug("Login Method: %s",session_data['login_method'])

    # Prepare session data for centralized operation
    session_data["session_id"] = session_id
    session_data["user_id"] = user_context.user_id

    try:
        # Create the session using centralized operation
        created_session = await create_session(session_data, user_context.organization_id)
        logger.debug("Session record created in database - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, User ID: %s",session_id,user_context.user_id)

        # Set audit context with new session data
        request.state.raw_audit_new_data = {
            "session_id": session_id,  # Use the session_id we already have
            "user_id": user_context.user_id,
            "organization_id": user_context.organization_id,
            "ip_address": session_data["ip_address"],
            "user_agent": session_data["user_agent"],
            "device_fingerprint": session_data["device_fingerprint"],
            "risk_score": session_data["risk_score"],
            "login_method": session_data["login_method"],
            "accessed_phi": False,
            "phi_access_purpose": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    except DatabaseOperationError as e:
        # Set audit data for database error
        request.state.raw_audit_new_data = {
            "session_id": session_id,
            "user_id": user_context.user_id,
            "organization_id": user_context.organization_id,
            "error": str(e)
        }
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

    # Format session data for response
    session_item = SessionItem(
        id=str(created_session["id"]),
        user_id=str(created_session["user_id"]),
        organization_id=str(created_session["organization_id"]),
        ip_address=str(created_session["ip_address"]),
        user_agent=created_session["user_agent"],
        device_fingerprint=created_session["device_fingerprint"],
        risk_score=created_session["risk_score"],
        login_timestamp=format_iso_datetime(created_session["login_timestamp"]) or "",
        logout_timestamp=format_iso_datetime(created_session["logout_timestamp"]),
        session_status=created_session["session_status"],
        login_method=created_session["login_method"],
        accessed_phi=created_session["accessed_phi"],
        phi_access_purpose=created_session["phi_access_purpose"],
    )

    logger.info("POST /sessions request completed successfully - Request ID: %s, ",request_id)
    logger.info("Session ID: %s, User ID: %s, ",session_id,user_context.user_id)
    logger.info(
        "IP Address: %s, Risk Score: %s, ",
        session_data['ip_address'],session_data['risk_score']
    )
    logger.info("Status Code: 201")

    return CreateSessionResponse(
        status_code=status.HTTP_201_CREATED,
        message="Session created successfully",
        session=session_item,
    )


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
        login_timestamp=format_iso_datetime(session_data["login_timestamp"]) or "",
        logout_timestamp=format_iso_datetime(session_data["logout_timestamp"]),
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

    # Get sessions using centralized operations
    sessions_data = await get_sessions_list(
        organization_id=user_context.organization_id,
        filters=filters
    )

    # Get total count using centralized operation
    total_count = await get_sessions_count(
        organization_id=user_context.organization_id,
        filters=filters
    )

    return sessions_data, total_count


@router.put(
    "/logout",
    response_model=UpdateSessionResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "hipaa",
        "soc2_audit",
        "audit_required",
    ],
    table_name="user_sessions",
    category="SESSION",
)
async def update_session_logout(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
):
    """
    Update session logout information (Optimized & Truly Async)
    """
    request_id = str(uuid.uuid4())
    logger.info("PUT /sessions/logout request started - Request ID: %s, ",request_id)
    logger.info("User ID: %s, ",current_user.get('user_id'))

    try:
        session_id = extract_session_id_from_token(current_user)
        logger.debug("Session ID extracted from token - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, ",session_id)

        # Extract and validate user context from JWT token
        user_context = extract_user_context(current_user)
        logger.debug("User context extracted - Request ID: %s, ",request_id)
        logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    except HTTPException as e:
        if "Session ID not found" in str(e.detail):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session ID not found in token"
            )
        raise

    # Check permission using utility function
    # await require_permission(
    #     permission_code="settings.sessions.manage",
    #     user_context=user_context,
    #     db_conn=db_conn,
    #     action_description="update sessions",
    # )

    # Set audit context for session update
    request.state.audit_table = "user_sessions"
    request.state.audit_requested_id = session_id
    request.state.audit_description = "Updated session logout information"
    request.state.audit_risk_level = "medium"
    logger.debug("Audit context set for session logout update - Request ID: %s, ",request_id)
    logger.debug("Session ID: %s, ",session_id)

    # Check if session exists in organization and get current data
    try:
        existing_session = await get_session_by_id(session_id, user_context.organization_id)

        if not existing_session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or access denied",
            )
    except DatabaseOperationError as e:
        # Set audit data for database error
        request.state.raw_audit_new_data = {
            "session_id": session_id,
            "user_id": user_context.user_id,
            "organization_id": user_context.organization_id,
            "error": str(e)
        }
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e
    logger.debug("Existing session retrieved - Request ID: %s, ",request_id)
    logger.debug("Session ID: %s, Session Status: %s",session_id,existing_session['session_status'])

    # Set old values for audit comparison
    request.state.raw_audit_old_data = {
        "session_id": session_id,
        "session_status": existing_session["session_status"],
        "logout_timestamp": (
            existing_session["logout_timestamp"].isoformat()
            if existing_session["logout_timestamp"]
            else None
        ),
        "accessed_phi": existing_session["accessed_phi"],
        "phi_access_purpose": existing_session["phi_access_purpose"],
        "organization_id": user_context.organization_id,
    }

    # Create default logout data
    logout_data = UpdateSessionRequest(
        session_status="inactive",
        accessed_phi=False,  # Default to False, can be updated later if needed
        phi_access_purpose=None,
        logout_reason="user_logout",
    )
    logger.debug("Logout data prepared - Request ID: %s, ",request_id)
    logger.debug("Session ID: %s, New Status: inactive",session_id)

    try:
        # Build update query using utility function
        # Update session using centralized operation
        updated_session = await update_session(
            session_id, user_context.organization_id, logout_data
        )
        logger.debug("Session logout updated in database - Request ID: %s, ",request_id)
        logger.debug("Session ID: %s, Update successful: %s",session_id,updated_session is not None)

        # Set new values for audit comparison
        request.state.raw_audit_new_data = {
            "session_id": session_id,
            "session_status": "inactive",
            "logout_timestamp": datetime.now(timezone.utc).isoformat(),
            "accessed_phi": False,
            "phi_access_purpose": None,
            "logout_reason": "user_logout",
            "organization_id": user_context.organization_id,
        }
    except DatabaseOperationError as e:
        # Set audit data for database error
        request.state.raw_audit_new_data = {
            "session_id": session_id,
            "session_status": existing_session.get("session_status", "active"),
            "organization_id": user_context.organization_id,
            "error": str(e)
        }
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) from e

    # Format updated session data for response
    session_item = SessionItem(
        id=str(updated_session["id"]),
        user_id=str(updated_session["user_id"]),
        organization_id=str(updated_session["organization_id"]),
        ip_address=str(updated_session["ip_address"]),
        user_agent=updated_session["user_agent"],
        device_fingerprint=updated_session["device_fingerprint"],
        risk_score=updated_session["risk_score"],
        login_timestamp=format_iso_datetime(updated_session["login_timestamp"]) or "",
        logout_timestamp=format_iso_datetime(updated_session["logout_timestamp"]),
        session_status=updated_session["session_status"],
        login_method=updated_session["login_method"],
        accessed_phi=updated_session["accessed_phi"],
        phi_access_purpose=updated_session["phi_access_purpose"],
    )

    logger.info("PUT /sessions/logout request completed successfully - Request ID: %s, ",request_id)
    logger.info("Session ID: %s, User ID: %s, ",session_id,user_context.user_id)
    logger.info("Old Status: %s, New Status: inactive, ",existing_session['session_status'])
    logger.info("Status Code: 200")

    return UpdateSessionResponse(
        status_code=status.HTTP_200_OK,
        message="Session logout updated successfully",
        session=session_item,
    )


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
async def get_sessions(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    query_params: SessionQueryParams = Depends(),
):
    """
    Get all sessions for the current organization (Optimized & Truly Async)


    Args:
        request (Request): The FastAPI request object
        current_user (dict): Decoded JWT token containing user information
        db_conn: AsyncPG database connection (truly async)
        query_params (SessionQueryParams): Query parameters object containing
            search, pagination, and filter options

    Returns:
        SessionsResponse: List of sessions with pagination information

    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info("GET /sessions request started - Request ID: %s, ",request_id)
    logger.info("User ID: %s, ",current_user.get('user_id'))
    logger.info("Organization ID: %s, ",current_user.get('organization_id'))
    logger.info("Search: %s, Session Status: %s, ",query_params.search,query_params.session_status)
    logger.info("Login Method: %s, Page: %s, ",query_params.login_method,query_params.page)
    logger.info("Page Size: %s",query_params.page_size)


    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug("User context extracted - Request ID: %s, ",request_id)
    logger.debug("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)

    # Check permission using utility function
    # await require_permission(
    #     permission_code="settings.sessions.manage",
    #     user_context=user_context,
    #     db_conn=db_conn,
    #     action_description="view sessions",
    # )

    # Set audit context for session list access
    request.state.audit_table = "user_sessions"
    request.state.audit_description = (
        f"Admin accessed session list with search: '{query_params.search or 'none'}'"
    )
    request.state.audit_risk_level = "medium"
    logger.debug("Audit context set for session list access - Request ID: %s, ",request_id)
    logger.debug("Search: %s, ",query_params.search or 'none')

    # Validate pagination parameters and calculate offset
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )
    logger.debug("Pagination parameters validated - Request ID: %s, ",request_id)
    logger.debug("Page: %s, Page Size: %s, Offset: %s",page,page_size,offset)

    # Fetch sessions data and count
    sessions_data, total_count = await _fetch_sessions_data(
        user_context, query_params, page_size, offset
    )
    logger.debug("Sessions data fetched - Request ID: %s, ",request_id)
    logger.debug("Sessions count: %s, Total count: %s",len(sessions_data),total_count)

    # Format sessions data using utility functions
    sessions = [_format_session_item(session) for session in sessions_data]
    logger.debug("Sessions data formatted - Request ID: %s, ",request_id)
    logger.debug("Formatted sessions count: %s",len(sessions))

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

    logger.info("GET /sessions request completed successfully - Request ID: %s, ",request_id)
    logger.info("Sessions Count: %s, Total Count: %s, ",len(sessions),total_count)
    logger.info("Page: %s, Page Size: %s, Status Code: 200",page,page_size)

    return SessionsResponse(
        status_code=status.HTTP_200_OK,
        message=message,
        sessions=sessions,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )
