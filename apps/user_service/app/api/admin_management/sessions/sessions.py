

"""
Sessions Management API Module

This module provides CRUD operations for user session management.

"""
from datetime import datetime
from typing import Optional
import uuid

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
    UpdateSessionResponse,
)

from apps.user_service.app.schemas.auth import SessionFilter

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth


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


async def check_session_exists(session_id: str, db_conn) -> bool:
    """
    Check if a session already exists in the database.

    """
    query = """
        SELECT EXISTS(
            SELECT 1 FROM public.user_sessions
            WHERE id = $1
        );
    """
    result = await db_conn.fetchval(query, session_id)
    return result


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

    # Check for missing device fingerprint
    if not request.headers.get("X-Device-Fingerprint"):
        risk_score += 15

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


def build_sessions_filter_query(
    organization_id: str,
    filters: SessionFilter,
) -> tuple[str, list]:
    """
    Build SQL query for filtering sessions with search and pagination.
    """
    base_query = """
        SELECT
            us.id, us.user_id, us.organization_id, us.ip_address, us.user_agent,
            us.device_fingerprint, us.risk_score, us.login_timestamp,
            us.logout_timestamp, us.session_status, us.login_method,
            us.accessed_phi, us.phi_access_purpose,
            om.email as user_email, om.full_name as user_name
        FROM public.user_sessions us
        LEFT JOIN public.organization_members om
            ON us.user_id = om.user_id AND us.organization_id = om.organization_id
        WHERE us.organization_id = $1
    """

    params = [organization_id]
    param_count = 1

    if filters.search:
        param_count += 1
        base_query += f"""
            AND (
                om.email ILIKE ${param_count} OR
                om.full_name ILIKE ${param_count}
            )
        """
        params.append(f"%{filters.search}%")

    if filters.session_status:
        param_count += 1
        base_query += f" AND us.session_status = ${param_count}"
        params.append(filters.session_status)

    if filters.login_method:
        param_count += 1
        base_query += f" AND us.login_method = ${param_count}"
        params.append(filters.login_method)

    param_count += 1
    limit_param = f"${param_count}"
    param_count += 1
    offset_param = f"${param_count}"

    base_query += f"""
        ORDER BY us.login_timestamp DESC
        LIMIT {limit_param} OFFSET {offset_param}
    """
    params.extend([filters.limit, filters.offset])

    return base_query, params


def build_sessions_count_query(
    organization_id: str,
    search: Optional[str] = None,
    session_status: Optional[str] = None,
    login_method: Optional[str] = None,
) -> tuple[str, list]:
    """
    Build SQL query for counting sessions with filters.


    """
    count_query = """
        SELECT COUNT(*) as total_count
        FROM public.user_sessions us
        LEFT JOIN public.organization_members om
            ON us.user_id = om.user_id AND us.organization_id = om.organization_id
        WHERE us.organization_id = $1
    """

    params = [organization_id]
    param_count = 1

    # Add search condition if provided
    if search:
        param_count += 1
        count_query += f"""
            AND (
                om.email ILIKE ${param_count} OR
                om.full_name ILIKE ${param_count}
            )
        """
        params.append(f"%{search}%")

    # Add session status filter if provided
    if session_status:
        param_count += 1
        count_query += f" AND us.session_status = ${param_count}"
        params.append(session_status)

    # Add login method filter if provided
    if login_method:
        param_count += 1
        count_query += f" AND us.login_method = ${param_count}"
        params.append(login_method)

    return count_query, params


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


async def check_session_exists_in_org(
    session_id: str, organization_id: str, db_conn
) -> dict:
    """
    Check if a session exists in the organization and return session data.


    """
    query = """
        SELECT
            us.id, us.user_id, us.organization_id, us.ip_address, us.user_agent,
            us.device_fingerprint, us.risk_score, us.login_timestamp,
            us.logout_timestamp, us.session_status, us.login_method,
            us.accessed_phi, us.phi_access_purpose
        FROM public.user_sessions us
        WHERE us.id = $1 AND us.organization_id = $2
    """

    session_data = await db_conn.fetchrow(query, session_id, organization_id)

    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or access denied",
        )

    return session_data


def build_session_update_query(
    session_data: UpdateSessionRequest, session_id: str, organization_id: str
) -> tuple[str, list]:
    """
    Build dynamic update query for session fields.

    Args:
        session_data (UpdateSessionRequest): Session update data
        session_id (str): Session ID to update
        organization_id (str): Organization ID for security

    Returns:
        tuple[str, list]: SQL query and parameters list
    """
    update_fields = []
    update_params = []
    param_count = 0

    # Always add logout_timestamp when updating session
    param_count += 1
    update_fields.append(f"logout_timestamp = ${param_count}")
    update_params.append(datetime.utcnow())

    if session_data.session_status is not None:
        param_count += 1
        update_fields.append(f"session_status = ${param_count}")
        update_params.append(session_data.session_status)

    if session_data.accessed_phi is not None:
        param_count += 1
        update_fields.append(f"accessed_phi = ${param_count}")
        update_params.append(session_data.accessed_phi)

    if session_data.phi_access_purpose is not None:
        param_count += 1
        update_fields.append(f"phi_access_purpose = ${param_count}")
        update_params.append(session_data.phi_access_purpose)

    # Add WHERE clause parameters
    param_count += 1
    session_id_param = f"${param_count}"
    update_params.append(session_id)

    param_count += 1
    org_id_param = f"${param_count}"
    update_params.append(organization_id)

    update_query = f"""
        UPDATE public.user_sessions
        SET {', '.join(update_fields)}
        WHERE id = {session_id_param} AND organization_id = {org_id_param}
        RETURNING
            id, user_id, organization_id, ip_address, user_agent,
            device_fingerprint, risk_score, login_timestamp,
            logout_timestamp, session_status, login_method,
            accessed_phi, phi_access_purpose;
    """

    return update_query, update_params


