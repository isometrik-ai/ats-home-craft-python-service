"""User Database Operations Module
This module contains all user-related database operations.
All SQL queries for user management should be centralized here.
"""

from datetime import UTC, datetime
from typing import Any

from apps.user_service.app.schemas.users import UserListItem
from libs.shared_db.supabase_db.db import (
    get_fresh_supabase_admin_client,
    get_supabase_admin_client,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("user_operations")


async def get_user_profile_by_id(user_id: str, organization_id: str) -> dict[str, Any] | None:
    """Get user profile by user ID and organization ID.
    Args:
        user_id: User ID
        organization_id: Organization ID
    Returns:
        dict containing the user profile or None if not found
    """
    if not user_id or user_id == "None":
        logger.error("Invalid user_id provided: %s", user_id)
        return None

    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("organization_members")
        .select(
            "id, user_id, email, full_name, first_name, last_name,avatar_url, salutation, "
            "phone, timezone, role_id, status, created_at, updated_at, last_active_at, joined_at, "
            "organization_id, roles(id, name, description)"
        )
        .eq("user_id", user_id)
    )

    if organization_id and organization_id != "None":
        query = query.eq("organization_id", organization_id)

    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    if organization_id and organization_id != "None":
        fallback_query = (
            supabase.table("organization_members")
            .select(
                "id, user_id, email, full_name, first_name, last_name,avatar_url, "
                "phone, timezone, role_id, status, created_at, updated_at,"
                "last_active_at, joined_at, organization_id, roles(id, name, description)"
            )
            .eq("user_id", user_id)
        )
        fallback_result = await fallback_query.execute()
        if fallback_result.data and len(fallback_result.data) > 0:
            return fallback_result.data[0]
    return None


async def get_user_permissions(user_id: str, organization_id: str) -> list[dict[str, Any]]:
    """Get user permissions through their role.
    Args:
        user_id: User ID
        organization_id: Organization ID
    Returns:
        list of permissions
    """
    if not user_id or user_id == "None":
        logger.error("Invalid user_id provided: %s", user_id)
        return []

    supabase = await get_supabase_admin_client()

    user_query = supabase.table("organization_members").select("role_id").eq("user_id", user_id)

    if organization_id and organization_id != "None":
        user_query = user_query.eq("organization_id", organization_id)

    user_result = await user_query.execute()

    if not user_result.data or len(user_result.data) == 0:
        return []

    role_id = user_result.data[0]["role_id"]

    permissions_query = (
        supabase.table("role_permissions")
        .select("permissions(id, name, code, category, description)")
        .eq("role_id", role_id)
    )

    if organization_id and organization_id != "None":
        permissions_query = permissions_query.eq("organization_id", organization_id)

    result = await permissions_query.execute()

    permissions = []
    if result.data:
        for item in result.data:
            if item.get("permissions"):
                permissions.append(item["permissions"])

    return permissions


async def create_new_user(user_data: dict[str, Any]) -> dict[str, Any]:
    """Create a new user in the organization.
    Args:
        user_data: User data
    Returns:
        dict containing the new user
    """
    supabase = await get_supabase_admin_client()

    user_record = {
        "user_id": user_data["user_id"],
        "email": user_data["email"],
        "first_name": user_data.get("first_name", None),
        "last_name": user_data.get("last_name", None),
        "salutation": user_data.get("salutation", None),
        "phone": user_data.get("phone"),
        "timezone": user_data.get("timezone", "UTC"),
        "role_id": user_data.get("role_id"),
        "status": user_data.get("status", "active"),
        "organization_id": user_data.get("organization_id"),
        "isometrik_user_id": user_data.get("isometrik_user_id", None),
        "created_at": datetime.now(UTC),
        "joined_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    result = await supabase.table("organization_members").insert(user_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def update_user_info(
    user_id: str, organization_id: str, update_data: dict[str, Any]
) -> dict[str, Any]:
    """Update user information.
    Args:
        user_id: User ID
        organization_id: Organization ID
        update_data: Update data
    Returns:
        dict containing the updated user
    """
    supabase = await get_fresh_supabase_admin_client()

    update_payload = {}

    for field, value in update_data.items():
        if value is not None:
            update_payload[field] = value

    if not update_payload:
        return {}

    update_payload["updated_at"] = datetime.now(UTC)

    result = (
        await supabase.table("organization_members")
        .update(update_payload)
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def delete_user(user_id: str, organization_id: str) -> bool:
    """Delete user from organization.
    Args:
        user_id: User ID
        organization_id: Organization ID
    Returns:
        bool: True if user was deleted successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("organization_members")
        .delete()
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


# delete auth user is moved to libs.shared_db.supabase_db.admin_operations.user.py


async def check_user_exists(email: str, organization_id: str) -> bool:
    """Check if user exists in organization.
    Args:
        email: Email address
        organization_id: Organization ID
    Returns:
        bool: True if user exists, False otherwise
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("organization_members")
        .select("id")
        .eq("email", email)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def check_phone_exists_for_other_user(
    phone: str, organization_id: str, user_id: str = None
) -> bool:
    """Check if phone number exists for another user.
    Args:
        phone: Phone number
        organization_id: Organization ID
        user_id: User ID
    Returns:
        bool: True if phone number exists for another user, False otherwise
    """
    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("organization_members")
        .select("id")
        .eq("phone", phone)
        .eq("organization_id", organization_id)
    )
    if user_id:
        query = query.neq("user_id", user_id)
    result = await query.execute()

    return len(result.data) > 0 if result.data else False


