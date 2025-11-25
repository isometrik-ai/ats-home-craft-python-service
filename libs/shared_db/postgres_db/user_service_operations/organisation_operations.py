"""
Organisation Database Operations Module

This module contains all organisation-related database operations.
All SQL queries for organisation management should be centralized here.
"""
from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import List, Dict, Any, Optional
from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id, update_metadata_of_user
from libs.shared_utils.common_query import DEFAULT_PERMISSIONS
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    handle_database_errors, create_error_messages
)
from libs import NOW_CONSTANT

# Initialize logger
logger = get_logger("organisation_operations")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _get_supabase_client():
    """Get Supabase admin client."""
    return await get_supabase_admin_client()

def _has_result_data(result) -> bool:
    """Check if result has data."""
    return len(result.data) > 0 if result.data else False

def _get_result_data(result, default=None):
    """Get result data with default fallback."""
    return result.data if result.data else (default or [])

def _apply_search_filter(query, search: str, fields: List[str]):
    """Apply search filter to query."""
    if not search:
        return query

    search_conditions = [f"{field}.ilike.%{search}%" for field in fields]
    return query.or_(','.join(search_conditions))

def _apply_pagination(query, limit: int, offset: int):
    """Apply pagination to query."""
    return query.order("created_at", desc=True).range(offset, offset + limit - 1)

# ============================================================================
# ORGANISATION CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_new_organisation",
    custom_messages=create_error_messages("create_new_organisation", "creating"))
