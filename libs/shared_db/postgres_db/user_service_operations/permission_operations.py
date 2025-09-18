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
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import CreatePermissionRequest
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
        "created_at": "now()"
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


# async def get_permission_by_code(code: str, organization_id: str) -> Optional[Dict[str, Any]]:
#     """Get permission by code and organization ID."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select(
#             "id, name, code, category, description, created_at, updated_at"
#         ).eq("code", code).eq("organization_id", organization_id).execute()

#         if result.data and len(result.data) > 0:
#             return result.data[0]
#         return None

#     except APIError as e:
#         logger.error("Supabase API error getting permission by code: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permission by code: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permission by code: %s", e, exc_info=True)
#         raise


# async def update_permission(permission_id: str, organization_id: str,
#                           update_data: Dict[str, Any]) -> Dict[str, Any]:
#     """Update permission information."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Prepare update data
#         update_payload = {}

#         for field, value in update_data.items():
#             if value is not None:
#                 update_payload[field] = value

#         if not update_payload:
#             # No fields to update
#             return {}

#         # Add updated_at
#         update_payload["updated_at"] = "now()"

#         result = await supabase.table("permissions").update(update_payload).eq(
#             "id", permission_id
#         ).eq("organization_id", organization_id).execute()

#         if result.data and len(result.data) > 0:
#             return result.data[0]
#         return {}

#     except APIError as e:
#         logger.error("Supabase API error updating permission: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error updating permission: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error updating permission: %s", e, exc_info=True)
#         raise


# async def delete_permission(permission_id: str, organization_id: str) -> bool:
#     """Delete permission from organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").delete().eq(
#             "id", permission_id
#         ).eq("organization_id", organization_id).execute()

#         return len(result.data) > 0 if result.data else False

#     except APIError as e:
#         logger.error("Supabase API error deleting permission: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error deleting permission: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error deleting permission: %s", e, exc_info=True)
#         raise


# async def check_permission_exists(permission_id: str, organization_id: str) -> bool:
#     """Check if permission exists in organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select("id").eq(
#             "id", permission_id
#         ).eq("organization_id", organization_id).execute()

#         return len(result.data) > 0 if result.data else False

#     except APIError as e:
#         logger.error("Supabase API error checking permission exists: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error checking permission exists: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error checking permission exists: %s", e, exc_info=True)
#         raise


# # ============================================================================
# # PERMISSION LISTING AND SEARCH
# # ============================================================================

# async def get_permissions_list(organization_id: str, search: Optional[str] = None,
#                              category: Optional[str] = None, limit: int = 20,
#                              offset: int = 0) -> List[Dict[str, Any]]:
#     """Get paginated list of permissions with optional search and filtering."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Build the query with filters
#         query = supabase.table("permissions").select(
#             "id, name, code, category, description, created_at, updated_at"
#         ).eq("organization_id", organization_id)

#         # Apply search filter
#         if search:
#             query = query.or_(
#                 f"name.ilike.%{search}%,"
#                 f"code.ilike.%{search}%,"
#                 f"description.ilike.%{search}%"
#             )

#         # Apply category filter
#         if category:
#             query = query.eq("category", category)

#         # Apply pagination and ordering
#         result = await query.order("created_at", desc=True).range(
#             offset, offset + limit - 1
#         ).execute()

#         return result.data if result.data else []

#     except APIError as e:
#         logger.error("Supabase API error getting permissions list: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permissions list: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permissions list: %s", e, exc_info=True)
#         raise


# async def get_permissions_count(organization_id: str, search: Optional[str] = None,
#                               category: Optional[str] = None) -> int:
#     """Get total count of permissions matching search criteria."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Build the count query with filters
#         query = supabase.table("permissions").select("id", count="exact").eq(
#             "organization_id", organization_id
#         )

#         # Apply search filter
#         if search:
#             query = query.or_(
#                 f"name.ilike.%{search}%,"
#                 f"code.ilike.%{search}%,"
#                 f"description.ilike.%{search}%"
#             )

#         # Apply category filter
#         if category:
#             query = query.eq("category", category)

#         result = await query.execute()

#         return result.count if result.count is not None else 0

#     except APIError as e:
#         logger.error("Supabase API error getting permissions count: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permissions count: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permissions count: %s", e, exc_info=True)
#         raise


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


# # ============================================================================
# # PERMISSION VALIDATION OPERATIONS
# # ============================================================================

# async def validate_permissions_exist(permission_ids: List[str], organization_id: str) -> bool:
#     """Validate if all permission IDs exist in organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select("id", count="exact").in_(
#             "id", permission_ids
#         ).eq("organization_id", organization_id).execute()

#         return result.count == len(permission_ids) if result.count is not None else False

#     except APIError as e:
#         logger.error("Supabase API error validating permissions exist: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error validating permissions exist: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error validating permissions exist: %s", e, exc_info=True)
#         raise


