"""
Organisation Database Operations Module

This module contains all organisation-related database operations.
All SQL queries for organisation management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Organisation CRUD operations
- Organisation member management
- Organisation validation operations
- Organisation search and filtering
- Organisation settings operations
"""

from typing import List, Dict, Any, Optional
from unittest import result
from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.organisation_utils import validate_uuid_format
import time
from fastapi import HTTPException

# Initialize logger
logger = get_logger("organisation_operations")


# ============================================================================
# ORGANISATION CRUD OPERATIONS
# ============================================================================

async def create_new_organisation(organisation_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new organisation."""
    supabase = await get_supabase_admin_client()
    try:
        org_record = {
            "id": organisation_data["id"],
            "name": organisation_data["name"],
            "slug": organisation_data["slug"],
            "domain": organisation_data.get("domain"),
            "logo_url": organisation_data.get("logo_url"),
            "plan_type": organisation_data.get("plan_type", "starter"),
            "status": organisation_data.get("status", "trial"),
            "account_type": organisation_data.get("account_type", "personal"),
            "created_at": "now()",
            "updated_at": "now()"
        }

        result = await supabase.table("organizations").insert(org_record).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]
        return {}

    except APIError as e:
        logger.error("Supabase API error creating organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error creating organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error creating organisation: %s", e, exc_info=True)
        raise


async def get_organisation_details_by_id(organisation_id: str) -> Optional[Dict[str, Any]]:
    """Get organisation details by ID with member_count, mimicking SQL query builder.

    Mirrors build_organisation_detail_query() from organisation_utils.py:
    - Returns core organisation fields
    - Counts active members (status = 'active') as member_count
    - Does not return the embedded members array
    """
    supabase = await get_supabase_admin_client()
    try:
        # Fetch organisation with embedded members (only fields needed to compute count)
        result = await supabase.table("organizations").select(
            "id, name, slug, domain, logo_url, plan_type, status, max_users, timezone, settings, "
            "created_at, updated_at, organization_members(status)"
        ).eq("id", organisation_id).limit(1).execute()

        if not result.data or len(result.data) == 0:
            return None

        org = result.data[0]

        # Compute active member count (equivalent to LEFT JOIN + COUNT where om.status='active')
        members = org.get("organization_members", []) or []
        active_member_count = sum(1 for m in members if (m or {}).get("status") == "active")

        # Shape response to match expected fields from the SQL builder
        org["member_count"] = active_member_count
        # Remove embedded members from response
        org.pop("organization_members", None)

        return org

    except APIError as e:
        logger.error("Supabase API error getting organisation by ID: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation by ID: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation by ID: %s", e, exc_info=True)
        raise


async def get_organisation_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Get organisation by slug."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").select(
            "id, name, slug, domain, logo_url, plan_type, status, account_type, "
            "created_at, updated_at"
        ).eq("slug", slug).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    except APIError as e:
        logger.error("Supabase API error getting organisation by slug: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation by slug: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation by slug: %s", e, exc_info=True)
        raise


async def update_organisation_details(organisation_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update organisation information, mimicking _build_organization_update_query logic.

    This function mimics the logic from _build_organization_update_query() in organisation.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await get_supabase_admin_client()
    try:
        # 1️⃣ Collect only keys the client actually sent (mimicking exclude_unset=True, exclude_none=True)
        payload = {k: v for k, v in update_data.items() if v is not None}

        # 2️⃣ Strip out empty strings so "" doesn't overwrite existing data (mimicking the empty string check)
        payload = {
            k: v for k, v in payload.items() if not (isinstance(v, str) and v.strip() == "")
        }

        if not payload:  # nothing to change (mimicking the early return logic)
            return {}

        # 3️⃣ Always set updated_at (mimicking the audit column logic)
        payload["updated_at"] = "now()"

        # 4️⃣ Execute update with Supabase SDK (mimicking the WHERE id = $N logic)
        result = await supabase.table("organizations").update(payload).eq(
            "id", organisation_id
        ).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]
        return {}

    except APIError as e:
        logger.error("Supabase API error updating organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating organisation: %s", e, exc_info=True)
        raise


async def delete_organisation(organisation_id: str) -> bool:
    """Delete organisation."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").delete().eq(
            "id", organisation_id
        ).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error deleting organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error deleting organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error deleting organisation: %s", e, exc_info=True)
        raise