async def create_new_organisation(organisation_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new organisation."""
    supabase = await get_supabase_admin_client()

    org_record = {
        "id": organisation_data["organization_id"],
        "name": organisation_data["name"],
        "slug": organisation_data["slug"],
        "domain": organisation_data.get("domain"),
        "logo_url": organisation_data.get("logo_url"),
        "status": organisation_data.get("status", "trial"),
        "industry": organisation_data.get("industry"),
        "company_size": organisation_data.get("company_size"),
        "description": organisation_data.get("description"),
        "referral_source": organisation_data.get("referral_source"),
        # "max_users": organisation_data.get("max_users"),
        # "account_type": organisation_data.get("account_type", "personal"),
        "created_at": NOW_CONSTANT,
        "updated_at":NOW_CONSTANT,
        "created_by_id":organisation_data.get("user_id")
    }

    org_record["settings"] = {}

    if organisation_data.get("address") is not None:
        org_record["settings"].update({"address": organisation_data.get("address").model_dump(exclude_unset=True, exclude_none=True)})

    org_record["settings"].update({"practice_areas":{"primary":None,"secondary":None,"specializations":None}})
    org_record["settings"]["practice_areas"]["primary"] = organisation_data.get("primary_practice_areas",None)
    org_record["settings"]["practice_areas"]["secondary"] = organisation_data.get("secondary_practice_areas",None)
    org_record["settings"]["practice_areas"]["specializations"] = organisation_data.get("specializations",None)

    org_record["settings"].update({"preferred_integration":organisation_data.get("preferred_integration",None)})

    org_record["settings"].update({"need_help_importing_data":organisation_data.get("need_help_importing_data",None)})

    org_record["settings"].update({"need_migration_assistance":organisation_data.get("need_migration_assistance",None)})

    org_record["settings"].update({"compliance_security":organisation_data.get("compliance_security",None)})

    org_record["settings"].update({"enterprise_features":organisation_data.get("enterprise_features",None)})

    subscription_dict = {
        'start_date' : NOW_CONSTANT,
        'end_date' : NOW_CONSTANT + " + interval '7 days'",
        'plan_type' : "trial",
        'max_users' : organisation_data.get("max_users",None),
    }

    org_record['settings']['subscription'] = subscription_dict

    table = supabase.table("organizations")
    # Use returning: 'minimal' to avoid SELECT permission requirements
    insert_query = table.insert(org_record, returning="minimal")
    await insert_query.execute()

    # Since we used returning="minimal", we don't get the inserted data back
    # Return the organization data we just inserted
    return {
        "id": organisation_data["organization_id"],
        "name": organisation_data["name"],
        "slug": organisation_data["slug"],
        "domain": organisation_data.get("domain"),
        "logo_url": organisation_data.get("logo_url"),
        "plan_type": organisation_data.get("plan_type", "starter"),
        "status": organisation_data.get("status", "trial"),
        "industry": organisation_data.get("industry"),
        "company_size": organisation_data.get("company_size"),
        "description": organisation_data.get("description"),
        "referral_source": organisation_data.get("referral_source"),
        "max_users": organisation_data.get("max_users"),
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT,
        "created_by_id": organisation_data.get("user_id")
    }


@handle_database_errors(
    "get_organisation_details_by_id",
    custom_messages=create_error_messages("get_organisation_details_by_id", "getting"))
async def get_organisation_details_by_id(organisation_id: str) -> Optional[Dict[str, Any]]:
    """Get organisation details by ID with member_count, mimicking SQL query builder.

    Mirrors build_organisation_detail_query() from organisation_utils.py:
    - Returns core organisation fields
    - Counts active members (status = 'active') as member_count
    - Does not return the embedded members array
    """
    supabase = await get_supabase_admin_client()

    # Fetch organisation with embedded members (only fields needed to compute count)
    table = supabase.table("organizations")
    select_query = table.select(
        "id, name, slug, domain, logo_url, status, timezone, settings, "
        "description, company_size, created_at, updated_at, organization_members(status)"
    )
    eq_query = select_query.eq("id", organisation_id)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

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


@handle_database_errors(
    "update_organisation_details",
    custom_messages=create_error_messages("update_organisation_details", "updating"))
async def update_organisation_details(
    organisation_id: str,
    organisation_data: dict[str,Any],
    update_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Update organisation information, mimicking _build_organization_update_query logic.

    This function mimics the logic from _build_organization_update_query() in organisation.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await get_supabase_admin_client()

    # 1️⃣ Collect only keys the client actually sent(mimicking exclude_unset=True,exclude_none=True)
    payload = {k: v for k, v in update_data.items() if v is not None}

    settings_fields = [
        "address", "primary_practice_areas", "secondary_practice_areas", "specializations",
        "preferred_integration", "need_help_importing_data", "need_migration_assistance",
        "compliance_security", "enterprise_features","max_users","plan_type"]

    if any(field in settings_fields for field in payload.keys()):
        # payload["settings"] = {}
        temp_settings = organisation_data
        temp_var = temp_settings["settings"]

        if payload.get("address") is not None:
            temp_var["address"] = payload.get("address")
            payload.pop("address")

        temp_practice_areas = temp_var.get("practice_areas",None)
        if temp_practice_areas is not None:
            if payload.get("primary_practice_areas") is not None:
                temp_practice_areas["primary"] = payload.get("primary_practice_areas")
                payload.pop("primary_practice_areas")
            if payload.get("secondary_practice_areas") is not None:
                temp_practice_areas["secondary"] = payload.get("secondary_practice_areas")
                payload.pop("secondary_practice_areas")
            if payload.get("specializations") is not None:
                temp_practice_areas["specializations"] = payload.get("specializations")
                payload.pop("specializations")
        else:
            temp_practice_areas = {"primary":None,"secondary":None,"specializations":None}
            if payload.get("primary_practice_areas") is not None:
                temp_practice_areas["primary"] = payload.get("primary_practice_areas")
                payload.pop("primary_practice_areas")
            if payload.get("secondary_practice_areas") is not None:
                temp_practice_areas["secondary"] = payload.get("secondary_practice_areas")
                payload.pop("secondary_practice_areas")
            if payload.get("specializations") is not None:
                temp_practice_areas["specializations"] = payload.get("specializations")
                payload.pop("specializations")
        temp_var["practice_areas"] = temp_practice_areas

        temp_subscription = temp_var.get("subscription",None)
        if temp_subscription is not None:
            if payload.get("max_users") is not None:
                temp_subscription["max_users"] = payload.get("max_users")
                payload.pop("max_users")
            if payload.get("plan_type") is not None:
                temp_subscription["plan_type"] = payload.get("plan_type")
                payload.pop("plan_type")
        else:
            temp_subscription = {"start_date":NOW_CONSTANT,"end_date":NOW_CONSTANT + " + interval '7 days'","plan_type":"trial","max_users":None}
            if payload.get("max_users") is not None:
                temp_subscription["max_users"] = payload.get("max_users")
                payload.pop("max_users")
            if payload.get("plan_type") is not None:
                temp_subscription["plan_type"] = payload.get("plan_type")
                payload.pop("plan_type")
        temp_var["subscription"] = temp_subscription

        if payload.get("preferred_integration") is not None:
            temp_var["preferred_integration"] = payload.get("preferred_integration")
            payload.pop("preferred_integration")

        if payload.get("need_help_importing_data") is not None:
            temp_var["need_help_importing_data"] = payload.get("need_help_importing_data")
            payload.pop("need_help_importing_data")

        if payload.get("need_migration_assistance") is not None:
            temp_var["need_migration_assistance"] = payload.get("need_migration_assistance")
            payload.pop("need_migration_assistance")

        if payload.get("compliance_security") is not None:
            temp_var["compliance_security"] = payload.get("compliance_security")
            payload.pop("compliance_security")

        if payload.get("enterprise_features") is not None:
            temp_var["enterprise_features"] = payload.get("enterprise_features")
            payload.pop("enterprise_features")

        payload["settings"] = temp_var

    if not payload:  # nothing to change (mimicking the early return logic)
        return {}

    # 3️⃣ Always set updated_at (mimicking the audit column logic)
    payload["updated_at"] = NOW_CONSTANT

    print("payload: ", json.dumps(payload, indent=4))

    # 4️⃣ Execute update with Supabase SDK (mimicking the WHERE id = $N logic)
    table = supabase.table("organizations")
    result = await table.update(payload).eq("id", organisation_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "delete_organisation",
    custom_messages=create_error_messages("delete_organisation", "deleting"))
async def delete_organisation(organisation_id: str) -> bool:
    """Delete organisation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    result = await table.delete().eq(
        "id", organisation_id
    ).execute()

    return _has_result_data(result)


@handle_database_errors(
    "check_organisation_exists",
    custom_messages=create_error_messages("check_organisation_exists", "checking"))
async def check_organisation_exists(organisation_id: str) -> bool:
    """Check if organisation exists."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    select_query = table.select("id")
    query = select_query.eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)

# ============================================================================
# ORGANISATION LISTING AND SEARCH
# ============================================================================

@handle_database_errors(
    "get_list_of_organisations",
    custom_messages=create_error_messages("get_list_of_organisations", "getting"))
async def get_list_of_organisations(search: Optional[str] = None, status: Optional[str] = None,
                               limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get paginated list of organisations with optional search and filtering.

    This function mimics the logic from build_organisations_filter_query() in organisation_utils.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await _get_supabase_client()

    # Get the table object first
    table = supabase.table("organizations")

    # Build the query using the same logic as build_organisations_filter_query
    select_query = table.select(
        "id, name, slug, domain, logo_url, status, account_type, "
        "created_at, updated_at, organization_members(id)"
    )

    # Apply search filter using helper function
    query = _apply_search_filter(select_query, search, ["name", "slug", "domain"])

    # Apply status filter (mimicking the exact match logic from build_organisations_filter_query)
    if status:
        query = query.eq("status", status)

    # Apply pagination using helper function
    paginated_query = _apply_pagination(query, limit, offset)
    result = await paginated_query.execute()

    # Process results to add member count using helper function
    organisations = _get_result_data(result)
    for org in organisations:
        org["member_count"] = len(org.get("organization_members", []))
        # Remove the organization_members array as it's not needed in response
        # This mimics the behavior of the SQL query which only returns the count
        org.pop("organization_members", None)

    return organisations


@handle_database_errors(
    "get_organisations_count",
    custom_messages=create_error_messages("get_organisations_count", "getting"))
async def get_organisations_count(search: Optional[str], status: Optional[str]) -> int:
    """Get total count of organisations matching search criteria.

    This function mimics the logic from build_organisations_count_query() in organisation_utils.py
    to ensure consistent parameter handling and filtering across the codebase.
    """
    supabase = await _get_supabase_client()

    # Build the count query with filters (mimicking build_organisations_count_query logic)
    table = supabase.table("organizations")
    select_query = table.select("id", count="exact")

    # Apply search filter using helper function
    query = _apply_search_filter(select_query, search, ["name", "slug", "domain"])

    # Apply status filter (mimicking the exact match logic from build_organisations_count_query)
    if status:
        query = query.eq("status", status)

    result = await query.execute()

    return result.count if result.count is not None else 0


# ============================================================================
# ORGANISATION VALIDATION OPERATIONS
# ============================================================================

@handle_database_errors(
    "check_organisation_slug_unique",
    custom_messages=create_error_messages("check_organisation_slug_unique", "checking"))
async def check_organisation_slug_unique(slug: str, exclude_org_id: Optional[str] = None) -> bool:
    """
    Check if organisation slug is unique.

    Args:
        slug (str): Organisation slug to check
        exclude_org_id (Optional[str]): Organisation ID to exclude from check (for updates)

    Raises:
        HTTPException: 409 for slug conflicts

    Usage:
        await check_organisation_slug_unique(body.slug)
        await check_organisation_slug_unique(body.slug, exclude_org_id=org_id)
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("id").eq("slug", slug)

    if exclude_org_id:
        query = query.neq("id", exclude_org_id)

    result = await query.execute()

    return len(result.data) == 0 if result.data else True


@handle_database_errors(
    "check_organisation_name_unique",
    custom_messages=create_error_messages("check_organisation_name_unique", "checking"))
async def check_organisation_name_unique(name: str, exclude_org_id: Optional[str] = None) -> bool:
    """Check if organisation name is unique."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("id").eq("name", name)

    if exclude_org_id:
        query = query.neq("id", exclude_org_id)

    result = await query.execute()

    return len(result.data) == 0 if result.data else True


# ============================================================================
# ORGANISATION MEMBER OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_organisation_members",
    custom_messages=create_error_messages("get_organisation_members", "getting"))
async def get_organisation_members(organisation_id: str, search: Optional[str] = None,
                                 limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get members of an organisation."""
    supabase = await get_supabase_admin_client()

    # Build the query with filters
    table = supabase.table("organization_members")
    query = table.select(
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
    query = query.order("created_at", desc=True).range(
        offset, offset + limit - 1
    )
    result = await query.execute()

    return _get_result_data(result)


@handle_database_errors(
    "get_organisation_members_count",
    custom_messages=create_error_messages("get_organisation_members_count", "getting"))
async def get_organisation_members_count(organisation_id: str, search: Optional[str] = None) -> int:
    """Get count of organisation members."""
    supabase = await get_supabase_admin_client()

    # Build the count query with filters
    table = supabase.table("organization_members")
    query = table.select("id", count="exact").eq(
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


@handle_database_errors(
    "add_member_to_organisation",
    custom_messages=create_error_messages("add_member_to_organisation", "adding"))
async def add_member_to_organisation(
    organisation_id: str,
    member_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Add a member to organisation."""
    supabase = await get_supabase_admin_client()

    member_record = {
        "user_id": member_data["user_id"],
        "email": member_data["email"],
        "role_id": member_data.get("role_id"),
        "status": member_data.get("status", "active"),
        "organization_id": organisation_id,
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT,
        "joined_at": NOW_CONSTANT
    }

    data = await get_user_by_id(member_data["user_id"])
    member_record["first_name"] =data.user.user_metadata.get("first_name",member_data["first_name"])
    member_record["last_name"] = data.user.user_metadata.get("last_name",member_data["last_name"])
    member_record["phone"] = data.user.user_metadata.get("phone",member_data["phone"])
    member_record["timezone"] = data.user.user_metadata.get("timezone",member_data["timezone"])

    table = supabase.table("organization_members")
    query = table.insert(member_record)
    result = await query.execute()

    await update_metadata_of_user(member_data["user_id"],{
        "organization_id": organisation_id
    })

    return _has_result_data(result)


@handle_database_errors(
    "remove_member_from_organisation",
    custom_messages=create_error_messages("remove_member_from_organisation", "removing"))
async def remove_member_from_organisation(organisation_id: str, user_id: str) -> bool:
    """Remove a member from organisation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    query = table.delete().eq("user_id", user_id).eq("organization_id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)


@handle_database_errors(
    "update_member_role",
    custom_messages=create_error_messages("update_member_role", "updating"))
async def update_member_role(organisation_id: str, user_id: str, role_id: str) -> bool:
    """Update member's role in organisation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    query = table.update({
        "role_id": role_id,
        "updated_at": NOW_CONSTANT
    }).eq("user_id", user_id).eq("organization_id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)


# ============================================================================
# ORGANISATION SETTINGS OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_organisation_settings",
    custom_messages=create_error_messages("get_organisation_settings", "getting"))
async def get_organisation_settings(organisation_id: str) -> Dict[str, Any]:
    """Get organisation settings."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("settings").eq("id", organisation_id)
    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0].get("settings", {})
    return {}


@handle_database_errors(
    "update_organisation_settings",
    custom_messages=create_error_messages("update_organisation_settings", "updating"))
async def update_organisation_settings(organisation_id: str, settings: Dict[str, Any]) -> bool:
    """Update organisation settings."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({
        "settings": settings,
        "updated_at": NOW_CONSTANT
    }).eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)


@handle_database_errors(
    "get_organisation_preferences",
    custom_messages=create_error_messages("get_organisation_preferences", "getting"))
async def get_organisation_preferences(organisation_id: str) -> Dict[str, Any]:
    """Get organisation preferences."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("preferences").eq("id", organisation_id)
    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0].get("preferences", {})
    return {}


@handle_database_errors(
    "update_organisation_preferences",
    custom_messages=create_error_messages("update_organisation_preferences", "updating"))
async def update_organisation_preferences(organisation_id: str,preferences: Dict[str, Any]) -> bool:
    """Update organisation preferences."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({
        "preferences": preferences,
        "updated_at": NOW_CONSTANT
    }).eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)

# ============================================================================
# ORGANISATION STATISTICS OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_organisation_statistics",
    custom_messages=create_error_messages("get_organisation_statistics", "getting"))
async def get_organisation_statistics(organisation_id: str) -> Dict[str, Any]:
    """Get statistics for an organisation."""
    supabase = await get_supabase_admin_client()

    # Get member count
    members_table = supabase.table("organization_members")
    members_query = members_table.select("id", count="exact").eq("organization_id", organisation_id)
    members_result = await members_query.execute()

    member_count = members_result.count if members_result.count is not None else 0

    # Get role count
    roles_table = supabase.table("roles")
    roles_query = roles_table.select("id", count="exact").eq("organization_id", organisation_id)
    roles_result = await roles_query.execute()

    role_count = roles_result.count if roles_result.count is not None else 0

    # Get permission count
    permissions_table = supabase.table("permissions")
    permissions_query = permissions_table.select(
        "id", count="exact"
        ).eq("organization_id", organisation_id)
    permissions_result = await permissions_query.execute()

    permission_count = permissions_result.count if permissions_result.count is not None else 0

    return {
        "member_count": member_count,
        "role_count": role_count,
        "permission_count": permission_count
    }


@handle_database_errors(
    "get_organisation_member_stats",
    custom_messages=create_error_messages("get_organisation_member_stats", "getting"))
async def get_organisation_member_stats(organisation_id: str) -> Dict[str, Any]:
    """Get member statistics for an organisation."""
    supabase = await get_supabase_admin_client()

    # Get total members
    members_table = supabase.table("organization_members")
    total_query = members_table.select("id", count="exact").eq("organization_id", organisation_id)
    total_result = await total_query.execute()

    total_members = total_result.count if total_result.count is not None else 0

    # Get active members
    active_query = members_table.select(
        "id", count="exact").eq(
        "organization_id", organisation_id).eq(
        "status", "active")
    active_result = await active_query.execute()

    active_members = active_result.count if active_result.count is not None else 0

    # Get banned members
    banned_query = members_table.select(
        "id", count="exact").eq(
        "organization_id", organisation_id).eq(
        "status", "banned")
    banned_result = await banned_query.execute()

    banned_members = banned_result.count if banned_result.count is not None else 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "banned_members": banned_members
    }


@handle_database_errors(
    "get_organisation_activity_stats",
    custom_messages=create_error_messages("get_organisation_activity_stats", "getting"))
async def get_organisation_activity_stats(organisation_id: str) -> Dict[str, Any]:
    """Get activity statistics for an organisation."""
    supabase = await get_supabase_admin_client()

    # Get recent activity (last 30 days)
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()

    table = supabase.table("organization_members")
    query = table.select("id", count="exact").eq("organization_id", organisation_id).gte(
        "last_active_at", thirty_days_ago
    )
    recent_activity_result = await query.execute()

    if recent_activity_result.count is not None:
        recent_activity = recent_activity_result.count
    else:
        recent_activity = 0

    return {
        "recent_activity_count": recent_activity,
        "period_days": 30
    }

# ============================================================================
# ORGANISATION BULK OPERATIONS
# ============================================================================

@handle_database_errors(
    "bulk_delete_organisations",
    custom_messages=create_error_messages("bulk_delete_organisations", "bulk deleting"))
async def bulk_delete_organisations(organisation_ids: List[str]) -> int:
    """Bulk delete multiple organisations."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.delete().in_("id", organisation_ids)
    result = await query.execute()

    return len(result.data) if result.data else 0


@handle_database_errors(
    "bulk_add_members",
    custom_messages=create_error_messages("bulk_add_members", "bulk adding"))
async def bulk_add_members(
    organisation_id: str,
    members_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Bulk add multiple members to organisation."""
    supabase = await get_supabase_admin_client()

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
            "created_at": NOW_CONSTANT,
            "updated_at": NOW_CONSTANT
        })

    table = supabase.table("organization_members")
    query = table.insert(member_records)
    result = await query.execute()

    return _has_result_data(result)

# ============================================================================
# ORGANISATION PERMISSIONS OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_default_permissions_for_organisation",
    custom_messages=create_error_messages("create_default_permissions_for_organisation","creating")
)
async def create_default_permissions_for_organisation(organisation_id: str) -> List[str]:
    """Create default permissions for new organisation and return permission IDs."""
    supabase = await get_supabase_admin_client()

    # Prepare permission records for bulk insert
    permission_records = []
    for code, name, description, category in DEFAULT_PERMISSIONS:
        permission_records.append({
            "organization_id": organisation_id,
            "code": code,
            "name": name,
            "description": description,
            "category": category,
            "created_at": NOW_CONSTANT
        })

    # Insert permissions with conflict handling
    table = supabase.table("permissions")
    upsert_query = table.upsert(permission_records, on_conflict="organization_id,code")
    result = await upsert_query.execute()

    if result.data:
        return [str(perm["id"]) for perm in result.data]
    return []


@handle_database_errors(
    "create_super_admin_role",
    custom_messages=create_error_messages("create_super_admin_role", "creating"))
async def create_super_admin_role(organisation_id: str) -> Dict[str, Any]:
    """Create super admin role for organisation."""
    supabase = await get_supabase_admin_client()

    role_id = str(uuid.uuid4())
    role_record = {
        "id": role_id,
        "name": "admin",
        "description": "Full administrative access to all system features",
        "organization_id": organisation_id,
        "is_default": True,
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT
    }

    table = supabase.table("roles")
    # Use returning: 'minimal' to avoid SELECT permission requirements
    query = table.insert(role_record, returning="minimal")
    await query.execute()

    # Since we used returning="minimal", return the role data we just inserted
    return {
        "id": role_id,
        "name": role_record["name"],
        "description": role_record["description"],
        "organization_id": role_record["organization_id"],
        "is_default": role_record["is_default"],
        "created_at": role_record["created_at"],
        "updated_at": role_record["updated_at"]
    }


@handle_database_errors(
    "assign_all_permissions_to_role",
    custom_messages=create_error_messages("assign_all_permissions_to_role", "assigning"))
async def assign_all_permissions_to_role(role_id: str, organisation_id: str) -> bool:
    """Assign all permissions to a role."""
    supabase = await get_supabase_admin_client()

    # First, get all permissions for the organisation
    permissions_table = supabase.table("permissions")
    select_query = permissions_table.select("id")
    permissions_query = select_query.eq("organization_id", organisation_id)
    permissions_result = await permissions_query.execute()

    if not permissions_result.data:
        return False

    # Prepare role-permission assignments
    role_permission_records = []
    for permission in permissions_result.data:
        role_permission_records.append({
            "organization_id": organisation_id,
            "role_id": role_id,
            "permission_id": permission["id"],
            "created_at": NOW_CONSTANT
        })

    # Insert role-permission assignments with conflict handling
    role_permissions_table = supabase.table("role_permissions")
    upsert_query = role_permissions_table.upsert(
        role_permission_records,
        on_conflict="organization_id,role_id,permission_id"
    )
    result = await upsert_query.execute()

    return _has_result_data(result)


@handle_database_errors(
    "get_organisation_permissions",
    custom_messages=create_error_messages("get_organisation_permissions", "getting"))
async def get_organisation_permissions(organisation_id: str) -> List[Dict[str, Any]]:
    """Get all permissions for an organisation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("permissions")
    select_query = table.select(
        "id, name, code, category, description, created_at, updated_at"
    )
    query = select_query.eq("organization_id", organisation_id)
    result = await query.execute()

    return _get_result_data(result)

# ============================================================================
# ORGANISATION CLEANUP OPERATIONS
# ============================================================================

@handle_database_errors(
    "cleanup_organisation_data",
    custom_messages=create_error_messages("cleanup_organisation_data", "cleaning up"))
async def cleanup_organisation_data(organisation_id: str) -> Dict[str, int]:
    """Clean up all data associated with an organisation."""
    supabase = await get_supabase_admin_client()

    # Delete organization members
    members_table = supabase.table("organization_members")
    members_query = members_table.delete().eq("organization_id", organisation_id)
    members_result = await members_query.execute()

    members_deleted = len(members_result.data) if members_result.data else 0

    # Delete roles
    roles_table = supabase.table("roles")
    roles_query = roles_table.delete().eq("organization_id", organisation_id)
    roles_result = await roles_query.execute()

    roles_deleted = len(roles_result.data) if roles_result.data else 0

    # Delete permissions
    permissions_table = supabase.table("permissions")
    permissions_query = permissions_table.delete().eq("organization_id", organisation_id)
    permissions_result = await permissions_query.execute()

    permissions_deleted = len(permissions_result.data) if permissions_result.data else 0

    return {
        "members_deleted": members_deleted,
        "roles_deleted": roles_deleted,
        "permissions_deleted": permissions_deleted
    }

@handle_database_errors(
    "archive_organisation",
    custom_messages=create_error_messages("archive_organisation", "archiving"))
async def archive_organisation(organisation_id: str) -> bool:
    """Archive an organisation (soft delete)."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"status": "archived","updated_at": NOW_CONSTANT
    }).eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)

@handle_database_errors(
    "restore_organisation",
    custom_messages=create_error_messages("restore_organisation", "restoring"))
async def restore_organisation(organisation_id: str) -> bool:
    """Restore an archived organisation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"status": "active","updated_at": NOW_CONSTANT
        }).eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)

# ============================================================================
# ORGANISATION MONITORING OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_organisation_health_status",
    custom_messages=create_error_messages("get_organisation_health_status", "getting"))
