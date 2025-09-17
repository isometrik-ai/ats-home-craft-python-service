"""
Roles Management Utilities Module

This module provides specialized utility functions for role management operations.
These utilities handle role-specific validations, database operations, and business logic.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Role-Specific Operations Covered:
1. Permission existence validation
2. Role existence checking
3. Role name uniqueness validation
4. Role type validation
5. Role creation helpers
6. Role query building
"""

import time
from typing import List, Optional, Any

from fastapi import HTTPException, status

from libs.shared_utils.common_query import ROLE_SELECT_FIELDS


# Schema imports
from apps.user_service.app.schemas.admin_access_management import (
    RoleQueryParams,
)

from .common_utils import (
    validate_uuid_format,
    validate_uuid_list,
    ROLE_TYPES,
    extract_user_context,
    require_permission,
)

# ============================================================================
# ROLE VALIDATION
# ============================================================================


async def check_roles_manage_permission(
    current_user, db_conn, action_description="access role details"
):
    """
    Extracts user context and checks if the user has 'settings.roles.manage' permission.
    """
    user_context = extract_user_context(current_user)
    await require_permission(
        permission_code="settings.roles.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description=action_description,
    )
    return user_context


async def check_roles_manage_multiple_permission(
    current_user, db_conn, action_description="access role details"
):
    """
    Extracts user context and checks if the user has 'settings.roles.manage' permission.
    """
    user_context = extract_user_context(current_user)
    await require_permission(
        permission_code=["settings.roles.manage", "settings.users.manage"],
        user_context=user_context,
        db_conn=db_conn,
        action_description=action_description,
    )
    return user_context


def validate_role_type(role_type: str) -> None:
    """
    Validate role type against allowed values.

    Args:
        role_type (str): Role type to validate

    Raises:
        HTTPException: 400 for invalid role type

    Usage:
        validate_role_type(role_data.role_type)
    """
    if role_type not in ROLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role type must be 'system' or 'custom'",
        )


# ============================================================================
# PERMISSION VALIDATION
# ============================================================================


async def validate_permissions_exist(
    permission_ids: List[str], organization_id: str, db_conn, with_timing: bool = True
) -> None:
    """
    Validate that all permission IDs exist in the organization.

    Args:
        permission_ids (List[str]): List of permission IDs to validate
        organization_id (str): Organization ID to check within
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Raises:
        HTTPException: 400 for invalid permission IDs

    Usage:
        await validate_permissions_exist(role_data.permission_ids, organization_id, db_conn)
    """
    if not permission_ids:
        return

    # Validate UUID format first
    validate_uuid_list(permission_ids, "permission ID")

    if with_timing:
        start_time = time.time()

    # Create placeholder string for IN clause
    permission_placeholders = ", ".join([f"${i+2}" for i in range(len(permission_ids))])

    permissions_check_query = f"""
        SELECT COUNT(*) as valid_count
        FROM public.permissions p
        WHERE p.organization_id = $1
            AND p.id IN ({permission_placeholders});
    """

    # Build parameters: organization_id, then all permission_ids
    check_params = [organization_id] + permission_ids

    permission_count_result = await db_conn.fetchrow(
        permissions_check_query, *check_params
    )
    valid_permission_count = (
        permission_count_result["valid_count"] if permission_count_result else 0
    )

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Permission validation took {elapsed:.2f}ms")

    if valid_permission_count != len(permission_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid permission IDs found. Expected "
                f"{len(permission_ids)}, found "
                f"{valid_permission_count} valid permissions."
            ),
        )


# ============================================================================
# ROLE DATABASE OPERATIONS
# ============================================================================


