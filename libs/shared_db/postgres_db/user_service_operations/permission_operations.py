"""Permission Database Operations Module
This module contains all permission-related database operations.
All SQL queries for permission management should be centralized here.
"""

from typing import Any

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import (
    CreatePermissionRequest,
)
from libs import NOW_CONSTANT
from libs.shared_db.supabase_db.db import get_supabase_admin_client

logger = get_logger("permission_operations")


async def create_new_permission(
    permission_data: CreatePermissionRequest, organization_id: str
) -> dict[str, Any]:
    """Create a new permission.
    Args:
        permission_data: Permission data
        organization_id: Organization ID
    Returns:
        dict containing the new permission
    """
    supabase = await get_supabase_admin_client()

    permission_record = {
        "name": permission_data.name,
        "code": permission_data.code,
        "category": permission_data.category,
        "description": permission_data.description,
        "organization_id": organization_id,
        "created_at": NOW_CONSTANT,
    }

    result = await supabase.table("permissions").insert(permission_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def get_permission_details_by_id(
    permission_id: str, organization_id: str
) -> dict[str, Any] | None:
    """Get permission by ID and organization ID.
    Args:
        permission_id: Permission ID
        organization_id: Organization ID
    Returns:
        dict containing the permission or None if not found
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("permissions")
        .select("id, name, code, category, description, created_at")
        .eq("id", permission_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def get_all_permissions(organization_id: str) -> list[dict[str, Any]]:
    """Get all permissions for organization.
    Args:
        organization_id: Organization ID
    Returns:
        list of permissions
    """
    supabase = await get_supabase_admin_client()

    result = (
        await supabase.table("permissions")
        .select("id, name, code, category, description, created_at")
        .eq("organization_id", organization_id)
        .order("category", desc=False)
        .order("name", desc=False)
        .execute()
    )

    return result.data if result.data else []


async def delete_permission(permission_id: str, organization_id: str) -> bool:
    """Delete a permission.
    Args:
        permission_id: Permission ID
        organization_id: Organization ID
    Returns:
        bool: True if permission was deleted successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()
    result = (
        await supabase.table("permissions")
        .delete()
        .eq("id", permission_id)
        .eq("organization_id", organization_id)
        .execute()
    )
    return result.data if result.data else []