# async def check_permission_code_unique(code: str, organization_id: str,
#                                      exclude_permission_id: Optional[str] = None) -> bool:
#     """Check if permission code is unique in organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         query = supabase.table("permissions").select("id").eq("code", code).eq(
#             "organization_id", organization_id
#         )

#         if exclude_permission_id:
#             query = query.neq("id", exclude_permission_id)

#         result = await query.execute()

#         return len(result.data) == 0 if result.data else True

#     except APIError as e:
#         logger.error("Supabase API error checking permission code unique: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error checking permission code unique: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error(
#               "Data validation error checking permission code unique: %s",
#               e, exc_info=True)
#         raise


# async def check_permission_name_unique(name: str, organization_id: str,
#                                      exclude_permission_id: Optional[str] = None) -> bool:
#     """Check if permission name is unique in organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         query = supabase.table("permissions").select("id").eq("name", name).eq(
#             "organization_id", organization_id
#         )

#         if exclude_permission_id:
#             query = query.neq("id", exclude_permission_id)

#         result = await query.execute()

#         return len(result.data) == 0 if result.data else True

#     except APIError as e:
#         logger.error("Supabase API error checking permission name unique: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error checking permission name unique: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error(
#               "Data validation error checking permission name unique: %s",
#               e, exc_info=True)
#         raise


# # ============================================================================
# # PERMISSION CATEGORY OPERATIONS
# # ============================================================================

# async def get_permission_categories(organization_id: str) -> List[str]:
#     """Get all unique permission categories in organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select("category").eq(
#             "organization_id", organization_id
#         ).not_.is_("category", "null").execute()

#         if result.data:
#             categories = list(set(item["category"] for item in result.data if item["category"]))
#             return sorted(categories)
#         return []

#     except APIError as e:
#         logger.error("Supabase API error getting permission categories: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permission categories: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permission categories: %s", e, exc_info=True)
#         raise


# async def get_permissions_by_category(category: str,organization_id: str) -> List[Dict[str, Any]]:
#     """Get all permissions in a specific category."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select(
#             "id, name, code, category, description, created_at, updated_at"
#         ).eq("category", category).eq("organization_id", organization_id).order(
#             "name", desc=False
#         ).execute()

#         return result.data if result.data else []

#     except APIError as e:
#         logger.error("Supabase API error getting permissions by category: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permissions by category: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permissions by category: %s",e,exc_info=True)
#         raise


# async def create_permission_category(category: str, organization_id: str) -> bool:
#     """Create a new permission category."""
#     # This is typically handled by creating a permission with the category
#     # For now, return True as categories are created implicitly
#     return True


# # ============================================================================
# # PERMISSION QUERY BUILDING
# # ============================================================================

# # Note: Query building functions have been removed as Supabase SDK
# # provides built-in query methods that are more efficient and type-safe.
# # The filtering logic is now handled directly in the respective functions.


# # ============================================================================
# # PERMISSION ASSIGNMENT OPERATIONS
# # ============================================================================

# async def get_permission_assignments(
#   permission_id: str,
#   organization_id: str) -> List[Dict[str, Any]]:
#     """Get all roles that have this permission assigned."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("role_permissions").select(
#             "role_id, roles(id, name, description)"
#         ).eq("permission_id", permission_id).eq("organization_id", organization_id).execute()

#         assignments = []
#         if result.data:
#             for item in result.data:
#                 if item.get("roles"):
#                     assignments.append(item["roles"])

#         return assignments

#     except APIError as e:
#         logger.error("Supabase API error getting permission assignments: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permission assignments: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permission assignments: %s", e, exc_info=True)
#         raise


# async def get_role_permission_assignments(
#   role_id: str,
#   organization_id: str) -> List[Dict[str, Any]]:
#     """Get all permissions assigned to a role."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("role_permissions").select(
#             "permission_id, permissions(id, name, code, category, description)"
#         ).eq("role_id", role_id).eq("organization_id", organization_id).execute()

#         assignments = []
#         if result.data:
#             for item in result.data:
#                 if item.get("permissions"):
#                     assignments.append(item["permissions"])

#         return assignments

#     except APIError as e:
#         logger.error("Supabase API error getting role permission assignments: %s",e,exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting role permission assignments: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
        # logger.error(
        #     "Data validation error getting role permission assignments: %s",
        #     e, exc_info=True)
#         raise


# async def check_permission_assigned_to_role(permission_id: str, role_id: str,
#                                           organization_id: str) -> bool:
#     """Check if permission is assigned to role."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("role_permissions").select("id").eq(
#             "permission_id", permission_id
#         ).eq("role_id", role_id).eq("organization_id", organization_id).execute()

#         return len(result.data) > 0 if result.data else False

#     except APIError as e:
#         logger.error("Supabase API error checking permission assigned to role:%s",e,exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error checking permission assigned to role: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error(
#               "Data validation error checking permission assigned to role: %s",
#               e, exc_info=True)
#         raise


# # ============================================================================
# # PERMISSION STATISTICS OPERATIONS
# # ============================================================================

