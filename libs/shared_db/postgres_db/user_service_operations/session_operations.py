"""Session Database Operations Module
This module contains all session-related database operations.
All Supabase queries for session management should be centralized here.
"""

from datetime import datetime, timezone
from typing import Any

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.auth import SessionFilter
from libs.shared_db.supabase_db.db import (
    get_fresh_supabase_admin_client,
    get_supabase_admin_client,
)

logger = get_logger(__name__)


def _apply_organization_filter(query: Any, organization_id: str | None) -> Any:
    """Apply organization_id filter to a query, handling NULL values properly.
    Args:
        query: Query to apply organization filter to
        organization_id: Optional organization ID (can be None)
    Returns:
        Query with organization filter applied
    """
    if organization_id is None:
        return query.is_("organization_id", "null")
    return query.eq("organization_id", organization_id)


async def create_session(
    session_data: dict[str, Any], organization_id: str | None = None
) -> dict[str, Any]:
    """Create a new user session.
    Args:
        session_data: Session data
        organization_id: Optional organization ID (can be None)
    Returns:
        dict containing the new session
    """
    if not session_data.get("session_id") or session_data.get("session_id") is None:
        raise ValueError("Session ID cannot be None or empty")

    if not session_data.get("user_id") or session_data.get("user_id") is None:
        raise ValueError("User ID cannot be None or empty")

    supabase = await get_supabase_admin_client()

    session_record = {
        "id": session_data["session_id"],
        "user_id": session_data["user_id"],
        "organization_id": organization_id,
        "ip_address": session_data["ip_address"],
        "user_agent": session_data["user_agent"],
        "device_fingerprint": session_data["device_fingerprint"],
        "risk_score": session_data["risk_score"],
        "login_timestamp": datetime.now(timezone.utc).isoformat(),
        "session_status": "active",
        "login_method": session_data["login_method"],
        "accessed_phi": session_data.get("accessed_phi", False),
        "phi_access_purpose": session_data.get("phi_access_purpose"),
    }

    if organization_id:
        supabase.auth.set_user(
            {
                "id": session_data["user_id"],
                "email": session_data.get("user_email", ""),
                "user_metadata": {"organization_id": organization_id},
            }
        )

    result = await supabase.table("user_sessions").insert(session_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def get_session_by_id(
    session_id: str, organization_id: str | None = None
) -> dict[str, Any] | None:
    """Get session by ID and organization ID.
    Args:
        session_id: Session ID
        organization_id: Optional organization ID (can be None)
    Returns:
        dict containing the session or None if not found
    """
    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("user_sessions")
        .select(
            "id, user_id, organization_id, ip_address, user_agent, "
            "device_fingerprint, risk_score, login_timestamp, "
            "logout_timestamp, session_status, login_method, "
            "accessed_phi, phi_access_purpose"
        )
        .eq("id", session_id)
    )

    query = _apply_organization_filter(query, organization_id)

    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def update_session(
    session_id: str,
    organization_id: str | None = None,
    update_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update session information.
    Args:
        session_id: Session ID
        organization_id: Optional organization ID (can be None)
        update_data: Update data
    Returns:
        dict containing the updated session
    """
    supabase = await get_supabase_admin_client()

    update_payload = {"logout_timestamp": datetime.now(timezone.utc).isoformat()}

    def _get(field_name: str):
        if isinstance(update_data, dict):
            return update_data.get(field_name)
        return getattr(update_data, field_name, None) if update_data else None

    session_status_value = _get("session_status")
    if session_status_value is not None:
        update_payload["session_status"] = session_status_value

    accessed_phi_value = _get("accessed_phi")
    if accessed_phi_value is not None:
        update_payload["accessed_phi"] = accessed_phi_value

    phi_access_purpose_value = _get("phi_access_purpose")
    if phi_access_purpose_value is not None:
        update_payload["phi_access_purpose"] = phi_access_purpose_value

    query = supabase.table("user_sessions").update(update_payload).eq("id", session_id)
    query = _apply_organization_filter(query, organization_id)

    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def check_session_exists(session_id: str, organization_id: str | None = None) -> bool:
    """Check if session exists.
    Args:
        session_id: Session ID
        organization_id: Optional organization ID (can be None)
    Returns:
        bool: True if session exists, False otherwise
    """
    if not session_id:
        raise ValueError("Session ID cannot be None or empty")

    supabase = await get_supabase_admin_client()

    query = supabase.table("user_sessions").select("id").eq("id", session_id)
    query = _apply_organization_filter(query, organization_id)

    result = await query.execute()

    return len(result.data) > 0 if result.data else False


# ============================================================================
# SESSION LISTING AND SEARCH
# ============================================================================

# Common field list for session queries
SESSION_FIELDS = (
    "id, user_id, organization_id, ip_address, user_agent, "
    "device_fingerprint, risk_score, login_timestamp, "
    "logout_timestamp, session_status, login_method, "
    "accessed_phi, phi_access_purpose"
)


def _check_member_matches(member_info: dict[str, Any] | None, search_term: str) -> bool:
    """Check if search term matches any member fields (email, first_name, last_name).
    Args:
        member_info: Member dictionary with email, first_name, last_name (can be None)
        search_term: Lowercase search term
    Returns:
        bool: True if search matches any member field, False otherwise
    """
    if not member_info:
        return False

    email = (member_info.get("email") or "").lower()
    first_name = (member_info.get("first_name") or "").lower()
    last_name = (member_info.get("last_name") or "").lower()

    return search_term in email or search_term in first_name or search_term in last_name


def _check_session_matches(session: dict[str, Any], search_term: str) -> bool:
    """Check if search term matches any session fields (user_agent, ip_address).
    Args:
        session: Session dictionary
        search_term: Lowercase search term
    Returns:
        bool: True if search matches any session field, False otherwise
    """
    user_agent = (session.get("user_agent") or "").lower()
    ip_address = (session.get("ip_address") or "").lower()

    return search_term in user_agent or search_term in ip_address


def _attach_member_info_to_session(
    session: dict[str, Any], member_info: dict[str, Any] | None
) -> dict[str, Any]:
    """Attach organization member info to a session dictionary.
    Args:
        session: Session dictionary
        member_info: Member dictionary with email, first_name, last_name (can be None)
    Returns:
        dict containing the session with organization member info
    """
    session_with_member = {**session}
    if member_info:
        session_with_member["organization_members"] = {
            "email": member_info.get("email"),
            "first_name": member_info.get("first_name"),
            "last_name": member_info.get("last_name"),
        }
    else:
        session_with_member["organization_members"] = None

    return session_with_member


def _match_sessions_with_members_and_filter(
    all_sessions: list[dict[str, Any]],
    members_dict: dict[str, dict[str, Any]],
    search_term: str,
) -> list[dict[str, Any]]:
    """Match sessions with organization members and apply search filtering.
    Args:
        all_sessions: List of all sessions
        members_dict: Dictionary mapping user_id to member info
        search_term: Lowercase search term
    Returns:
        list of sessions with organization member info
    """
    matched_sessions = []

    for session in all_sessions:
        user_id = session.get("user_id")
        member_info = members_dict.get(user_id) if user_id else None

        session_with_member = _attach_member_info_to_session(session, member_info)

        if search_term:
            member_matches = _check_member_matches(member_info, search_term)
            session_matches = _check_session_matches(session, search_term)

            if member_matches or session_matches:
                matched_sessions.append(session_with_member)
        else:
            matched_sessions.append(session_with_member)

    return matched_sessions


async def get_sessions_list(
    organization_id: str | None, user_id: str, filters: SessionFilter
) -> list[dict[str, Any]]:
    """Get paginated list of sessions with optional search and filtering.
    Args:
        organization_id: Optional organization ID (can be None)
        user_id: User ID
        filters: Filters
    Returns:
        list of sessions with organization member info
    """
    supabase = await get_fresh_supabase_admin_client()

    if filters.search and organization_id:
        query = (
            supabase.table("user_sessions")
            .select(f"{SESSION_FIELDS}, organization_members!inner(email, full_name)")
            .eq("organization_id", organization_id)
            .eq("user_id", user_id)
            .or_(
                f"organization_members.email.ilike.*{filters.search}*,"
                f"organization_members.full_name.ilike.*{filters.search}*"
            )
        )
    else:
        query = supabase.table("user_sessions").select(SESSION_FIELDS).eq("user_id", user_id)
        query = _apply_organization_filter(query, organization_id)

    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    result = (
        await query.order("login_timestamp", desc=True)
        .range(filters.offset, filters.offset + filters.limit - 1)
        .execute()
    )

    return result.data if result.data else []


async def get_sessions_count(
    organization_id: str | None, user_id: str, filters: SessionFilter
) -> int:
    """Get total count of sessions matching search criteria.
    Args:
        organization_id: Optional organization ID (can be None)
        user_id: User ID
        filters: Filters
    Returns:
        int: Total count of sessions
    """
    supabase = await get_fresh_supabase_admin_client()

    query = supabase.table("user_sessions").select("id", count="exact").eq("user_id", user_id)
    query = _apply_organization_filter(query, organization_id)

    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    if filters.search and organization_id:
        query = query.select("id, organization_members!inner(email, full_name)", count="exact").or_(
            f"organization_members.email.ilike.%{filters.search}%,"
            f"organization_members.full_name.ilike.%{filters.search}%"
        )

    result = await query.execute()

    return result.count if result.count is not None else 0


async def get_sessions_with_count(
    organization_id: str | None, user_id: str, filters: SessionFilter
) -> dict[str, Any]:
    """Get paginated list of sessions with total count in a single database call.
    Args:
        organization_id: Optional organization ID (can be None)
        user_id: User ID
        filters: Filters
    Returns:
        dict containing the paginated list of sessions and total count
    """
    supabase = await get_fresh_supabase_admin_client()

    if filters.search and organization_id:
        query = (
            supabase.table("user_sessions")
            .select(f"{SESSION_FIELDS}, organization_members!inner(email, full_name)")
            .eq("organization_id", organization_id)
            .eq("user_id", user_id)
            .or_(
                f"organization_members.email.ilike.%{filters.search}%,"
                f"organization_members.full_name.ilike.%{filters.search}%"
            )
        )
    else:
        query = supabase.table("user_sessions").select(SESSION_FIELDS).eq("user_id", user_id)
        query = _apply_organization_filter(query, organization_id)

    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    result = (
        await query.order("login_timestamp", desc=True)
        .range(filters.offset, filters.offset + filters.limit - 1)
        .execute()
    )

    data = result.data if result.data else []

    return {
        "data": data,
        "total_count": len(data),
    }


async def get_org_sessions_with_count(
    organization_id: str | None,
    filters: SessionFilter,
) -> dict[str, Any]:
    """Get paginated list of sessions for **all users** in an organization
    along with a total count (count of current page).
    Args:
        organization_id: Optional organization ID (can be None)
        filters: Filters
    Returns:
        dict containing the paginated list of sessions and total count
    """
    supabase = await get_fresh_supabase_admin_client()

    if not organization_id:
        return {"data": [], "total_count": 0}

    # Step 2: Get all users and details from organization_members table
    member_query = (
        supabase.table("organization_members")
        .select("user_id, email, first_name, last_name")
        .eq("organization_id", organization_id)
    )

    member_result = await member_query.execute()
    members_data = member_result.data or []

    # Create a dictionary mapping user_id to member info for quick lookup
    members_dict = {member["user_id"]: member for member in members_data}

    # Step 3: Get all sessions from user_sessions table with same organization_id
    session_query = (
        supabase.table("user_sessions")
        .select(SESSION_FIELDS)
        .eq("organization_id", organization_id)
    )

    # Apply session-level filters before fetching
    if filters.session_status:
        session_query = session_query.eq("session_status", filters.session_status)

    if filters.login_method:
        session_query = session_query.eq("login_method", filters.login_method)

    # Get all sessions (we'll paginate after filtering)
    session_result = await session_query.order("login_timestamp", desc=True).execute()
    all_sessions = session_result.data or []

    # Step 4: Match sessions with members and apply search filters
    # Convert search term to lowercase for case-insensitive matching
    # Note: Special characters like +, @, etc. are preserved and matched as-is
    search_term = (filters.search or "").lower() if filters.search else ""

    # Use helper function to reduce cognitive complexity
    matched_sessions = _match_sessions_with_members_and_filter(
        all_sessions, members_dict, search_term
    )

    # Step 5: Apply pagination
    total_count = len(matched_sessions)
    paginated_sessions = matched_sessions[filters.offset : filters.offset + filters.limit]

    # Step 6: Return formatted result
    return {"data": paginated_sessions, "total_count": total_count}