async def _extract_session_data_from_request(request: Request) -> dict:
    """Extract all session data from request headers."""
    return {
        "ip_address": await get_client_ip(request),
        "user_agent": await get_user_agent(request),
        "device_fingerprint": await get_device_fingerprint(request),
        "risk_score": await get_risk_score(request),
        "login_method": await get_login_method(request),
    }


async def _create_session_record(
    db_conn, session_data: dict, user_context, session_id: str
):
    """Create session record in database."""
    create_session_query = """
        INSERT INTO public.user_sessions (
            id, user_id, organization_id, ip_address, user_agent,
            device_fingerprint, risk_score, login_timestamp,
            session_status, login_method, accessed_phi, phi_access_purpose
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), 'active', $8, $9, $10)
        RETURNING
            id, user_id, organization_id, ip_address, user_agent,
            device_fingerprint, risk_score, login_timestamp,
            logout_timestamp, session_status, login_method,
            accessed_phi, phi_access_purpose;
    """

    return await db_conn.fetchrow(
        create_session_query,
        session_id,
        user_context.user_id,
        user_context.organization_id,
        session_data["ip_address"],
        session_data["user_agent"],
        session_data["device_fingerprint"],
        session_data["risk_score"],
        session_data["login_method"],
        False,  # accessed_phi
        None,  # phi_access_purpose
    )


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
async def create_session(
    request: Request,
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
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
    logger.info(
        ("POST /sessions request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id'))
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

    # Extract session ID from JWT token
    session_id = extract_session_id_from_token(current_user)
    logger.debug(
        ("Session ID extracted from token - Request ID: %s, ",request_id),
        ("Session ID: %s, ",session_id)
    )

    # Set audit context for session creation
    request.state.audit_table = "user_sessions"
    request.state.audit_requested_id = session_id
    request.state.audit_description = (
        f"Created new session for user: {user_context.email}"
    )
    request.state.audit_risk_level = "medium"
    logger.debug(
        ("Audit context set for session creation - Request ID: %s, ",request_id),
        ("Session ID: %s, User Email: %s",session_id,user_context.email)
    )

    # Check if session already exists
    session_exists = await check_session_exists(session_id, db_conn)
    logger.debug(
        ("Session existence check completed - Request ID: %s, ",request_id),
        ("Session ID: %s, Session exists: %s",session_id,session_exists)
    )

    if session_exists:
        logger.warning(
            ("Session already exists - Request ID: %s, ",request_id),
            ("Session ID: %s, ",session_id)
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already exists",
        )

    # Extract client information from request headers
    session_data = await _extract_session_data_from_request(request)
    logger.debug(
        ("Session data extracted from request - Request ID: %s, ",request_id),
        ("IP Address: %s, ",session_data['ip_address']),
        ("User Agent: %s, ",session_data['user_agent'][:50]),
        ("Risk Score: %s, ",session_data['risk_score']),
        ("Login Method: %s",session_data['login_method'])
    )

    # Create the session using async SQL
    created_session = await _create_session_record(
        db_conn, session_data, user_context, session_id
    )
    logger.debug(
        ("Session record created in database - Request ID: %s, ",request_id),
        ("Session ID: %s, User ID: %s",session_id,user_context.user_id)
    )

    # Set audit context with new session data
    request.state.raw_audit_new_data = {
        "session_id": str(created_session["id"]),
        "user_id": user_context.user_id,
        "organization_id": user_context.organization_id,
        "ip_address": session_data["ip_address"],
        "user_agent": session_data["user_agent"],
        "device_fingerprint": session_data["device_fingerprint"],
        "risk_score": session_data["risk_score"],
        "login_method": session_data["login_method"],
        "accessed_phi": False,
        "phi_access_purpose": None,
        "created_at": (
            created_session["login_timestamp"].isoformat()
            if created_session["login_timestamp"]
            else None
        ),
    }

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

    logger.info(
        ("POST /sessions request completed successfully - Request ID: %s, ",request_id),
        ("Session ID: %s, User ID: %s, ",session_id,user_context.user_id),
        ("IP Address: %s, Risk Score: %s, ",session_data['ip_address'],session_data['risk_score']),
        ("Status Code: 201")
    )

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
    db_conn, user_context, query_params, page_size: int, offset: int
):
    """Fetch sessions data and count."""
    # Build query using utility function
    filters = SessionFilter(
        search=query_params.search,
        session_status=query_params.session_status,
        login_method=query_params.login_method,
        limit=page_size,
        offset=offset,
    )

    sessions_query, query_params_list = build_sessions_filter_query(
        organization_id=user_context.organization_id,
        filters=filters,
    )

    # Execute sessions query
    sessions_data = await db_conn.fetch(sessions_query, *query_params_list)

    # Build and execute count query
    count_query, count_params = build_sessions_count_query(
        organization_id=user_context.organization_id,
        search=query_params.search,
        session_status=query_params.session_status,
        login_method=query_params.login_method,
    )

    count_result = await db_conn.fetchrow(count_query, *count_params)
    total_count = count_result["total_count"] if count_result else 0

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
    db_conn=Depends(get_async_db_conn),
):
    """
    Update session logout information (Optimized & Truly Async)
    """
    request_id = str(uuid.uuid4())
    logger.info(
        ("PUT /sessions/logout request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
    )

    session_id = extract_session_id_from_token(current_user)
    logger.debug(
        ("Session ID extracted from token - Request ID: %s, ",request_id),
        ("Session ID: %s, ",session_id)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

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
    logger.debug(
        ("Audit context set for session logout update - Request ID: %s, ",request_id),
        ("Session ID: %s, ",session_id)
    )

    # Check if session exists in organization and get current data
    existing_session = await check_session_exists_in_org(
        session_id, user_context.organization_id, db_conn
    )
    logger.debug(
        ("Existing session retrieved - Request ID: %s, ",request_id),
        ("Session ID: %s, Session Status: %s",session_id,existing_session['session_status'])
    )

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
    logger.debug(
        ("Logout data prepared - Request ID: %s, ",request_id),
        ("Session ID: %s, New Status: inactive",session_id)
    )

    # Build update query using utility function
    update_query, update_params = build_session_update_query(
        logout_data, session_id, user_context.organization_id
    )

    # Execute update query
    updated_session = await db_conn.fetchrow(update_query, *update_params)
    logger.debug(
        ("Session logout updated in database - Request ID: %s, ",request_id),
        ("Session ID: %s, Update successful: %s",session_id,updated_session is not None)
    )

    # Set new values for audit comparison
    request.state.raw_audit_new_data = {
        "session_id": session_id,
        "session_status": "inactive",
        "logout_timestamp": (
            updated_session["logout_timestamp"].isoformat()
            if updated_session["logout_timestamp"]
            else None
        ),
        "accessed_phi": False,
        "phi_access_purpose": None,
        "logout_reason": "user_logout",
        "organization_id": user_context.organization_id,
    }

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

    logger.info(
        ("PUT /sessions/logout request completed successfully - Request ID: %s, ",request_id),
        ("Session ID: %s, User ID: %s, ",session_id,user_context.user_id),
        ("Old Status: %s, New Status: inactive, ",existing_session['session_status']),
        ("Status Code: 200")
    )

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
    db_conn=Depends(get_async_db_conn),
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
    logger.info(
        ("GET /sessions request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Search: %s, Session Status: %s, ",query_params.search,query_params.session_status),
        ("Login Method: %s, Page: %s, ",query_params.login_method,query_params.page),
        ("Page Size: %s",query_params.page_size)
    )

    # Extract and validate user context from JWT token
    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s",user_context.email,user_context.organization_id)
    )

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
    logger.debug(
        ("Audit context set for session list access - Request ID: %s, ",request_id),
        ("Search: %s, ",query_params.search or 'none')
    )

    # Validate pagination parameters and calculate offset
    page, page_size, offset = validate_pagination_params(
        query_params.page, query_params.page_size
    )
    logger.debug(
        ("Pagination parameters validated - Request ID: %s, ",request_id),
        ("Page: %s, Page Size: %s, Offset: %s",page,page_size,offset)
    )

    # Fetch sessions data and count
    sessions_data, total_count = await _fetch_sessions_data(
        db_conn, user_context, query_params, page_size, offset
    )
    logger.debug(
        ("Sessions data fetched - Request ID: %s, ",request_id),
        ("Sessions count: %s, Total count: %s",len(sessions_data),total_count)
    )

    # Format sessions data using utility functions
    sessions = [_format_session_item(session) for session in sessions_data]
    logger.debug(
        ("Sessions data formatted - Request ID: %s, ",request_id),
        ("Formatted sessions count: %s",len(sessions))
    )

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

    logger.info(
        ("GET /sessions request completed successfully - Request ID: %s, ",request_id),
        ("Sessions Count: %s, Total Count: %s, ",len(sessions),total_count),
        ("Page: %s, Page Size: %s, Status Code: 200",page,page_size)
    )

    return SessionsResponse(
        status_code=status.HTTP_200_OK,
        message=message,
        sessions=sessions,
        total_count=total_count,
        page=page,
        page_size=page_size,
    )
