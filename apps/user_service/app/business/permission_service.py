"""Permissions Service Module

This module provides business logic for permission management.
User context is provided during initialization.
"""

import asyncpg

from apps.user_service.app.db.repositories import PermissionsRepository
from apps.user_service.app.schemas.admin_access_management import (
    CreatePermissionRequest,
    PermissionItem,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    format_permissions_data,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class PermissionsService:
    """Service for permission business logic.
    User context is provided during initialization.
    """

    def __init__(self, user_context: UserContext, db_connection: asyncpg.Connection):
        """Initialize PermissionsService with user context.

        Args:
            user_context: Authenticated user context
            db_connection: Database connection for postgresql
        """
        self.user_context = user_context
        self.permissions_repository = PermissionsRepository(db_connection=db_connection)

    async def get_all_permissions(self) -> list[PermissionItem]:
        """Get all permissions for the current organization.

        Returns:
            list[PermissionItem]: List of permissions with id, name,
                code, category, description, and created_at
        """

        permissions_data = await self.permissions_repository.get_all_permissions(
            self.user_context.organization_id
        )

        permissions = format_permissions_data(permissions_data)

        return permissions

    async def get_permission_by_id(self, permission_id: str) -> PermissionItem:
        """Get permission by ID.

        Args:
            permission_id: The ID of the permission to retrieve

        Returns:
            PermissionItem: Permission details

        Raises:
            NotFoundException: 404 if permission not found
        """
        permission = await self.permissions_repository.get_permission_by_id(
            permission_id, self.user_context.organization_id
        )

        if not permission:
            raise NotFoundException(
                message_key="permissions.errors.permission_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        permission_item = PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=format_iso_datetime(permission["created_at"]) or "",
        )

        return permission_item

    async def create_permission(self, permission_data: CreatePermissionRequest) -> PermissionItem:
        """Create a new permission.

        Args:
            permission_data: Permission creation request data

        Returns:
            PermissionItem: Created permission details

        Raises:
            ValidationException: 400 if creation fails
        """
        permission = await self.permissions_repository.create_permission(
            permission_data=permission_data, organization_id=self.user_context.organization_id
        )

        if not permission:
            raise ValidationException(
                message_key="permissions.errors.creation_failed",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

        created_permission = PermissionItem(
            id=str(permission["id"]),
            name=permission["name"],
            code=permission["code"],
            category=permission["category"],
            description=permission["description"],
            created_at=format_iso_datetime(permission["created_at"]) or "",
        )

        return created_permission

    async def delete_permission(self, permission_id: str) -> None:
        """Delete a permission.

        Args:
            permission_id: The ID of the permission to delete

        Raises:
            NotFoundException: 404 if permission not found
        """
        # Delete permission
        permission = await self.permissions_repository.delete_permission(
            permission_id, self.user_context.organization_id
        )

        # Check if permission exists
        if not permission:
            raise NotFoundException(
                message_key="permissions.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