async def get_organisation_health_status(organisation_id: str) -> Dict[str, Any]:
    """Get health status of an organisation."""
    supabase = await get_supabase_admin_client()

    # Get organization status
    table = supabase.table("organizations")
    select_query = table.select("status, created_at, updated_at")
    query = select_query.eq("id", organisation_id)
    org_result = await query.execute()

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

@handle_database_errors(
    "get_organisation_usage_stats",
    custom_messages=create_error_messages("get_organisation_usage_stats", "getting"))
async def get_organisation_usage_stats(organisation_id: str) -> Dict[str, Any]:
    """Get usage statistics for an organisation."""
    supabase = await get_supabase_admin_client()

    # Get member count
    members_table = supabase.table("organization_members")
    members_select = members_table.select("id", count="exact")
    members_query = members_select.eq("organization_id", organisation_id)
    members_result = await members_query.execute()

    member_count = members_result.count if members_result.count is not None else 0

    # Get role count
    roles_table = supabase.table("roles")
    roles_select = roles_table.select("id", count="exact")
    roles_query = roles_select.eq("organization_id", organisation_id)
    roles_result = await roles_query.execute()

    role_count = roles_result.count if roles_result.count is not None else 0

    # Convert MagicMock to int if needed
    member_count = int(member_count) if isinstance(member_count, (int, float)) else 0
    role_count = int(role_count) if isinstance(role_count, (int, float)) else 0

    return {
        "member_count": member_count,
        "role_count": role_count,
        "usage_percentage": min(100, (member_count / 100) * 100)  # Assuming 100 is max
    }

@handle_database_errors(
    "get_organisation_compliance_status",
    custom_messages=create_error_messages("get_organisation_compliance_status", "getting"))
async def get_organisation_compliance_status(organisation_id: str) -> Dict[str, Any]:
    """Get compliance status for an organisation."""
    supabase = await get_supabase_admin_client()

    # Get organization status
    table = supabase.table("organizations")
    select_query = table.select("status, created_at")
    query = select_query.eq("id", organisation_id)
    org_result = await query.execute()

    if not org_result.data or len(org_result.data) == 0:
        return {"compliant": False, "status": "not_found"}

    org_data = org_result.data[0]

    # Basic compliance check
    is_active = org_data.get("status") == "active"

    return {
        "compliant": is_active,
        "status": org_data.get("status"),
        "created_at": org_data.get("created_at")
    }
