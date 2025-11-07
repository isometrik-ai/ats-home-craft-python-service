"""
User Database Operations Module

This module contains all user-related database operations.
All SQL queries for user management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- User profile operations
- User CRUD operations
- User permission operations
- User activity tracking
- User search and filtering
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.users import UserListItem
from libs import NOW_CONSTANT
from libs.shared_db.supabase_db.db import get_supabase_admin_client, get_fresh_supabase_admin_client
from .exception_handling import handle_database_errors, create_error_messages
#
# Initialize logger
logger = get_logger("user_operations")


# ============================================================================
# USER PROFILE OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_user_profile_by_id",
    custom_messages=create_error_messages("get_user_profile_by_id", "getting"))
async def get_user_profile_by_id(user_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
    """Get user profile by user ID and organization ID."""
    # Validate user_id
    if not user_id or user_id == "None":
        logger.error("Invalid user_id provided: %s", user_id)
        return None

    supabase = await get_supabase_admin_client()

    # Build query based on whether organization_id is provided
    query = supabase.table("organization_members").select(
        "id, user_id, email, full_name, first_name, last_name,avatar_url, "
        "phone, timezone, role_id, status, created_at, updated_at, last_active_at, joined_at, "
        "organization_id, roles(id, name, description)"
    ).eq("user_id", user_id)

    # Only add organization_id filter if it's provided and not None
    if organization_id and organization_id != "None":
        query = query.eq("organization_id", organization_id)

    result = await query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    else:
        # If no results found, try to find the user in any organization
        if organization_id and organization_id != "None":
            fallback_query = supabase.table("organization_members").select(
                "id, user_id, email, full_name, first_name, last_name,avatar_url, "
                "phone, timezone, role_id, status, created_at, updated_at,"
                "last_active_at, joined_at, organization_id, roles(id, name, description)"
            ).eq("user_id", user_id)
            fallback_result = await fallback_query.execute()
            if fallback_result.data and len(fallback_result.data) > 0:
                return fallback_result.data[0]
    return None


@handle_database_errors(
    "get_user_permissions",
    custom_messages=create_error_messages("get_user_permissions", "getting"))
async def get_user_permissions(user_id: str, organization_id: str) -> List[Dict[str, Any]]:
    """Get user permissions through their role."""
    # Validate user_id
    if not user_id or user_id == "None":
        logger.error("Invalid user_id provided: %s", user_id)
        return []

    supabase = await get_supabase_admin_client()

    # First get the user's role
    user_query = supabase.table("organization_members").select("role_id").eq("user_id", user_id)

    # Only add organization_id filter if it's provided and not None
    if organization_id and organization_id != "None":
        user_query = user_query.eq("organization_id", organization_id)

    user_result = await user_query.execute()

    if not user_result.data or len(user_result.data) == 0:
        return []

    role_id = user_result.data[0]["role_id"]

    # Get permissions for the role
    permissions_query = supabase.table("role_permissions").select(
        "permissions(id, name, code, category, description)"
    ).eq("role_id", role_id)

    # Only add organization_id filter if it's provided and not None
    if organization_id and organization_id != "None":
        permissions_query = permissions_query.eq("organization_id", organization_id)

    result = await permissions_query.execute()

    permissions = []
    if result.data:
        for item in result.data:
            if item.get("permissions"):
                permissions.append(item["permissions"])

    return permissions


# ============================================================================
# USER CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_new_user",
    custom_messages=create_error_messages("create_new_user", "creating"))
async def create_new_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new user in the organization."""
    supabase = await get_supabase_admin_client()

    user_record = {
        "user_id": user_data["user_id"],
        "email": user_data["email"],
        "full_name": user_data["full_name"],
        "phone": user_data.get("phone"),
        "timezone": user_data.get("timezone", "UTC"),
        "role_id": user_data.get("role_id"),
        "status": user_data.get("status", "active"),
        "organization_id": user_data.get("organization_id"),
        "created_at": NOW_CONSTANT,
        "joined_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT
    }

    result = await supabase.table("organization_members").insert(user_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "update_user_info",
    custom_messages=create_error_messages("update_user_info", "updating"))
