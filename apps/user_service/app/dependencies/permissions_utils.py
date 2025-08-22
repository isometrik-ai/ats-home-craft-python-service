# pylint: disable=import-error,no-name-in-module
"""
Permmissions Management Utilities Module
"""

from fastapi import HTTPException, status

from app.dependencies.common_utils import validate_uuid_format
from app.schemas.admin_access_management import CreatePermissionRequest

from libs.shared_utils.common_query import PERMISSION_SELECT_FIELDS


async def create_permission_in_db(
    permission_data: CreatePermissionRequest, organization_id: str, db_conn
):
    """
    Create a new permission
    """
    validate_uuid_format(organization_id, "organization ID")

    insert_query = """
        INSERT INTO public.permissions (name, organization_id, code, category, description, created_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        RETURNING id, name, code, category, description, created_at;
    """

    permission = await db_conn.fetchrow(
        insert_query,
        permission_data.name,
        organization_id,
        permission_data.code,
        permission_data.category,
        permission_data.description,
    )

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create permission",
        )

    return permission


async def get_all_permission_from_db(organization_id: str, db_conn):
    """
    Get all permissions for an organization
    """

    validate_uuid_format(organization_id, "organization ID")

    permissions_query = f"""
            SELECT 
                {PERMISSION_SELECT_FIELDS}
            FROM public.permissions p
            WHERE p.organization_id = $1
            ORDER BY p.category NULLS LAST, p.name ASC;
        """

    # Execute async query (non-blocking)
    permissions_data = await db_conn.fetch(permissions_query, organization_id)

    if not permissions_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No permissions found",
        )

    return permissions_data


async def get_permission_by_id_from_db(
    permission_id: str, organization_id: str, db_conn
):
    """
    Get permission by ID
    """
    validate_uuid_format(permission_id, "permission ID")
    validate_uuid_format(organization_id, "organization ID")

    permission_query = """
        SELECT id, name, code, category, description, created_at
        FROM public.permissions
        WHERE id = $1 AND organization_id = $2;
    """

    permission_data = await db_conn.fetchrow(
        permission_query, permission_id, organization_id
    )

    if not permission_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )

    return permission_data