async def check_role_exists(
    role_id: str, organization_id: str, db_conn, with_timing: bool = True
) -> dict:
    """
    Check if role exists in organization and return role data.

    Args:
        role_id (str): Role ID to check
        organization_id (str): Organization ID to check within
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Returns:
        dict: Role data if found

    Raises:
        HTTPException: 404 if role not found

    Usage:
        role_data = await check_role_exists(role_id, organization_id, db_conn)
    """
    validate_uuid_format(role_id, "role ID")

    if with_timing:
        start_time = time.time()

    role_check_query = """
        SELECT id, name, is_default, description FROM public.roles
        WHERE id = $1 AND organization_id = $2;
    """

    existing_role = await db_conn.fetchrow(role_check_query, role_id, organization_id)

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Role existence check took {elapsed:.2f}ms")

    if not existing_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found or access denied",
        )

    return existing_role


async def check_role_name_unique(
    name: str,
    organization_id: str,
    db_conn,
    exclude_role_id: Optional[str] = None,
    with_timing: bool = True,
) -> None:
    """
    Check if role name is unique in organization.

    Args:
        name (str): Role name to check
        organization_id (str): Organization ID to check within
        db_conn: AsyncPG database connection
        exclude_role_id (Optional[str]): Role ID to exclude from check (for updates)
        with_timing (bool): Whether to log timing information

    Raises:
        HTTPException: 409 for name conflicts

    Usage:
        await check_role_name_unique(role_data.name, organization_id, db_conn)
        await check_role_name_unique(role_data.name, org_id, db_conn, exclude_role_id=role_id)
    """
    if with_timing:
        start_time = time.time()

    if exclude_role_id:
        validate_uuid_format(exclude_role_id, "role ID")
        name_conflict_query = """
            SELECT id FROM public.roles
            WHERE name = $1 AND organization_id = $2 AND id != $3;
        """
        name_conflict = await db_conn.fetchrow(
            name_conflict_query, name, organization_id, exclude_role_id
        )
    else:
        name_check_query = """
            SELECT id FROM public.roles
            WHERE name = $1 AND organization_id = $2;
        """
        name_conflict = await db_conn.fetchrow(name_check_query, name, organization_id)

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Name uniqueness check took {elapsed:.2f}ms")

    if name_conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role with name '{name}' already exists in organization",
        )


async def check_role_usage(
    role_id: str, organization_id: str, db_conn, with_timing: bool = True
) -> int:
    """
    Check how many organization members are using this role.

    Args:
        role_id (str): Role ID to check
        organization_id (str): Organization ID to check within
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Returns:
        int: Number of members using this role

    Usage:
        member_count = await check_role_usage(role_id, organization_id, db_conn)
    """
    validate_uuid_format(role_id, "role ID")

    if with_timing:
        start_time = time.time()

    usage_check_query = """
        SELECT COUNT(*) as member_count
        FROM public.organization_members om
        WHERE om.role_id = $1 AND om.organization_id = $2;
    """

    usage_result = await db_conn.fetchrow(usage_check_query, role_id, organization_id)
    member_count = usage_result["member_count"] if usage_result else 0

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Role usage check took {elapsed:.2f}ms")

    return member_count


# ============================================================================
# ROLE QUERY BUILDERS
# ============================================================================