async def update_user_info(
    user_id: str,
    organization_id: str,
    update_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Update user information."""
    supabase = await get_supabase_admin_client()

    # Prepare update data
    update_payload = {}

    for field, value in update_data.items():
        if value is not None:
            update_payload[field] = value

    if not update_payload:
        # No fields to update
        return {}

    # Add updated_at
    update_payload["updated_at"] = NOW_CONSTANT

    result = await supabase.table("organization_members").update(update_payload).eq(
        "user_id", user_id
    ).eq("organization_id", organization_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "delete_user",
    custom_messages=create_error_messages("delete_user", "deleting"))
async def delete_user(user_id: str, organization_id: str) -> bool:
    """Delete user from organization."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("organization_members").delete().eq(
        "user_id", user_id
    ).eq("organization_id", organization_id).execute()

    return len(result.data) > 0 if result.data else False

# delete auth user is moved to libs.shared_db.supabase_db.admin_operations.user.py

@handle_database_errors(
    "check_user_exists",
    custom_messages=create_error_messages("check_user_exists", "checking"))
async def check_user_exists(email: str, organization_id: str) -> bool:
    """Check if user exists in organization."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("organization_members").select("id").eq(
        "email", email
    ).eq("organization_id", organization_id).execute()

    return len(result.data) > 0 if result.data else False


@handle_database_errors(
    "check_phone_exists_for_other_user",
    custom_messages=create_error_messages("check_phone_exists_for_other_user", "checking"))
async def check_phone_exists_for_other_user(
    phone: str,
    organization_id: str,
    user_id: str = None
) -> bool:
    """Check if phone number exists for another user."""
    supabase = await get_supabase_admin_client()

    query = supabase.table("organization_members").select("id").eq(
        "phone", phone
    ).eq("organization_id", organization_id)
    if user_id:
        query = query.neq("user_id", user_id)
    result = await query.execute()

    return len(result.data) > 0 if result.data else False


# ============================================================================
# USER LISTING AND SEARCH
# ============================================================================

@handle_database_errors(
    "get_users_details_list",
    custom_messages=create_error_messages("get_users_details_list", "getting"))
async def get_users_details_list(organization_id: str, search: Optional[str] = None,
                        limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get paginated list of users with optional search."""
    supabase = await get_supabase_admin_client()

    # Build the query with filters
    query = supabase.table("organization_members").select(
        "id, user_id, email, full_name, first_name, last_name, phone, timezone, role_id, status, "
        "created_at, updated_at, last_active_at"
    ).eq("organization_id", organization_id)

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


@handle_database_errors(
    "get_users_total_count",
    custom_messages=create_error_messages("get_users_total_count", "getting"))