# ============================================================================
# USER LISTING AND SEARCH
# ============================================================================


async def get_users_details_list(
    organization_id: str, search: str | None = None, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Get paginated list of users with optional search.
    Args:
        organization_id: Organization ID
        search: Search query
        limit: Limit
        offset: Offset
    Returns:
        list of users
    """
    supabase = await get_fresh_supabase_admin_client()

    query = (
        supabase.table("organization_members")
        .select(
            "id, user_id, email, first_name, last_name, salutation, phone, "
            "timezone, role_id, status, "
            "created_at, updated_at, last_active_at"
        )
        .eq("organization_id", organization_id)
    )

    if search:
        query = query.or_(
            f"email.ilike.%{search}%,"
            f"first_name.ilike.%{search}%,"
            f"last_name.ilike.%{search}%,"
            f"salutation.ilike.%{search}%,"
            f"phone.ilike.%{search}%"
        )

    result = await query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

    return result.data if result.data else []


async def get_users_total_count(organization_id: str, search: str | None = None) -> int:
    """Get total count of users matching search criteria.
    Args:
        organization_id: Organization ID
        search: Search query
    Returns:
        int: Total count of users
    """
    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("organization_members")
        .select("id", count="exact")
        .eq("organization_id", organization_id)
    )

    if search:
        query = query.or_(
            f"email.ilike.%{search}%,full_name.ilike.%{search}%,phone.ilike.%{search}%"
        )

    result = await query.execute()

    return result.count if result.count is not None else 0


async def update_user_activity(user_id: str, organization_id: str) -> None:
    """Update user's last active timestamp.
    Args:
        user_id: User ID
        organization_id: Organization ID
    """
    supabase = await get_supabase_admin_client()

    await (
        supabase.table("organization_members")
        .update({"last_active_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .eq("status", "active")
        .execute()
    )


async def suspend_user(user_id: str, organization_id: str) -> bool:
    """Suspend a user in the organization.
    Args:
        user_id: User ID
        organization_id: Organization ID
    Returns:
        bool: True if user was suspended successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    update_data = {"status": "suspended", "updated_at": datetime.now(UTC)}

    result = (
        await supabase.table("organization_members")
        .update(update_data)
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def revoke_suspended_user(user_id: str, organization_id: str) -> bool:
    """Revoke a suspended user in the organization.
    Args:
        user_id: User ID
        organization_id: Organization ID
    Returns:
        bool: True if user was revoked successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    update_data = {"status": "active", "updated_at": datetime.now(UTC)}

    result = (
        await supabase.table("organization_members")
        .update(update_data)
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def update_user_email(user_id: str, organization_id: str, new_email: str) -> bool:
    """Update user's email address.
    Args:
        user_id: User ID
        organization_id: Organization ID
        new_email: New email address
    Returns:
        bool: True if user's email address was updated successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("organization_members")
        .update({"email": new_email, "updated_at": datetime.now(UTC)})
        .eq("user_id", user_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def get_auth_user_by_email(email: str) -> dict[str, Any] | None:
    """Get user from auth.users table by email.
    Args:
        email: Email address
    Returns:
        dict containing the user or None if not found
    """
    supabase = await get_fresh_supabase_admin_client()

    total_users = 0
    while total_users < 1000:
        result = await supabase.auth.admin.list_users(page=total_users // 1000, per_page=1000)
        for user in result:
            if user.email == email:
                return user
        total_users += 1000
    logger.error("User with email %s not found", email)
    return None


async def get_organization_member_status_by_email(email: str) -> str | None:
    """Get organization member status by email.
    Args:
        email: Email address
    Returns:
        str: Organization member status or None if not found
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("organization_members")
        .select("status")
        .eq("email", email)
        .limit(1)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]["status"]
    return None


async def transform_users(
    users_data: list[dict[str, Any]], organization_id: str
) -> list[UserListItem]:
    """Transform users data to UserListItem.
    Args:
        users_data: List of users data
        organization_id: Organization ID
    Returns:
        list of UserListItem
    """
    if not users_data:
        return []

    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("role_permissions")
        .select("id", count="exact")
        .eq("organization_id", organization_id)
        .eq("role_id", users_data[0]["role_id"])
        .execute()
    )
    if result and hasattr(result, "count") and result.count is not None:
        permissions_count = result.count
    else:
        permissions_count = 0

    return [
        UserListItem(
            user_id=str(u["user_id"]),
            email=u["email"],
            salutation=u["salutation"],
            first_name=u["first_name"],
            last_name=u["last_name"],
            phone=u["phone"],
            role_id=str(u["role_id"]),
            status=u["status"],
            joined_at=(u["joined_at"] if u.get("joined_at") else datetime.now().isoformat()),
            last_active_at=(u["last_active_at"] if u.get("last_active_at") else None),
            permissions_count=permissions_count,
        )
        for u in users_data
    ]
