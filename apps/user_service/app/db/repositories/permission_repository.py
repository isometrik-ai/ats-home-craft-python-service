"""Permissions Repository Module

This module provides asyncpg-based database operations for the `permissions` table.
All SQL queries for permission management should be centralized here.
"""

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.admin_access_management import (
    CreatePermissionRequest,
)

logger = get_logger("permissions_repository")


class PermissionsRepository:
    """Repository class for managing `permissions` table using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection):
        """Initialize with an active asyncpg connection.

        Args:
            db_connection: Active asyncpg database connection
        """
        self.db_connection = db_connection

    async def get_all_permissions(self, organization_id: str) -> list[dict]:
        """Get all permissions for an organization.

        Args:
            organization_id: The organization ID

        Returns:
            list[dict]: List of permission records
        """
        query = """
            SELECT
                id,
                name,
                code,
                category,
                description,
                created_at
            FROM permissions
            WHERE organization_id = $1
            ORDER BY category ASC, name ASC
        """
        rows = await self.db_connection.fetch(query, organization_id)
        return [dict(row) for row in rows]

    async def get_permission_by_id(self, permission_id: str, organization_id: str) -> dict | None:
        """Get permission by ID and organization ID.

        Args:
            permission_id: The permission ID
            organization_id: The organization ID

        Returns:
            dict | None: Permission record or None if not found
        """
        query = """
            SELECT
                id,
                name,
                code,
                category,
                description,
                created_at
            FROM permissions
            WHERE id = $1 AND organization_id = $2
        """
        row = await self.db_connection.fetchrow(query, permission_id, organization_id)
        return dict(row) if row else None

    async def create_permission(
        self, permission_data: CreatePermissionRequest, organization_id: str
    ) -> dict:
        """Create a new permission.

        Args:
            permission_data: Permission creation request data
            organization_id: The organization ID

        Returns:
            dict: Created permission record
        """
        query = """
            INSERT INTO permissions (name, code, category, description, organization_id, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            RETURNING id, name, code, category, description, created_at
        """
        row = await self.db_connection.fetchrow(
            query,
            permission_data.name,
            permission_data.code,
            permission_data.category,
            permission_data.description,
            organization_id,
        )
        return dict(row) if row else {}

    async def delete_permission(self, permission_id: str, organization_id: str) -> bool:
        """Delete a permission.

        Args:
            permission_id: The permission ID
            organization_id: The organization ID

        Returns:
            bool: True if deleted, False otherwise
        """
        query = """
            DELETE FROM permissions
            WHERE id = $1 AND organization_id = $2
            RETURNING id
        """
        row = await self.db_connection.fetchrow(query, permission_id, organization_id)
        return row is not None