def build_roles_filter_query(
    organization_id: str,
    search: Optional[str] = None,
    # role_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    params: Optional[RoleQueryParams] = None,
) -> tuple[str, List[Any]]:
    """
    Build a dynamic roles query with filters.

    Args:
        organization_id (str): Organization ID to filter by
        search (Optional[str]): Search term for role names
        role_type (Optional[str]): Role type filter ("system" or "custom")
        limit (int): Query limit
        offset (int): Query offset

    Returns:
        tuple: (query_string, parameters_list)

    Usage:
        query, params = build_roles_filter_query(
            organization_id, search="admin", role_type="system", limit=10
        )
    """
    query_params: List[Any] = [organization_id]
    param_count = 1

    # Base WHERE conditions
    # where_conditions = ["r.organization_id = $1"]
    where_conditions = ["r.organization_id = $1", "r.name != 'Super Admin'"]
    # Add search filter
    if search:
        param_count += 1
        where_conditions.append(f"r.name ILIKE ${param_count}")
        query_params.append(f"%{search}%")

    # Add role type filter
    if params.role_type:
        validate_role_type(params.role_type)
        param_count += 1
        is_default_value = params.role_type == "system"
        where_conditions.append(f"r.is_default = ${param_count}")
        query_params.append(is_default_value)

    # Build WHERE clause
    where_clause = " AND ".join(where_conditions)

    # Add pagination parameters
    param_count += 1
    limit_param = f"${param_count}"
    query_params.append(limit)

    param_count += 1
    offset_param = f"${param_count}"
    query_params.append(offset)

    # Determine ORDER BY clause
    if params and params.sort_type:
        order_by_clause = "ORDER BY r.name ASC"
    else:
        order_by_clause = "ORDER BY r.updated_at DESC"

    # Build complete query
    roles_query = f"""
            WITH role_user_counts AS (
                SELECT
                    r.id AS role_id,
                    COUNT(DISTINCT om.user_id) AS user_count
                FROM public.roles r
                LEFT JOIN public.organization_members om
                    ON r.id = om.role_id
                    AND om.organization_id = r.organization_id
                    AND om.status = 'active'
                WHERE {where_clause}
                GROUP BY r.id
            ),
            role_permission_counts AS (
                SELECT
                    r.id AS role_id,
                    COUNT(DISTINCT rp.permission_id) AS permission_count,
                    COALESCE(
                        JSON_OBJECT_AGG(
                            COALESCE(p.category, 'uncategorized'),
                            category_count
                        ) FILTER (WHERE category_count > 0),
                        '{{}}'::json
                    ) AS permission_categories
                FROM public.roles r
                LEFT JOIN public.role_permissions rp
                    ON r.id = rp.role_id
                    AND rp.organization_id = r.organization_id
                LEFT JOIN public.permissions p
                    ON rp.permission_id = p.id
                LEFT JOIN (
                    SELECT
                        r2.id AS role_id,
                        COALESCE(p2.category, 'uncategorized') AS category,
                        COUNT(*) AS category_count
                    FROM public.roles r2
                    LEFT JOIN public.role_permissions rp2
                        ON r2.id = rp2.role_id
                        AND rp2.organization_id = r2.organization_id
                    LEFT JOIN public.permissions p2
                        ON rp2.permission_id = p2.id
                    WHERE r2.organization_id = $1
                    GROUP BY r2.id, COALESCE(p2.category, 'uncategorized')
                ) cat_counts
                ON r.id = cat_counts.role_id
                AND COALESCE(p.category, 'uncategorized') = cat_counts.category
                WHERE {where_clause}
                GROUP BY r.id
            )
            SELECT
                {ROLE_SELECT_FIELDS},
                COALESCE(ruc.user_count, 0) AS user_count,
                COALESCE(rpc.permission_count, 0) AS permission_count,
                COALESCE(rpc.permission_categories, '{{}}'::json) AS permission_categories
            FROM public.roles r
            LEFT JOIN role_user_counts ruc ON r.id = ruc.role_id
            LEFT JOIN role_permission_counts rpc ON r.id = rpc.role_id
            WHERE {where_clause}
            {order_by_clause}
            LIMIT {limit_param} OFFSET {offset_param};
        """

    return roles_query, query_params