# async def get_permission_statistics(organization_id: str) -> Dict[str, Any]:
#     """Get permission statistics for organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Get total permissions
#         total_result = await supabase.table("permissions").select(
#             "id", count="exact"
#         ).eq("organization_id", organization_id).execute()

#         total_permissions = total_result.count if total_result.count is not None else 0

#         # Get categories count
#         categories_result = await supabase.table("permissions").select(
#             "category"
#         ).eq("organization_id", organization_id).not_.is_("category", "null").execute()

#         categories = set()
#         if categories_result.data:
#             categories = set(item["category"]
#                              for item in categories_result.data if item["category"])

#         return {
#             "total_permissions": total_permissions,
#             "category_count": len(categories),
#             "categories": list(categories)
#         }

#     except APIError as e:
#         logger.error("Supabase API error getting permission statistics: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permission statistics: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permission statistics: %s", e, exc_info=True)
#         raise


# async def get_permission_usage_stats(permission_id: str, organization_id: str) -> Dict[str, Any]:
#     """Get usage statistics for a specific permission."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Get role assignments count
#         assignments_result = await supabase.table("role_permissions").select(
#             "id", count="exact"
#         ).eq("permission_id", permission_id).eq("organization_id", organization_id).execute()

#         role_assignments = assignments_result.count if assignments_result.count is not None else 0

#         return {
#             "role_assignments": role_assignments,
#             "is_used": role_assignments > 0
#         }

#     except APIError as e:
#         logger.error("Supabase API error getting permission usage stats: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error getting permission usage stats: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error getting permission usage stats: %s", e, exc_info=True)
#         raise


# # ============================================================================
# # PERMISSION BULK OPERATIONS
# # ============================================================================

# async def bulk_create_permissions(permissions_data: List[Dict[str, Any]],
#                                 organization_id: str) -> List[Dict[str, Any]]:
#     """Bulk create multiple permissions."""
#     supabase = await get_supabase_admin_client()
#     try:
#         # Prepare permission records
#         permission_records = []
#         for perm_data in permissions_data:
#             permission_records.append({
#                 "name": perm_data["name"],
#                 "code": perm_data["code"],
#                 "category": perm_data["category"],
#                 "description": perm_data["description"],
#                 "organization_id": organization_id,
#                 "created_at": "now()",
#                 "updated_at": "now()"
#             })

#         result = await supabase.table("permissions").insert(permission_records).execute()

#         return result.data if result.data else []

#     except APIError as e:
#         logger.error("Supabase API error bulk creating permissions: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error bulk creating permissions: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error bulk creating permissions: %s", e, exc_info=True)
#         raise


# async def bulk_update_permissions(updates: List[Dict[str, Any]],
#                                 organization_id: str) -> List[Dict[str, Any]]:
#     """Bulk update multiple permissions."""
#     supabase = await get_supabase_admin_client()
#     try:
#         results = []
#         for update_data in updates:
#             permission_id = update_data.pop("id")
#             result = await update_permission(permission_id, organization_id, update_data)
#             if result:
#                 results.append(result)
#         return results

#     except APIError as e:
#         logger.error("Supabase API error bulk updating permissions: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error bulk updating permissions: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error bulk updating permissions: %s", e, exc_info=True)
#         raise


# async def bulk_delete_permissions(permission_ids: List[str], organization_id: str) -> int:
#     """Bulk delete multiple permissions."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").delete().in_(
#             "id", permission_ids
#         ).eq("organization_id", organization_id).execute()

#         return len(result.data) if result.data else 0

#     except APIError as e:
#         logger.error("Supabase API error bulk deleting permissions: %s", e, exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error bulk deleting permissions: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error("Data validation error bulk deleting permissions: %s", e, exc_info=True)
#         raise


# # ============================================================================
# # DEFAULT PERMISSIONS OPERATIONS
# # ============================================================================

# async def create_default_permissions(organization_id: str) -> List[Dict[str, Any]]:
#     """Create default permissions for new organization."""
#     # This would typically create a set of default permissions
#     # For now, return empty list as this is handled by the permission service
#     return []


# async def get_default_permissions() -> List[Dict[str, Any]]:
#     """Get list of default permissions to create."""
#     # This would typically return a predefined list of default permissions
#     # For now, return empty list as this is handled by the permission service
#     return []


# async def check_default_permissions_exist(organization_id: str) -> bool:
#     """Check if default permissions already exist for organization."""
#     supabase = await get_supabase_admin_client()
#     try:
#         result = await supabase.table("permissions").select("id", count="exact").eq(
#             "organization_id", organization_id
#         ).execute()

#         return result.count > 0 if result.count is not None else False

#     except APIError as e:
#         logger.error("Supabase API error checking default permissions exist: %s",e,exc_info=True)
#         raise
#     except (HTTPError, RequestError, TimeoutException) as e:
#         logger.error("Network error checking default permissions exist: %s", e, exc_info=True)
#         raise
#     except (KeyError, TypeError, ValueError) as e:
#         logger.error(
#               "Data validation error checking default permissions exist: %s",
#               e, exc_info=True)
#         raise
