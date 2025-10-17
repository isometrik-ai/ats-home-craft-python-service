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
# SESSION CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_session",
    custom_messages=create_error_messages("create_session", "creating"))
async def create_session(session_data: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
    """Create a new user session."""
    # Validate input parameters
    if not organization_id or organization_id is None:
        raise ValueError("Organization ID cannot be None or empty")

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
        "phi_access_purpose": session_data.get("phi_access_purpose")
    }

    # Try to set user context for RLS policies
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
async def get_session_by_id(session_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
    """Get session by ID and organization ID."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("user_sessions").select(
        "id, user_id, organization_id, ip_address, user_agent, "
        "device_fingerprint, risk_score, login_timestamp, "
        "logout_timestamp, session_status, login_method, "
        "accessed_phi, phi_access_purpose"
    ).eq("id", session_id).eq("organization_id", organization_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


@handle_database_errors(
    "update_session",
    custom_messages=create_error_messages("update_session", "updating"))
async def update_session(session_id: str, organization_id: str,
                        update_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update session information."""
    supabase = await get_supabase_admin_client()

    # Prepare update data with logout_timestamp
    update_payload = {
        "logout_timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Helper to read from Pydantic model or dict uniformly
    def _get(field_name: str):
        if isinstance(update_data, dict):
            return update_data.get(field_name)
        return getattr(update_data, field_name, None)

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

    result = await supabase.table("user_sessions").update(update_payload).eq(
        "id", session_id
    ).eq("organization_id", organization_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "check_session_exists",
    custom_messages=create_error_messages("check_session_exists", "checking"))
async def check_session_exists(session_id: str, organization_id: str) -> bool:
    """Check if session exists."""
    # Validate input parameters
    if not session_id or session_id is None:
        raise ValueError("Session ID cannot be None or empty")

    if not organization_id or organization_id is None:
        raise ValueError("Organization ID cannot be None or empty")

    supabase = await get_supabase_admin_client()

    result = await supabase.table("user_sessions").select("id").eq(
        "id", session_id
    ).eq("organization_id", organization_id).execute()

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

@handle_database_errors(
    "get_sessions_list",
    custom_messages=create_error_messages("get_sessions_list", "getting"))
async def get_sessions_list(
    organization_id: str, user_id: str,
    filters: SessionFilter) -> List[Dict[str, Any]]:
    """Get paginated list of sessions with optional search and filtering."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the query with filters
    if filters.search:
        # Include join with organization_members for search functionality
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
        # Simple query without join when no search is needed
        query = supabase.table("user_sessions").select(
            SESSION_FIELDS
        ).eq("organization_id", organization_id
        ).eq("user_id", user_id)

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
async def get_sessions_count(organization_id: str, user_id: str, filters: SessionFilter) -> int:
    """Get total count of sessions matching search criteria."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the count query with filters
    query = supabase.table("user_sessions").select(
        "id", count="exact"
    ).eq("organization_id", organization_id).eq(
        "user_id", user_id
    )

    # Apply filters
    if filters.session_status:
        query = query.eq("session_status", filters.session_status)

    if filters.login_method:
        query = query.eq("login_method", filters.login_method)

    if filters.search:
        # For search, we need to join with organization_members
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
    organization_id: str, user_id: str,
    filters: SessionFilter) -> Dict[str, Any]:
    """Get paginated list of sessions with total count in a single database call."""
    # Use fresh admin client to avoid state corruption
    supabase = await get_fresh_supabase_admin_client()

    # Build the query with filters
    if filters.search:
        # Include join with organization_members for search functionality
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
        # Simple query without join when no search is needed
        query = supabase.table("user_sessions").select(
            SESSION_FIELDS
        ).eq("organization_id", organization_id
        ).eq("user_id", user_id)

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


# ============================================================================
# SESSION QUERY BUILDING
# ============================================================================

# Note: Query building functions have been removed as Supabase SDK
# provides built-in query methods that are more efficient and type-safe.
# The filtering logic is now handled directly in the respective functions.