async def check_organisation_exists(organisation_id: str) -> bool:
    """Check if organisation exists."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").select("id").eq(
            "id", organisation_id
        ).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error checking organisation exists: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error checking organisation exists: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error checking organisation exists: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION LISTING AND SEARCH
# ============================================================================

async def get_list_of_organisations(search: Optional[str] = None, status: Optional[str] = None,
                               limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get paginated list of organisations with optional search and filtering.

    This function mimics the logic from build_organisations_filter_query() in organisation_utils.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await get_supabase_admin_client()
    try:
        # Build the query using the same logic as build_organisations_filter_query
        query = supabase.table("organizations").select(
            "id, name, slug, domain, logo_url, plan_type, status, account_type, "
            "created_at, updated_at, organization_members(id)"
        )

        # Apply search filter (mimicking the ILIKE logic from build_organisations_filter_query)
        # This handles the same search logic: name, slug, domain with case-insensitive partial matching
        if search:
            query = query.or_(
                f"name.ilike.%{search}%,"
                f"slug.ilike.%{search}%,"
                f"domain.ilike.%{search}%"
            )

        # Apply status filter (mimicking the exact match logic from build_organisations_filter_query)
        if status:
            query = query.eq("status", status)

        # Apply pagination and ordering (mimicking the LIMIT/OFFSET and ORDER BY logic)
        result = await query.order("created_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()

        # Process results to add member count (mimicking the GROUP BY logic from build_organisations_filter_query)
        # This calculates member_count by counting organization_members, just like the SQL query does
        organisations = result.data if result.data else []
        for org in organisations:
            org["member_count"] = len(org.get("organization_members", []))
            # Remove the organization_members array as it's not needed in response
            # This mimics the behavior of the SQL query which only returns the count
            org.pop("organization_members", None)

        return organisations

    except APIError as e:
        logger.error("Supabase API error getting organisations list: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisations list: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisations list: %s", e, exc_info=True)
        raise


async def get_organisations_count(search: Optional[str] = None, status: Optional[str] = None) -> int:
    """Get total count of organisations matching search criteria.

    This function mimics the logic from build_organisations_count_query() in organisation_utils.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await get_supabase_admin_client()
    try:
        # Build the count query with filters (mimicking build_organisations_count_query logic)
        query = supabase.table("organizations").select("id", count="exact")

        # Apply search filter (mimicking the ILIKE logic from build_organisations_count_query)
        # This handles the same search logic: name, slug, domain with case-insensitive partial matching
        if search:
            query = query.or_(
                f"name.ilike.%{search}%,"
                f"slug.ilike.%{search}%,"
                f"domain.ilike.%{search}%"
            )

        # Apply status filter (mimicking the exact match logic from build_organisations_count_query)
        if status:
            query = query.eq("status", status)

        result = await query.execute()

        return result.count if result.count is not None else 0

    except APIError as e:
        logger.error("Supabase API error getting organisations count: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisations count: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisations count: %s", e, exc_info=True)
        raise


