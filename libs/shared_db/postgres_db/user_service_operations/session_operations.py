"""
Session Database Operations Module

This module contains all session-related database operations.
All Supabase queries for session management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Session CRUD operations
- Session search and filtering
- Session query building
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging
from apps.user_service.app.schemas.auth import SessionFilter
from libs.shared_db.supabase_db.db import get_fresh_supabase_admin_client, get_supabase_admin_client
from .exception_handling import handle_database_errors, create_error_messages

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _apply_organization_filter(query, organization_id: Optional[str]):
    """
    Apply organization_id filter to a query, handling NULL values properly.

    Args:
        query: Supabase query object
        organization_id: Optional organization ID (can be None)

    Returns:
        Query with organization filter applied
    """
    if organization_id is None:
        return query.is_("organization_id", "null")
    return query.eq("organization_id", organization_id)


# ============================================================================
# SESSION CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_session",
    custom_messages=create_error_messages("create_session", "creating"))
async def create_session(session_data: Dict[str, Any], organization_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a new user session."""
    # Validate input parameters
    if not session_data.get("session_id") or session_data.get("session_id") is None:
        raise ValueError("Session ID cannot be None or empty")

    if not session_data.get("user_id") or session_data.get("user_id") is None:
        raise ValueError("User ID cannot be None or empty")

    supabase = await get_supabase_admin_client()

    session_record = {
        "id": session_data["session_id"],
        "user_id": session_data["user_id"],
        "organization_id": organization_id,  # Can be None now
        "ip_address": session_data["ip_address"],
        "user_agent": session_data["user_agent"],
        "device_fingerprint": session_data["device_fingerprint"],
        "risk_score": session_data["risk_score"],
        "login_timestamp": datetime.now(timezone.utc).isoformat(),
        "session_status": "active",
        "login_method": session_data["login_method"],
        "accessed_phi": session_data.get("accessed_phi", False),
        "phi_access_purpose": session_data.get("phi_access_purpose")
    }

    # Try to set user context for RLS policies (only if organization_id is not None)
    if organization_id:
        try:
            # Set the user context in the Supabase client
            supabase.auth.set_user({
                "id": session_data["user_id"],
                "email": session_data.get("user_email", ""),
                "user_metadata": {
                    "organization_id": organization_id
                }
            })
        except Exception as e:
            logger.warning("Could not set user context: %s", str(e))

    result = await supabase.table("user_sessions").insert(session_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "get_session_by_id",
    custom_messages=create_error_messages("get_session_by_id", "getting"))
