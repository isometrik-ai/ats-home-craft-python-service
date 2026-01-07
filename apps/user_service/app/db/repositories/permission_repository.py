"""Permissions Repository Module

This module provides asyncpg-based database operations for the `permissions` table.
All SQL queries for permission management should be centralized here.
"""

from datetime import datetime, timezone

import asyncpg

from apps.user_service.app.schemas.admin_access_management import (
    CreatePermissionRequest,
)
from libs.shared_utils.common_query import DEFAULT_PERMISSIONS
from libs.shared_utils.logger import get_logger

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

    async def delete_all_permissions_by_organization_id(self, organization_id: str) -> int:
        """Delete all permissions for an organization.

        Args:
            organization_id: The organization ID

        Returns:
            int: Number of permissions deleted
        """
        query = """
            DELETE FROM permissions
            WHERE organization_id = $1
        """
        result = await self.db_connection.execute(query, organization_id)
        return int(result.split()[-1]) if result else 0

    async def create_default_permissions(self, organization_id: str) -> list[str]:
        """Insert default permissions for a new organization and return their IDs."""
        if not DEFAULT_PERMISSIONS:
            return []

        columns = ["organization_id", "code", "name", "description", "category", "created_at"]

        now = datetime.now(timezone.utc)
        values = []
        placeholders = []

        for idx, (code, name, description, category) in enumerate(DEFAULT_PERMISSIONS):
            base_idx = idx * len(columns)
            values.extend([organization_id, code, name, description, category, now])
            placeholders.append(
                f"({', '.join(f'${base_idx + i + 1}' for i in range(len(columns)))})"
            )

        query = f"""
            INSERT INTO permissions ({", ".join(columns)})
            VALUES {", ".join(placeholders)}
            RETURNING id
        """

        rows = await self.db_connection.fetch(query, *values)
        return [str(row["id"]) for row in rows]
