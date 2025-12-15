"""Role Database Operations Module
This module contains all role-related database operations.
All SQL queries for role management should be centralized here.
"""

from typing import Any

from apps.user_service.app.dependencies.logger import get_logger
from libs import NOW_CONSTANT
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_utils.common_query import ROLE_SELECT_FIELDS

logger = get_logger("role_operations")


async def create_role(
    name: str, description: str, organization_id: str, is_default: bool = False
) -> dict[str, Any]:
    """Create a new role.
    Args:
        name: Role name
        description: Role description
        organization_id: Organization ID
        is_default: Whether the role is the default role
    Returns:
        dict containing the new role
    """
    supabase = await get_supabase_admin_client()

    role_record = {
        "name": name,
        "description": description,
        "organization_id": organization_id,
        "is_default": is_default,
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT,
    }

    result = await supabase.table("roles").insert(role_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def get_role_by_id(role_id: str, organization_id: str) -> dict[str, Any] | None:
    """Get role by ID and organization ID.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        dict containing the role or None if not found
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("roles")
        .select(ROLE_SELECT_FIELDS)
        .eq("id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def update_role(
    role_id: str, organization_id: str, update_data: dict[str, Any]
) -> dict[str, Any]:
    """Update role information.
    Args:
        role_id: Role ID
        organization_id: Organization ID
        update_data: Update data
    Returns:
        dict containing the updated role
    """
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

    result = (
        await supabase.table("roles")
        .update(update_payload)
        .eq("id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def delete_role(role_id: str, organization_id: str) -> bool:
    """Delete role from organization.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        bool: True if role was deleted successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("roles")
        .delete()
        .eq("id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def check_role_exists(role_id: str, organization_id: str) -> bool:
    """Check if role exists in organization.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        bool: True if role exists, False otherwise
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("roles")
        .select("id")
        .eq("id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def get_roles_list(
    organization_id: str, search: str | None = None, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Get paginated list of roles with optional search and filtering.
    Args:
        organization_id: Organization ID
        search: Search query
        limit: Limit
        offset: Offset
    Returns:
        list of roles
    """
    supabase = await get_supabase_admin_client()
    rpc_builder = supabase.rpc(
        "get_roles_list_enriched",
        {
            "p_org_id": organization_id,
            "p_search": search,
            "p_limit": limit,
            "p_offset": offset,
        },
    )

    await rpc_builder.execute()

    execute_fn = getattr(rpc_builder, "execute", None)
    if callable(execute_fn):
        rpc_result = execute_fn()
        if hasattr(rpc_result, "__await__"):
            rpc_result = await rpc_result  # type: ignore[func-returns-value]
        data = getattr(rpc_result, "data", None)
        if isinstance(data, list):
            return data


async def get_roles_count(
    organization_id: str,
    search: str | None = None,
) -> int:
    """Get total count of roles matching search criteria.
    Args:
        organization_id: Organization ID
        search: Search query
    Returns:
        int: Total count of roles
    """
    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("roles")
        .select("id", count="exact")
        .eq("organization_id", organization_id)
        .neq("name", "admin")
    )

    if search:
        query = query.ilike("name", f"%{search}%")

    result = await query.execute()

    return result.count if result.count is not None else 0


async def assign_permissions_to_role(
    role_id: str, organization_id: str, permission_ids: list[str]
) -> bool:
    """Assign permissions to a role.
    Args:
        role_id: Role ID
        organization_id: Organization ID
        permission_ids: List of permission IDs
    Returns:
        bool: True if permissions were assigned successfully, False otherwise
    """
    if not permission_ids:
        return True

    supabase = await get_supabase_admin_client()

    await remove_all_permissions_from_role(role_id, organization_id)

    role_permissions = []
    for permission_id in permission_ids:
        role_permissions.append(
            {
                "organization_id": organization_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "created_at": NOW_CONSTANT,
            }
        )

    result = await supabase.table("role_permissions").insert(role_permissions).execute()

    return len(result.data) > 0 if result.data else False


async def remove_permissions_from_role(
    role_id: str, organization_id: str, permission_ids: list[str]
) -> bool:
    """Remove specific permissions from a role.
    Args:
        role_id: Role ID
        organization_id: Organization ID
        permission_ids: List of permission IDs
    Returns:
        bool: True if permissions were removed successfully, False otherwise
    """
    if not permission_ids:
        return True

    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("role_permissions")
        .delete()
        .eq("role_id", role_id)
        .eq("organization_id", organization_id)
        .in_("permission_id", permission_ids)
        .execute()
    )

    return len(result.data) > 0 if result.data else False


async def remove_all_permissions_from_role(role_id: str, organization_id: str) -> bool:
    """Remove all permissions from a role.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        bool: True if permissions were removed successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    await (
        supabase.table("role_permissions")
        .delete()
        .eq("role_id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return True


async def get_role_permissions(role_id: str, organization_id: str) -> list[dict[str, Any]]:
    """Get all permissions assigned to a role.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        list of permissions
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("role_permissions")
        .select("*")
        .eq("role_id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return result.data if result.data else []


async def check_permissions_exist(permission_ids: list[str], organization_id: str) -> bool:
    """Check if all permission IDs exist in organization.
    Args:
        permission_ids: List of permission IDs
        organization_id: Organization ID
    Returns:
        bool: True if all permission IDs exist, False otherwise
    """
    if not permission_ids:
        return True

    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("permissions")
        .select("id", count="exact")
        .in_("id", permission_ids)
        .eq("organization_id", organization_id)
        .execute()
    )

    return result.count == len(permission_ids) if result.count is not None else False


async def check_role_name_unique(
    name: str, organization_id: str, exclude_role_id: str | None = None
) -> bool:
    """Check if role name is unique in organization.
    Args:
        name: Role name
        organization_id: Organization ID
        exclude_role_id: Role ID to exclude
    Returns:
        bool: True if role name is unique, False otherwise
    """
    supabase = await get_supabase_admin_client()

    query = (
        supabase.table("roles").select("id").eq("name", name).eq("organization_id", organization_id)
    )

    if exclude_role_id:
        query = query.neq("id", exclude_role_id)

    result = await query.execute()

    return len(result.data) == 0 if result.data else True


async def check_role_usage(role_id: str, organization_id: str) -> int:
    """Check how many users are using this role.
    Args:
        role_id: Role ID
        organization_id: Organization ID
    Returns:
        int: Number of users using this role
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("organization_members")
        .select("id", count="exact")
        .eq("role_id", role_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    return result.count if result.count is not None else 0