async def get_session_by_id(session_id: str, organization_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get session by ID and organization ID (organization_id can be None)."""
    supabase = await get_supabase_admin_client()

    query = supabase.table("user_sessions").select(
        "id, user_id, organization_id, ip_address, user_agent, "
        "device_fingerprint, risk_score, login_timestamp, "
        "logout_timestamp, session_status, login_method, "
        "accessed_phi, phi_access_purpose"
    ).eq("id", session_id)

    query = _apply_organization_filter(query, organization_id)

    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


@handle_database_errors(
    "update_session",
    custom_messages=create_error_messages("update_session", "updating"))
async def update_session(session_id: str, organization_id: Optional[str] = None,
                        update_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Update session information (organization_id can be None)."""
    supabase = await get_supabase_admin_client()

    # Prepare update data with logout_timestamp
    update_payload = {
        "logout_timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Helper to read from Pydantic model or dict uniformly
    def _get(field_name: str):
        if isinstance(update_data, dict):
            return update_data.get(field_name)
        return getattr(update_data, field_name, None) if update_data else None

    # Add optional fields if provided
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


@handle_database_errors(
    "check_session_exists",
    custom_messages=create_error_messages("check_session_exists", "checking"))
async def check_session_exists(session_id: str, organization_id: Optional[str] = None) -> bool:
    """Check if session exists (organization_id can be None)."""
    # Validate input parameters
    if not session_id or session_id is None:
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


def _match_sessions_with_members_and_filter(
    all_sessions: List[Dict[str, Any]],
    members_dict: Dict[str, Dict[str, Any]],
    search_term: str
) -> List[Dict[str, Any]]:
    """
    Match sessions with organization members and apply search filtering.
    
    Args:
        all_sessions: List of session dictionaries from database
        members_dict: Dictionary mapping user_id to member info (email, first_name, last_name)
        search_term: Lowercase search term (empty string if no search)
    
    Returns:
        List of sessions with member info attached, filtered by search if applicable
    """
    matched_sessions = []
    
    for session in all_sessions:
        user_id = session.get("user_id")
        member_info = members_dict.get(user_id) if user_id else None
        
        # Add member info to session
        session_with_member = {**session}
        if member_info:
            session_with_member["organization_members"] = {
                "email": member_info.get("email"),
                "first_name": member_info.get("first_name"),
                "last_name": member_info.get("last_name")
            }
        else:
            session_with_member["organization_members"] = None
        
        # Apply search filter if provided
        if search_term:
            # Check if search matches member fields (case-insensitive, preserves special chars)
            member_matches = False
            if member_info:
                email = (member_info.get("email") or "").lower()
                first_name = (member_info.get("first_name") or "").lower()
                last_name = (member_info.get("last_name") or "").lower()
                
                # Python's 'in' operator handles all characters including +, @, etc.
                member_matches = (
                    search_term in email or
                    search_term in first_name or
                    search_term in last_name
                )
            
            # Check if search matches session fields (case-insensitive, preserves special chars)
            user_agent = (session.get("user_agent") or "").lower()
            ip_address = (session.get("ip_address") or "").lower()
            
            # Python's 'in' operator handles all characters including +, @, etc.
            session_matches = (
                search_term in user_agent or
                search_term in ip_address
            )
            
            # Include session if either member or session fields match
            if member_matches or session_matches:
                matched_sessions.append(session_with_member)
        else:
            # No search filter, include all sessions
            matched_sessions.append(session_with_member)
    
    return matched_sessions

@handle_database_errors(
    "get_sessions_list",
    custom_messages=create_error_messages("get_sessions_list", "getting"))
async def get_sessions_list(
    organization_id: Optional[str], user_id: str,
    filters: SessionFilter) -> List[Dict[str, Any]]:
    """Get paginated list of sessions with optional search and filtering (organization_id can be None)."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the query with filters
    if filters.search and organization_id:
        # Include join with organization_members for search functionality (only if organization_id exists)
        query = supabase.table("user_sessions").select(
            f"{SESSION_FIELDS}, "
            "organization_members!inner(email, full_name)"
        ).eq("organization_id", organization_id
        ).eq("user_id", user_id
        ).or_(
            f"organization_members.email.ilike.*{filters.search}*,"
            f"organization_members.full_name.ilike.*{filters.search}*"
        )
    else:
        # Simple query without join when no search is needed or organization_id is NULL
        query = supabase.table("user_sessions").select(
            SESSION_FIELDS
        ).eq("user_id", user_id)
        query = _apply_organization_filter(query, organization_id)

    # Apply additional filters
    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    # Apply pagination and ordering
    result = await query.order("login_timestamp", desc=True).range(
        filters.offset, filters.offset + filters.limit - 1
    ).execute()

    return result.data if result.data else []


@handle_database_errors(
    "get_sessions_count",
    custom_messages=create_error_messages("get_sessions_count", "getting"))
async def get_sessions_count(organization_id: Optional[str], user_id: str, filters: SessionFilter) -> int:
    """Get total count of sessions matching search criteria (organization_id can be None)."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the count query with filters
    query = supabase.table("user_sessions").select(
        "id", count="exact"
    ).eq("user_id", user_id)
    query = _apply_organization_filter(query, organization_id)

    # Apply filters
    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    if filters.search and organization_id:
        # For search, we need to join with organization_members (only if organization_id exists)
        query = query.select(
            "id, organization_members!inner(email, full_name)", count="exact"
        ).or_(
            f"organization_members.email.ilike.%{filters.search}%,"
            f"organization_members.full_name.ilike.%{filters.search}%"
        )

    result = await query.execute()

    return result.count if result.count is not None else 0


@handle_database_errors(
    "get_sessions_with_count",
    custom_messages=create_error_messages("get_sessions_with_count", "getting"))
async def get_sessions_with_count(
    organization_id: Optional[str], user_id: str,
    filters: SessionFilter) -> Dict[str, Any]:
    """Get paginated list of sessions with total count in a single database call (organization_id can be None)."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the query with filters
    if filters.search and organization_id:
        # Include join with organization_members for search functionality (only if organization_id exists)
        query = supabase.table("user_sessions").select(
            f"{SESSION_FIELDS}, "
            "organization_members!inner(email, full_name)"
        ).eq("organization_id", organization_id
        ).eq("user_id", user_id
        ).or_(
            f"organization_members.email.ilike.%{filters.search}%,"
            f"organization_members.full_name.ilike.%{filters.search}%"
        )
    else:
        # Simple query without join when no search is needed or organization_id is NULL
        query = supabase.table("user_sessions").select(
            SESSION_FIELDS
        ).eq("user_id", user_id)
        query = _apply_organization_filter(query, organization_id)

    # Apply additional filters
    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    # Execute query with pagination - we'll return the length of results as count
    # This is more efficient than making a separate count query
    result = await query.order("login_timestamp", desc=True).range(
        filters.offset, filters.offset + filters.limit - 1
    ).execute()

    data = result.data if result.data else []

    return {
        "data": data,
        "total_count": len(data)  # Return count of current page results
    }


@handle_database_errors(
    "get_org_sessions_with_count",
    custom_messages=create_error_messages("get_org_sessions_with_count", "getting"))
async def get_org_sessions_with_count(
    organization_id: Optional[str],
    filters: SessionFilter,
) -> Dict[str, Any]:
    """
    Get paginated list of sessions for **all users** in an organization
    along with a total count (count of current page).

    Uses three-step approach to avoid PostgREST JOIN limitations:
    1. Get all users and details from organization_members table using organization_id
    2. Get all sessions from user_sessions table with same organization_id
    3. Match by user_id and apply search/filters in Python
    """
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Step 1: Validate organization_id
    if not organization_id:
        return {"data": [], "total_count": 0}

    # Step 2: Get all users and details from organization_members table
    member_query = supabase.table("organization_members").select(
        "user_id, email, first_name, last_name"
    ).eq("organization_id", organization_id)

    member_result = await member_query.execute()
    members_data = member_result.data or []

    # Create a dictionary mapping user_id to member info for quick lookup
    members_dict = {member["user_id"]: member for member in members_data}

    # Step 3: Get all sessions from user_sessions table with same organization_id
    session_query = supabase.table("user_sessions").select(
        SESSION_FIELDS
    ).eq("organization_id", organization_id)

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
    paginated_sessions = matched_sessions[
        filters.offset : filters.offset + filters.limit
    ]

    # Step 6: Return formatted result
    return {
        "data": paginated_sessions,
        "total_count": total_count
    }
