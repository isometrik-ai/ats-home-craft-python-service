"""Organisation Database Operations Module

This module contains all organisation-related database operations.
All SQL queries for organisation management should be centralized here.
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from apps.user_service.app.schemas.auth import PlanType
from libs.shared_db.supabase_db.admin_operations.user import (
    get_user_by_id,
    update_metadata_of_user,
)
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_utils.common_query import DEFAULT_PERMISSIONS
from libs.shared_utils.http_exceptions import ServiceUnavailableException
from libs.shared_utils.isometrik_service import create_isometrik_user
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("organisation_operations")


async def _get_supabase_client():
    """Get Supabase admin client."""
    return await get_supabase_admin_client()


def _has_result_data(result: Any) -> bool:
    """Check if result has data."""
    return len(result.data) > 0 if result.data else False


def _get_result_data(result: Any, default: Any = None) -> Any:
    """Get result data with default fallback."""
    return result.data if result.data else (default or [])


def _serialize_value(value: Any) -> Any:
    """Convert enums and pydantic models into JSON-serializable primitives."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_unset=True, exclude_none=True)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    return value


def _apply_search_filter(query: Any, search: str, fields: list[str]) -> Any:
    """Apply search filter to query."""
    if not search:
        return query

    search_conditions = [f"{field}.ilike.%{search}%" for field in fields]
    return query.or_(",".join(search_conditions))


def _apply_pagination(query: Any, limit: int, offset: int) -> Any:
    """Apply pagination to query."""
    return query.order("created_at", desc=True).range(offset, offset + limit - 1)


# ============================================================================
# ORGANISATION CRUD OPERATIONS
# ============================================================================


async def create_new_organisation(organisation_data: dict[str, Any]) -> dict[str, Any]:
    """Create a new organisation.
    Args:
        organisation_data: Organisation data
    Returns:
        dict containing the new organisation
    """
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
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "created_by_id": organisation_data.get("user_id"),
    }

    settings = {}

    address = organisation_data.get("address")
    if address is not None:
        settings["address"] = _serialize_value(address)

    settings["practice_areas"] = {
        "primary": _serialize_value(organisation_data.get("primary_practice_areas")),
        "secondary": _serialize_value(organisation_data.get("secondary_practice_areas")),
        "specializations": _serialize_value(organisation_data.get("specializations")),
    }

    settings["preferred_integration"] = _serialize_value(
        organisation_data.get("preferred_integration")
    )
    settings["need_help_importing_data"] = organisation_data.get("need_help_importing_data", None)
    settings["need_migration_assistance"] = organisation_data.get("need_migration_assistance", None)
    settings["compliance_security"] = _serialize_value(organisation_data.get("compliance_security"))
    settings["enterprise_features"] = _serialize_value(organisation_data.get("enterprise_features"))

    settings["isometrik_application_details"] = _serialize_value(
        organisation_data.get("isometrik_application_details")
    )

    org_record["settings"] = settings

    subscription_dict = {
        "start_date": datetime.now(UTC),
        "end_date": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "plan_type": PlanType.TRIAL,
        "max_users": 5,
    }

    org_record["subscription"] = subscription_dict

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
        "status": organisation_data.get("status", "trial"),
        "industry": organisation_data.get("industry"),
        "company_size": organisation_data.get("company_size"),
        "description": organisation_data.get("description"),
        "referral_source": organisation_data.get("referral_source"),
        "subscription": organisation_data.get("subscription"),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "created_by_id": organisation_data.get("user_id"),
    }


async def get_organisation_details_by_id(
    organization_id: str,
) -> dict[str, Any] | None:
    """Get organisation details by ID with member_count, mimicking SQL query builder.
    Args:
        organization_id: Organization ID
    Returns:
        dict containing the organisation details or None if not found
    """
    supabase = await get_supabase_admin_client()

    # Fetch organisation with embedded members (only fields needed to compute count)
    table = supabase.table("organizations")
    select_query = table.select(
        "id, name, slug, domain, logo_url, status, timezone, settings, subscription,"
        "description, company_size, created_at, updated_at, organization_members(status)"
    )
    eq_query = select_query.eq("id", organization_id)
    eq_query = eq_query.eq("status", "active")
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