async def get_organisations_with_members(search: Optional[str] = None, status: Optional[str] = None,
                                       limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get organisations with member count information."""
    supabase = await get_supabase_admin_client()
    try:
        # Build the query with joins and filters
        query = supabase.table("organizations").select(
            "id, name, slug, domain, logo_url, plan_type, status, account_type, "
            "created_at, updated_at, "
            "organization_members(id)"
        )

        # Apply search filter
        if search:
            query = query.or_(
                f"name.ilike.%{search}%,"
                f"slug.ilike.%{search}%,"
                f"domain.ilike.%{search}%"
            )

        # Apply status filter
        if status:
            query = query.eq("status", status)

        # Apply pagination and ordering
        result = await query.order("created_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()

        # Process results to add member count
        organisations = result.data if result.data else []
        for org in organisations:
            org["member_count"] = len(org.get("organization_members", []))
            # Remove the organization_members array as it's not needed in response
            org.pop("organization_members", None)

        return organisations

    except APIError as e:
        logger.error("Supabase API error getting organisations with members: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisations with members: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisations with members: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION VALIDATION OPERATIONS
# ============================================================================

async def check_organisation_slug_unique(slug: str, exclude_org_id: Optional[str] = None) -> bool:
    """
    Check if organisation slug is unique.

    Args:
        slug (str): Organisation slug to check
        exclude_org_id (Optional[str]): Organisation ID to exclude from check (for updates)
        with_timing (bool): Whether to log timing information

    Raises:
        HTTPException: 409 for slug conflicts

    Usage:
        await check_organisation_slug_unique(body.slug)
        await check_organisation_slug_unique(body.slug, exclude_org_id=org_id)
    """
    supabase = await get_supabase_admin_client()
    try:
        query = supabase.table("organizations").select("id").eq("slug", slug)

        if exclude_org_id:
            query = query.neq("id", exclude_org_id)

        result = await query.execute()

        return len(result.data) == 0 if result.data else True

    except APIError as e:
        logger.error("Supabase API error checking organisation slug unique: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error checking organisation slug unique: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error checking organisation slug unique: %s", e, exc_info=True)
        raise


async def check_organisation_name_unique(name: str, exclude_org_id: Optional[str] = None) -> bool:
    """Check if organisation name is unique."""
    supabase = await get_supabase_admin_client()
    try:
        query = supabase.table("organizations").select("id").eq("name", name)

        if exclude_org_id:
            query = query.neq("id", exclude_org_id)

        result = await query.execute()

        return len(result.data) == 0 if result.data else True

    except APIError as e:
        logger.error("Supabase API error checking organisation name unique: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error checking organisation name unique: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error checking organisation name unique: %s", e, exc_info=True)
        raise


async def validate_organisation_status(status: str) -> bool:
    """Validate if organisation status is valid."""
    valid_statuses = ["active", "suspended", "trial", "inactive"]
    return status in valid_statuses


# ============================================================================
# ORGANISATION MEMBER OPERATIONS
# ============================================================================

async def get_organisation_members(organisation_id: str, search: Optional[str] = None,
                                 limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get members of an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Build the query with filters
        query = supabase.table("organization_members").select(
            "id, user_id, email, full_name, phone, timezone, role_id, status, "
            "created_at, updated_at, last_active_at, "
            "roles(id, name, description)"
        ).eq("organization_id", organisation_id)

        # Apply search filter
        if search:
            query = query.or_(
                f"email.ilike.%{search}%,"
                f"full_name.ilike.%{search}%,"
                f"phone.ilike.%{search}%"
            )

        # Apply pagination and ordering
        result = await query.order("created_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()

        return result.data if result.data else []

    except APIError as e:
        logger.error("Supabase API error getting organisation members: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation members: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation members: %s", e, exc_info=True)
        raise


async def get_organisation_members_count(organisation_id: str, search: Optional[str] = None) -> int:
    """Get count of organisation members."""
    supabase = await get_supabase_admin_client()
    try:
        # Build the count query with filters
        query = supabase.table("organization_members").select("id", count="exact").eq(
            "organization_id", organisation_id
        )

        # Apply search filter
        if search:
            query = query.or_(
                f"email.ilike.%{search}%,"
                f"full_name.ilike.%{search}%,"
                f"phone.ilike.%{search}%"
            )

        result = await query.execute()

        return result.count if result.count is not None else 0

    except APIError as e:
        logger.error("Supabase API error getting organisation members count: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation members count: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation members count: %s", e, exc_info=True)
        raise


async def add_member_to_organisation(organisation_id: str, member_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add a member to organisation."""
    supabase = await get_supabase_admin_client()
    try:
        member_record = {
            "user_id": member_data["user_id"],
            "email": member_data["email"],
            "full_name": member_data["full_name"],
            "phone": member_data.get("phone"),
            "timezone": member_data.get("timezone", "UTC"),
            "role_id": member_data.get("role_id"),
            "status": member_data.get("status", "active"),
            "organization_id": organisation_id,
            "created_at": "now()",
            "updated_at": "now()",
            "joined_at": "now()"
        }

        result = await supabase.table("organization_members").insert(member_record).execute()

        if result.data and len(result.data) > 0:
            return result.data[0]
        return {}

    except APIError as e:
        logger.error("Supabase API error adding member to organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error adding member to organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error adding member to organisation: %s", e, exc_info=True)
        raise


async def remove_member_from_organisation(organisation_id: str, user_id: str) -> bool:
    """Remove a member from organisation."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organization_members").delete().eq(
            "user_id", user_id
        ).eq("organization_id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error removing member from organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error removing member from organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error removing member from organisation: %s", e, exc_info=True)
        raise


async def update_member_role(organisation_id: str, user_id: str, role_id: str) -> bool:
    """Update member's role in organisation."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organization_members").update({
            "role_id": role_id,
            "updated_at": "now()"
        }).eq("user_id", user_id).eq("organization_id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error updating member role: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating member role: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating member role: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION SETTINGS OPERATIONS
# ============================================================================

async def get_organisation_settings(organisation_id: str) -> Dict[str, Any]:
    """Get organisation settings."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").select(
            "settings"
        ).eq("id", organisation_id).execute()

        if result.data and len(result.data) > 0:
            return result.data[0].get("settings", {})
        return {}

    except APIError as e:
        logger.error("Supabase API error getting organisation settings: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation settings: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation settings: %s", e, exc_info=True)
        raise


async def update_organisation_settings(organisation_id: str, settings: Dict[str, Any]) -> bool:
    """Update organisation settings."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").update({
            "settings": settings,
            "updated_at": "now()"
        }).eq("id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error updating organisation settings: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating organisation settings: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating organisation settings: %s", e, exc_info=True)
        raise


