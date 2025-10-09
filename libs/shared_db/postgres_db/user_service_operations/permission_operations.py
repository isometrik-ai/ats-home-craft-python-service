"""
Permission Database Operations Module

This module contains all permission-related database operations.
All SQL queries for permission management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Permission CRUD operations
- Permission validation operations
- Permission search and filtering
- Permission category operations
- Permission assignment tracking
"""

from typing import List, Dict, Any, Optional
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import CreatePermissionRequest
from libs import NOW_CONSTANT
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from .exception_handling import handle_database_errors, create_error_messages

# Initialize logger
logger = get_logger("permission_operations")


# ============================================================================
# PERMISSION CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_new_permission",
    custom_messages=create_error_messages("create_new_permission", "creating"))
async def create_new_permission(
    permission_data: CreatePermissionRequest,
    organization_id: str
    ) -> Dict[str, Any]:
    """Create a new permission."""
    supabase = await get_supabase_admin_client()

    permission_record = {
        "name": permission_data.name,
        "code": permission_data.code,
        "category": permission_data.category,
        "description": permission_data.description,
        "organization_id": organization_id,
        "created_at": NOW_CONSTANT
    }

    result = await supabase.table("permissions").insert(permission_record).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "get_permission_details_by_id",
    custom_messages=create_error_messages("get_permission_details_by_id", "getting"))
async def get_permission_details_by_id(
    permission_id: str,
    organization_id: str) -> Optional[Dict[str, Any]]:
    """Get permission by ID and organization ID."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("permissions").select(
        "id, name, code, category, description, created_at"
    ).eq("id", permission_id).eq("organization_id", organization_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


# # ============================================================================
# # PERMISSION LISTING AND SEARCH
# # ============================================================================

@handle_database_errors(
    "get_all_permissions",
    custom_messages=create_error_messages("get_all_permissions", "getting"))
async def get_all_permissions(organization_id: str) -> List[Dict[str, Any]]:
    """Get all permissions for organization."""
    supabase = await get_supabase_admin_client()

    result = await supabase.table("permissions").select(
        "id, name, code, category, description, created_at"
    ).eq("organization_id", organization_id).order("category", desc=False).order(
        "name", desc=False
    ).execute()

    return result.data if result.data else []