async def get_users_total_count(organization_id: str, search: Optional[str] = None) -> int:
    """Get total count of users matching search criteria."""
    supabase = await get_supabase_admin_client()

    # Build the count query with filters
    query = supabase.table("organization_members").select("id", count="exact").eq(
        "organization_id", organization_id
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




# # ============================================================================
# # USER ACTIVITY OPERATIONS
# # ============================================================================

@handle_database_errors(
    "update_user_activity",
    custom_messages=create_error_messages("update_user_activity", "updating"))
async def update_user_activity(user_id: str, organization_id: str) -> None:
    """Update user's last active timestamp."""
    supabase = await get_supabase_admin_client()

    await supabase.table("organization_members").update({
        "last_active_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT
    }).eq("user_id", user_id
    ).eq("organization_id", organization_id
    ).eq("status", "active").execute()


# # ============================================================================
# # USER STATUS OPERATIONS
# # ============================================================================

@handle_database_errors(
    "suspend_user",
    custom_messages=create_error_messages("suspend_user", "suspending"))
async def suspend_user(user_id: str, organization_id: str) -> bool:
    """Suspend a user in the organization."""
    supabase = await get_supabase_admin_client()

    update_data = {
        "status": "suspended",
        "updated_at": NOW_CONSTANT
    }

    result = await supabase.table("organization_members").update(update_data).eq(
        "user_id", user_id
    ).eq("organization_id", organization_id).execute()

    return len(result.data) > 0 if result.data else False

@handle_database_errors(
    "revoke_suspended_user",
    custom_messages=create_error_messages("revoke_suspended_user", "revoking"))
async def revoke_suspended_user(user_id: str, organization_id: str) -> bool:
    """Revoke a suspended user in the organization."""
    supabase = await get_supabase_admin_client()

    update_data = {
        "status": "active",
        "updated_at": NOW_CONSTANT
    }

    result = await supabase.table("organization_members").update(update_data).eq(
        "user_id", user_id
    ).eq("organization_id", organization_id).execute()

    return len(result.data) > 0 if result.data else False


# # ============================================================================
# # USER EMAIL OPERATIONS
# # ============================================================================

@handle_database_errors(
    "update_user_email",
    custom_messages=create_error_messages("update_user_email", "updating"))
async def update_user_email(user_id: str, organization_id: str, new_email: str) -> bool:
    """Update user's email address."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("organization_members").update({
        "email": new_email,
        "updated_at": NOW_CONSTANT
    }).eq("user_id", user_id).eq("organization_id", organization_id).execute()

    return len(result.data) > 0 if result.data else False


# # ============================================================================
# # USER VALIDATION OPERATIONS
# # ============================================================================


# # ============================================================================
# # BULK USER OPERATIONS
# # ============================================================================


# ============================================================================
# AUTH USER OPERATIONS
# ============================================================================

@handle_database_errors(
    "get_auth_user_by_email",
    custom_messages=create_error_messages("get_auth_user_by_email", "getting"))
async def get_auth_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get user from auth.users table by email."""
    supabase = await get_fresh_supabase_admin_client()

    result = await supabase.auth.admin.list_users()

    for user in result:
        if user.email == email:
            return user
    logger.error("User with email %s not found", email)
    return None


@handle_database_errors(
    "get_organization_member_status_by_email",
    custom_messages=create_error_messages("get_organization_member_status_by_email", "getting"))
async def get_organization_member_status_by_email(email: str) -> Optional[str]:
    """Get organization member status by email."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("organization_members").select(
        "status"
    ).eq("email", email).limit(1).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]["status"]
    return None


# ============================================================================
# MISCHELLANEOUS OPERATIONS
# ============================================================================

@handle_database_errors(
    "transform_users",
    custom_messages=create_error_messages("transform_users", "transforming"))
async def transform_users(users_data, organization_id):
    """
    Build Proper response for User list
    """
    if not users_data:
        return []

    supabase = await get_supabase_admin_client()

    result = await supabase.table("role_permissions").select(
        "id", count="exact").eq("organization_id", organization_id).eq(
        "role_id", users_data[0]["role_id"]).execute()
    if result and hasattr(result, "count") and result.count is not None:
        permissions_count = result.count
    else:
        permissions_count = 0

    # Convert DB rows to response objects
    return [
        UserListItem(
            user_id=str(u["user_id"]),
            email=u["email"],
            full_name=u["full_name"],
            first_name=u["first_name"],
            last_name=u["last_name"],
            phone=u["phone"],
            role_id=str(u["role_id"]),
            status=u["status"],
            joined_at=(
                u["joined_at"]
                if u.get("joined_at")
                else datetime.now().isoformat()
            ),
            last_active_at=(
                u["last_active_at"] if u.get("last_active_at") else None
            ),
            permissions_count=permissions_count,
        )
        for u in users_data
    ]