async def get_organisation_preferences(organisation_id: str) -> Dict[str, Any]:
    """Get organisation preferences."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").select(
            "preferences"
        ).eq("id", organisation_id).execute()

        if result.data and len(result.data) > 0:
            return result.data[0].get("preferences", {})
        return {}

    except APIError as e:
        logger.error("Supabase API error getting organisation preferences: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation preferences: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation preferences: %s", e, exc_info=True)
        raise


async def update_organisation_preferences(organisation_id: str, preferences: Dict[str, Any]) -> bool:
    """Update organisation preferences."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").update({
            "preferences": preferences,
            "updated_at": "now()"
        }).eq("id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error updating organisation preferences: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error updating organisation preferences: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error updating organisation preferences: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION QUERY BUILDING
# ============================================================================

# Note: Query building functions have been removed as Supabase SDK
# provides built-in query methods that are more efficient and type-safe.
# The filtering logic is now handled directly in the respective functions.


# ============================================================================
# ORGANISATION STATISTICS OPERATIONS
# ============================================================================

async def get_organisation_statistics(organisation_id: str) -> Dict[str, Any]:
    """Get statistics for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get member count
        members_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        member_count = members_result.count if members_result.count is not None else 0

        # Get role count
        roles_result = await supabase.table("roles").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        role_count = roles_result.count if roles_result.count is not None else 0

        # Get permission count
        permissions_result = await supabase.table("permissions").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        permission_count = permissions_result.count if permissions_result.count is not None else 0

        return {
            "member_count": member_count,
            "role_count": role_count,
            "permission_count": permission_count
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation statistics: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation statistics: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation statistics: %s", e, exc_info=True)
        raise


async def get_organisation_member_stats(organisation_id: str) -> Dict[str, Any]:
    """Get member statistics for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get total members
        total_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        total_members = total_result.count if total_result.count is not None else 0

        # Get active members
        active_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).eq("status", "active").execute()

        active_members = active_result.count if active_result.count is not None else 0

        # Get banned members
        banned_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).eq("status", "banned").execute()

        banned_members = banned_result.count if banned_result.count is not None else 0

        return {
            "total_members": total_members,
            "active_members": active_members,
            "banned_members": banned_members
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation member stats: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation member stats: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation member stats: %s", e, exc_info=True)
        raise


async def get_organisation_activity_stats(organisation_id: str) -> Dict[str, Any]:
    """Get activity statistics for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get recent activity (last 30 days)
        from datetime import datetime, timedelta
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()

        recent_activity_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).gte(
            "last_active_at", thirty_days_ago
        ).execute()

        recent_activity = recent_activity_result.count if recent_activity_result.count is not None else 0

        return {
            "recent_activity_count": recent_activity,
            "period_days": 30
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation activity stats: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation activity stats: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation activity stats: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION BULK OPERATIONS
# ============================================================================

async def bulk_update_organisations(updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bulk update multiple organisations."""
    supabase = await get_supabase_admin_client()
    try:
        results = []
        for update_data in updates:
            org_id = update_data.pop("id")
            result = await update_organisation_details(org_id, update_data)
            if result:
                results.append(result)
        return results

    except APIError as e:
        logger.error("Supabase API error bulk updating organisations: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error bulk updating organisations: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error bulk updating organisations: %s", e, exc_info=True)
        raise


async def bulk_delete_organisations(organisation_ids: List[str]) -> int:
    """Bulk delete multiple organisations."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").delete().in_(
            "id", organisation_ids
        ).execute()

        return len(result.data) if result.data else 0

    except APIError as e:
        logger.error("Supabase API error bulk deleting organisations: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error bulk deleting organisations: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error bulk deleting organisations: %s", e, exc_info=True)
        raise


async def bulk_add_members(organisation_id: str, members_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bulk add multiple members to organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Prepare member records
        member_records = []
        for member_data in members_data:
            member_records.append({
                "user_id": member_data["user_id"],
                "email": member_data["email"],
                "full_name": member_data["full_name"],
                "phone": member_data.get("phone"),
                "timezone": member_data.get("timezone", "UTC"),
                "role_id": member_data.get("role_id"),
                "status": member_data.get("status", "active"),
                "organization_id": organisation_id,
                "created_at": "now()",
                "updated_at": "now()"
            })

        result = await supabase.table("organization_members").insert(member_records).execute()

        return result.data if result.data else []

    except APIError as e:
        logger.error("Supabase API error bulk adding members: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error bulk adding members: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error bulk adding members: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION PERMISSIONS OPERATIONS
# ============================================================================

async def create_default_permissions_for_organisation(organisation_id: str) -> List[Dict[str, Any]]:
    """Create default permissions for new organisation."""
    # This would typically create a set of default permissions
    # For now, return empty list as this is handled by the permission service
    return []


async def create_super_admin_role(organisation_id: str) -> Dict[str, Any]:
    """Create super admin role for organisation."""
    # This would typically create a super admin role
    # For now, return empty dict as this is handled by the role service
    return {}


async def assign_all_permissions_to_role(role_id: str, organisation_id: str) -> bool:
    """Assign all permissions to a role."""
    # This would typically assign all permissions to a role
    # For now, return True as this is handled by the role service
    return True


async def get_organisation_permissions(organisation_id: str) -> List[Dict[str, Any]]:
    """Get all permissions for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("permissions").select(
            "id, name, code, category, description, created_at, updated_at"
        ).eq("organization_id", organisation_id).execute()

        return result.data if result.data else []

    except APIError as e:
        logger.error("Supabase API error getting organisation permissions: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation permissions: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation permissions: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION CLEANUP OPERATIONS
# ============================================================================

async def cleanup_organisation_data(organisation_id: str) -> Dict[str, int]:
    """Clean up all data associated with an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Delete organization members
        members_result = await supabase.table("organization_members").delete().eq(
            "organization_id", organisation_id
        ).execute()

        members_deleted = len(members_result.data) if members_result.data else 0

        # Delete roles
        roles_result = await supabase.table("roles").delete().eq(
            "organization_id", organisation_id
        ).execute()

        roles_deleted = len(roles_result.data) if roles_result.data else 0

        # Delete permissions
        permissions_result = await supabase.table("permissions").delete().eq(
            "organization_id", organisation_id
        ).execute()

        permissions_deleted = len(permissions_result.data) if permissions_result.data else 0

        return {
            "members_deleted": members_deleted,
            "roles_deleted": roles_deleted,
            "permissions_deleted": permissions_deleted
        }

    except APIError as e:
        logger.error("Supabase API error cleaning up organisation data: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error cleaning up organisation data: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error cleaning up organisation data: %s", e, exc_info=True)
        raise


async def archive_organisation(organisation_id: str) -> bool:
    """Archive an organisation (soft delete)."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").update({
            "status": "archived",
            "updated_at": "now()"
        }).eq("id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error archiving organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error archiving organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error archiving organisation: %s", e, exc_info=True)
        raise


async def restore_organisation(organisation_id: str) -> bool:
    """Restore an archived organisation."""
    supabase = await get_supabase_admin_client()
    try:
        result = await supabase.table("organizations").update({
            "status": "active",
            "updated_at": "now()"
        }).eq("id", organisation_id).execute()

        return len(result.data) > 0 if result.data else False

    except APIError as e:
        logger.error("Supabase API error restoring organisation: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error restoring organisation: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error restoring organisation: %s", e, exc_info=True)
        raise


# ============================================================================
# ORGANISATION MONITORING OPERATIONS
# ============================================================================

async def get_organisation_health_status(organisation_id: str) -> Dict[str, Any]:
    """Get health status of an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get organization status
        org_result = await supabase.table("organizations").select(
            "status, created_at, updated_at"
        ).eq("id", organisation_id).execute()

        if not org_result.data or len(org_result.data) == 0:
            return {"status": "not_found", "healthy": False}

        org_data = org_result.data[0]

        # Check if organization is active
        is_active = org_data.get("status") == "active"

        return {
            "status": org_data.get("status"),
            "healthy": is_active,
            "created_at": org_data.get("created_at"),
            "updated_at": org_data.get("updated_at")
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation health status: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation health status: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation health status: %s", e, exc_info=True)
        raise


async def get_organisation_usage_stats(organisation_id: str) -> Dict[str, Any]:
    """Get usage statistics for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get member count
        members_result = await supabase.table("organization_members").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        member_count = members_result.count if members_result.count is not None else 0

        # Get role count
        roles_result = await supabase.table("roles").select(
            "id", count="exact"
        ).eq("organization_id", organisation_id).execute()

        role_count = roles_result.count if roles_result.count is not None else 0

        return {
            "member_count": member_count,
            "role_count": role_count,
            "usage_percentage": min(100, (member_count / 100) * 100)  # Assuming 100 is max
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation usage stats: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation usage stats: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation usage stats: %s", e, exc_info=True)
        raise


async def get_organisation_compliance_status(organisation_id: str) -> Dict[str, Any]:
    """Get compliance status for an organisation."""
    supabase = await get_supabase_admin_client()
    try:
        # Get organization status
        org_result = await supabase.table("organizations").select(
            "status, plan_type, created_at"
        ).eq("id", organisation_id).execute()

        if not org_result.data or len(org_result.data) == 0:
            return {"compliant": False, "status": "not_found"}

        org_data = org_result.data[0]

        # Basic compliance check
        is_active = org_data.get("status") == "active"
        has_plan = org_data.get("plan_type") is not None

        return {
            "compliant": is_active and has_plan,
            "status": org_data.get("status"),
            "plan_type": org_data.get("plan_type"),
            "created_at": org_data.get("created_at")
        }

    except APIError as e:
        logger.error("Supabase API error getting organisation compliance status: %s", e, exc_info=True)
        raise
    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error getting organisation compliance status: %s", e, exc_info=True)
        raise
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error getting organisation compliance status: %s", e, exc_info=True)
        raise