async def update_organisation_details(
    organisation_id: str, organisation_data: dict[str, Any], update_data: dict[str, Any]
) -> dict[str, Any]:
    """Update organisation information, mimicking _build_organization_update_query logic.

    This function mimics the logic from _build_organization_update_query() in organisation.py
    to ensure consistent parameter handling and filtering across the codebase.
    Args:
        organisation_id: Organisation ID
        organisation_data: Organisation data
        update_data: Update data
    Returns:
        dict containing the updated organisation
    """
    # pylint: disable=too-many-branches, too-complex
    supabase = await get_supabase_admin_client()

    # 1️⃣ Collect only keys the client actually sent(mimicking exclude_unset=True,exclude_none=True)
    payload = {k: v for k, v in update_data.items() if v is not None}

    settings_fields = [
        "address",
        "primary_practice_areas",
        "secondary_practice_areas",
        "specializations",
        "preferred_integration",
        "need_help_importing_data",
        "need_migration_assistance",
        "compliance_security",
        "enterprise_features",
    ]

    if any(field in settings_fields for field in payload.keys()):
        # payload["settings"] = {}
        temp_settings = organisation_data
        temp_var = temp_settings["settings"]

        if payload.get("address") is not None:
            temp_var["address"] = payload.get("address")
            payload.pop("address")

        temp_practice_areas = temp_var.get("practice_areas", None)
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
            temp_practice_areas = {
                "primary": None,
                "secondary": None,
                "specializations": None,
            }
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
    payload["updated_at"] = datetime.now(UTC)

    # 4️⃣ Execute update with Supabase SDK (mimicking the WHERE id = $N logic)
    table = supabase.table("organizations")
    result = await table.update(payload).eq("id", organisation_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def delete_organisation(organisation_id: str) -> bool:
    """Delete organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        bool: True if organisation was deleted successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    result = await table.delete().eq("id", organisation_id).execute()

    return _has_result_data(result)


async def check_organisation_exists(organisation_id: str) -> bool:
    """Check if organisation exists.
    Args:
        organisation_id: Organisation ID
    Returns:
        bool: True if organisation exists, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    select_query = table.select("id")
    query = select_query.eq("id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)


async def get_list_of_organisations(
    search: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get paginated list of organisations with optional search and filtering.
    Args:
        search: Search query
        status: Status
        limit: Limit
        offset: Offset
    Returns:
        list of organisations
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


async def get_organisations_count(search: str | None, status: str | None) -> int:
    """Get total count of organisations matching search criteria.
    Args:
        search: Search query
        status: Status
    Returns:
        int: Total count of organisations
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


async def check_organisation_slug_unique(slug: str, exclude_org_id: str | None = None) -> bool:
    """Check if organisation slug is unique.
    Args:
        slug: Slug
        exclude_org_id: Organisation ID to exclude
    Returns:
        bool: True if organisation slug is unique, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("id").eq("slug", slug)

    if exclude_org_id:
        query = query.neq("id", exclude_org_id)

    result = await query.execute()

    return len(result.data) == 0 if result.data else True


async def check_organisation_name_unique(name: str, exclude_org_id: str | None = None) -> bool:
    """Check if organisation name is unique.
    Args:
        name: Name
        exclude_org_id: Organisation ID to exclude
    Returns:
        bool: True if organisation name is unique, False otherwise
    """
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


async def get_organisation_members(
    organisation_id: str, search: str | None = None, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Get members of an organisation.
    Args:
        organisation_id: Organisation ID
        search: Search query
        limit: Limit
        offset: Offset
    Returns:
        list of members
    """
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
            f"email.ilike.%{search}%,full_name.ilike.%{search}%,phone.ilike.%{search}%"
        )

    # Apply pagination and ordering
    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = await query.execute()

    return _get_result_data(result)


async def get_organisation_members_count(organisation_id: str, search: str | None = None) -> int:
    """Get count of organisation members.
    Args:
        organisation_id: Organisation ID
        search: Search query
    Returns:
        int: Total count of organisation members
    """
    supabase = await get_supabase_admin_client()

    # Build the count query with filters
    table = supabase.table("organization_members")
    query = table.select("id", count="exact").eq("organization_id", organisation_id)

    # Apply search filter
    if search:
        query = query.or_(
            f"email.ilike.%{search}%,full_name.ilike.%{search}%,phone.ilike.%{search}%"
        )

    result = await query.execute()

    return result.count if result.count is not None else 0


async def add_member_to_organisation(
    organization_id: str,
    member_data: dict[str, Any],
    isometrik_credentials: dict[str, Any],
) -> dict[str, Any]:
    """Add a member to an organisation.
    Args:
        organization_id: Organization ID
        member_data: Member data
        isometrik_credentials: Isometrik credentials
    Returns:
        dict containing the new member
    """
    supabase = await get_supabase_admin_client()

    isometrik_user_id = None
    isometrik_response = await create_isometrik_user(
        user_id=member_data["user_id"],
        first_name=member_data.get("first_name", None),
        last_name=member_data.get("last_name", None),
        email=member_data["email"],
        isometrik_credentials=isometrik_credentials,
        organization_id=organization_id,
        role=member_data.get("role", "owner"),
        avatar_url=member_data.get("logo_url", None),
    )
    if isometrik_response:
        isometrik_user_id = isometrik_response.get("userId", None)
        if not isometrik_user_id:
            raise ServiceUnavailableException(
                message_key="errors.failed_to_create_isometrik_user",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

    if isometrik_user_id:
        member_data["isometrik_user_id"] = isometrik_user_id

    member_record = {
        "user_id": member_data["user_id"],
        "isometrik_user_id": member_data.get("isometrik_user_id", None),
        "email": member_data["email"],
        "role_id": member_data.get("role_id"),
        "status": member_data.get("status", "active"),
        "organization_id": organization_id,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "joined_at": datetime.now(UTC),
    }

    data = await get_user_by_id(member_data["user_id"])
    member_record["first_name"] = data.user.user_metadata.get(
        "first_name", member_data["first_name"]
    )
    member_record["last_name"] = data.user.user_metadata.get("last_name", member_data["last_name"])
    member_record["phone"] = data.user.user_metadata.get("phone", member_data["phone"])
    member_record["timezone"] = data.user.user_metadata.get("timezone", member_data["timezone"])
    member_record["salutation"] = data.user.user_metadata.get("salutation", None)

    table = supabase.table("organization_members")
    query = table.insert(member_record)
    result = await query.execute()

    await update_metadata_of_user(member_data["user_id"], {"organization_id": organization_id})

    return _has_result_data(result)


async def remove_member_from_organisation(organisation_id: str, user_id: str) -> bool:
    """Remove a member from organisation.
    Args:
        organisation_id: Organisation ID
        user_id: User ID
    Returns:
        bool: True if member was removed successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    query = table.delete().eq("user_id", user_id).eq("organization_id", organisation_id)
    result = await query.execute()

    return _has_result_data(result)


async def update_member_role(organisation_id: str, user_id: str, role_id: str) -> bool:
    """Update member's role in organisation.
    Args:
        organisation_id: Organisation ID
        user_id: User ID
        role_id: Role ID
    Returns:
        bool: True if member's role was updated successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    query = (
        table.update({"role_id": role_id, "updated_at": datetime.now(UTC)})
        .eq("user_id", user_id)
        .eq("organization_id", organisation_id)
    )
    result = await query.execute()

    return _has_result_data(result)


# ============================================================================
# ORGANISATION SETTINGS OPERATIONS
# ============================================================================


async def get_organisation_settings(organisation_id: str) -> dict[str, Any]:
    """Get organisation settings.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation settings
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("settings").eq("id", organisation_id)
    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0].get("settings", {})
    return {}


async def update_organisation_settings(organisation_id: str, settings: dict[str, Any]) -> bool:
    """Update organisation settings.
    Args:
        organisation_id: Organisation ID
        settings: Settings
    Returns:
        bool: True if organisation settings were updated successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"settings": settings, "updated_at": datetime.now(UTC)}).eq(
        "id", organisation_id
    )
    result = await query.execute()

    return _has_result_data(result)


async def get_organisation_preferences(organisation_id: str) -> dict[str, Any]:
    """Get organisation preferences.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation preferences
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.select("preferences").eq("id", organisation_id)
    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0].get("preferences", {})
    return {}


async def update_organisation_preferences(
    organisation_id: str, preferences: dict[str, Any]
) -> bool:
    """Update organisation preferences.
    Args:
        organisation_id: Organisation ID
        preferences: Preferences
    Returns:
        bool: True if organisation preferences were updated successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"preferences": preferences, "updated_at": datetime.now(UTC)}).eq(
        "id", organisation_id
    )
    result = await query.execute()

    return _has_result_data(result)


# ============================================================================
# ORGANISATION STATISTICS OPERATIONS
# ============================================================================


async def get_organisation_statistics(organisation_id: str) -> dict[str, Any]:
    """Get statistics for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation statistics
    """
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
    permissions_query = permissions_table.select("id", count="exact").eq(
        "organization_id", organisation_id
    )
    permissions_result = await permissions_query.execute()

    permission_count = permissions_result.count if permissions_result.count is not None else 0

    return {
        "member_count": member_count,
        "role_count": role_count,
        "permission_count": permission_count,
    }


async def get_organisation_member_stats(organisation_id: str) -> dict[str, Any]:
    """Get member statistics for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation member statistics
    """
    supabase = await get_supabase_admin_client()

    # Get total members
    members_table = supabase.table("organization_members")
    total_query = members_table.select("id", count="exact").eq("organization_id", organisation_id)
    total_result = await total_query.execute()

    total_members = total_result.count if total_result.count is not None else 0

    # Get active members
    active_query = (
        members_table.select("id", count="exact")
        .eq("organization_id", organisation_id)
        .eq("status", "active")
    )
    active_result = await active_query.execute()

    active_members = active_result.count if active_result.count is not None else 0

    # Get banned members
    banned_query = (
        members_table.select("id", count="exact")
        .eq("organization_id", organisation_id)
        .eq("status", "banned")
    )
    banned_result = await banned_query.execute()

    banned_members = banned_result.count if banned_result.count is not None else 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "banned_members": banned_members,
    }


async def get_organisation_activity_stats(organisation_id: str) -> dict[str, Any]:
    """Get activity statistics for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation activity statistics
    """
    supabase = await get_supabase_admin_client()

    # Get recent activity (last 30 days)
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()

    table = supabase.table("organization_members")
    query = (
        table.select("id", count="exact")
        .eq("organization_id", organisation_id)
        .gte("last_active_at", thirty_days_ago)
    )
    recent_activity_result = await query.execute()

    if recent_activity_result.count is not None:
        recent_activity = recent_activity_result.count
    else:
        recent_activity = 0

    return {"recent_activity_count": recent_activity, "period_days": 30}


# ============================================================================
# ORGANISATION BULK OPERATIONS
# ============================================================================


async def bulk_delete_organisations(organisation_ids: list[str]) -> int:
    """Bulk delete multiple organisations.
    Args:
        organisation_ids: List of organisation IDs
    Returns:
        int: Number of organisations deleted
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.delete().in_("id", organisation_ids)
    result = await query.execute()

    return len(result.data) if result.data else 0


async def bulk_add_members(
    organisation_id: str, members_data: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Bulk add multiple members to organisation.
    Args:
        organisation_id: Organisation ID
        members_data: List of member data
    Returns:
        list of members
    """
    supabase = await get_supabase_admin_client()

    # Prepare member records
    member_records = []
    for member_data in members_data:
        member_records.append(
            {
                "user_id": member_data["user_id"],
                "email": member_data["email"],
                "full_name": member_data["full_name"],
                "phone": member_data.get("phone"),
                "timezone": member_data.get("timezone", "UTC"),
                "role_id": member_data.get("role_id"),
                "status": member_data.get("status", "active"),
                "organization_id": organisation_id,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    table = supabase.table("organization_members")
    query = table.insert(member_records)
    result = await query.execute()

    return _has_result_data(result)


# ============================================================================
# ORGANISATION PERMISSIONS OPERATIONS
# ============================================================================


async def create_default_permissions_for_organisation(
    organisation_id: str,
) -> list[str]:
    """Create default permissions for new organisation and return permission IDs.
    Args:
        organisation_id: Organisation ID
    Returns:
        list of permission IDs
    """
    supabase = await get_supabase_admin_client()

    # Prepare permission records for bulk insert
    permission_records = []
    for code, name, description, category in DEFAULT_PERMISSIONS:
        permission_records.append(
            {
                "organization_id": organisation_id,
                "code": code,
                "name": name,
                "description": description,
                "category": category,
                "created_at": datetime.now(UTC),
            }
        )

    # Insert permissions with conflict handling
    table = supabase.table("permissions")
    upsert_query = table.upsert(permission_records, on_conflict="organization_id,code")
    result = await upsert_query.execute()

    if result.data:
        return [str(perm["id"]) for perm in result.data]
    return []


async def create_super_admin_role(organisation_id: str) -> dict[str, Any]:
    """Create super admin role for organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the super admin role
    """
    supabase = await get_supabase_admin_client()

    role_id = str(uuid.uuid4())
    role_record = {
        "id": role_id,
        "name": "admin",
        "description": "Full administrative access to all system features",
        "organization_id": organisation_id,
        "is_default": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
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
        "updated_at": role_record["updated_at"],
    }


async def assign_all_permissions_to_role(role_id: str, organisation_id: str) -> bool:
    """Assign all permissions to a role.
    Args:
        role_id: Role ID
        organisation_id: Organisation ID
    Returns:
        bool: True if permissions were assigned successfully, False otherwise
    """
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
        role_permission_records.append(
            {
                "organization_id": organisation_id,
                "role_id": role_id,
                "permission_id": permission["id"],
                "created_at": datetime.now(UTC),
            }
        )

    # Insert role-permission assignments with conflict handling
    role_permissions_table = supabase.table("role_permissions")
    upsert_query = role_permissions_table.upsert(
        role_permission_records, on_conflict="organization_id,role_id,permission_id"
    )
    result = await upsert_query.execute()

    return _has_result_data(result)


async def get_organisation_permissions(organisation_id: str) -> list[dict[str, Any]]:
    """Get all permissions for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        list of permissions
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("permissions")
    select_query = table.select("id, name, code, category, description, created_at, updated_at")
    query = select_query.eq("organization_id", organisation_id)
    result = await query.execute()

    return _get_result_data(result)


# ============================================================================
# ORGANISATION CLEANUP OPERATIONS
# ============================================================================


async def cleanup_organisation_data(organisation_id: str) -> dict[str, int]:
    """Clean up all data associated with an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the number of members, roles, and permissions deleted
    """
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
        "permissions_deleted": permissions_deleted,
    }


async def archive_organisation(organisation_id: str) -> bool:
    """Archive an organisation (soft delete).
    Args:
        organisation_id: Organisation ID
    Returns:
        bool: True if organisation was archived successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"status": "archived", "updated_at": datetime.now(UTC)}).eq(
        "id", organisation_id
    )
    result = await query.execute()

    return _has_result_data(result)


async def restore_organisation(organisation_id: str) -> bool:
    """Restore an archived organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        bool: True if organisation was restored successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organizations")
    query = table.update({"status": "active", "updated_at": datetime.now(UTC)}).eq(
        "id", organisation_id
    )
    result = await query.execute()

    return _has_result_data(result)


# ============================================================================
# ORGANISATION MONITORING OPERATIONS
# ============================================================================


async def get_organisation_health_status(organisation_id: str) -> dict[str, Any]:
    """Get health status of an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation health status
    """
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
        "updated_at": org_data.get("updated_at"),
    }


async def get_organisation_usage_stats(organisation_id: str) -> dict[str, Any]:
    """Get usage statistics for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation usage statistics
    """
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
        "usage_percentage": min(100, (member_count / 100) * 100),  # Assuming 100 is max
    }


async def get_organisation_compliance_status(organisation_id: str) -> dict[str, Any]:
    """Get compliance status for an organisation.
    Args:
        organisation_id: Organisation ID
    Returns:
        dict containing the organisation compliance status
    """
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
        "created_at": org_data.get("created_at"),
    }