def build_roles_count_query(
    organization_id: str,
    search: Optional[str] = None,
    # role_type: Optional[str] = None,
    params: Optional[RoleQueryParams] = None,
) -> tuple[str, List[Any]]:
    """
    Build a count query for roles with the same filters.

    Args:
        organization_id (str): Organization ID to filter by
        search (Optional[str]): Search term for role names
        role_type (Optional[str]): Role type filter ("system" or "custom")

    Returns:
        tuple: (count_query_string, parameters_list)

    Usage:
        count_query, count_params = build_roles_count_query(
            organization_id, search="admin", role_type="system"
        )
    """
    query_params: List[Any] = [organization_id]
    param_count = 1

    # Base WHERE conditions
    # where_conditions = ["r.organization_id = $1"]
    where_conditions = ["r.organization_id = $1", "r.name != 'Super Admin'"]
    # Add search filter
    if search:
        param_count += 1
        where_conditions.append(f"r.name ILIKE ${param_count}")
        query_params.append(f"%{search}%")

    # Add role type filter
    if params.role_type:
        validate_role_type(params.role_type)
        param_count += 1
        is_default_value = params.role_type == "system"
        where_conditions.append(f"r.is_default = ${param_count}")
        query_params.append(is_default_value)

    # Build WHERE clause
    where_clause = " AND ".join(where_conditions)

    # Build count query
    count_query = f"""
        SELECT COUNT(*) as total_count
        FROM public.roles r
        WHERE {where_clause};
    """

    return count_query, query_params


# ============================================================================
# ROLE PERMISSION MANAGEMENT
# ============================================================================


async def assign_permissions_to_role(
    role_id: str,
    organization_id: str,
    permission_ids: List[str],
    db_conn,
    with_timing: bool = True,
) -> None:
    """
    Assign permissions to a role using bulk insert.

    Args:
        role_id (str): Role ID to assign permissions to
        organization_id (str): Organization ID
        permission_ids (List[str]): List of permission IDs to assign
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Usage:
        await assign_permissions_to_role(role_id, org_id, permission_ids, db_conn)
    """
    if not permission_ids:
        return

    validate_uuid_format(role_id, "role ID")
    validate_uuid_list(permission_ids, "permission ID")

    if with_timing:
        start_time = time.time()

    # Prepare bulk insert for role_permissions
    permission_assignments = [
        (organization_id, str(role_id), permission_id)
        for permission_id in permission_ids
    ]

    assign_permissions_query = """
        INSERT INTO public.role_permissions (organization_id, role_id, permission_id, created_at)
        VALUES ($1, $2, $3, NOW());
    """

    # Execute bulk insert
    await db_conn.executemany(assign_permissions_query, permission_assignments)

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Permission assignment took {elapsed:.2f}ms")


async def remove_all_permissions_from_role(
    role_id: str, organization_id: str, db_conn, with_timing: bool = True
) -> None:
    """
    Remove all permissions from a role.

    Args:
        role_id (str): Role ID to remove permissions from
        organization_id (str): Organization ID
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Usage:
        await remove_all_permissions_from_role(role_id, org_id, db_conn)
    """
    validate_uuid_format(role_id, "role ID")

    if with_timing:
        start_time = time.time()

    delete_permissions_query = """
        DELETE FROM public.role_permissions
        WHERE role_id = $1 AND organization_id = $2;
    """

    await db_conn.execute(delete_permissions_query, role_id, organization_id)

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Permission removal took {elapsed:.2f}ms")


# ============================================================================
# ROLE RESPONSE HELPERS
# ============================================================================


def build_role_filter_message(
    search: Optional[str] = None,
    role_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> str:
    """
    Build a filter description message for role API responses.

    Args:
        search (Optional[str]): Search term
        role_type (Optional[str]): Role type filter
        skip (int): Skip/offset value
        limit (int): Limit value

    Returns:
        str: Formatted filter message

    Usage:
        filter_msg = build_role_filter_message(search="admin", role_type="system")
    """
    filter_info = []

    if search:
        filter_info.append(f"search='{search}'")
    if role_type:
        filter_info.append(f"type={role_type}")
    if skip > 0:
        filter_info.append(f"skip={skip}")
    filter_info.append(f"limit={limit}")

    filter_text = f" with filters: {', '.join(filter_info)}" if filter_info else ""
    return f"Roles retrieved successfully{filter_text}"
