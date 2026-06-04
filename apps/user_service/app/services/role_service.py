"""Service for role business logic"""

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.role_repository import RoleRepository
from apps.user_service.app.schemas.admin_access_management import (
    CreateRoleRequest,
    RoleDetailItem,
    RoleItem,
    UpdateRoleRequest,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    format_permissions_data,
    safe_json_loads,
    validate_uuid_format,
)
from libs.shared_utils.common_query import (
    ALL_CUSTOM_FIELDS_MANAGEMENT_PERMISSION_CODES,
    custom_fields_permission_codes_to_add,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("role_service")


class RoleService:
    """Service for role business logic.

    Handles all business logic related to roles, including validation,
    permission management, and role operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize RoleService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        # Initialize repository with database connection
        self.role_repository = RoleRepository(db_connection=db_connection)

    # ROLE OPERATIONS
    async def create_role(self, role_data: CreateRoleRequest) -> dict[str, Any]:
        """Create a new role with permissions.

        Args:
            role_data: Role creation data

        Returns:
            dict: Created role details

        Raises:
            ConflictException: If role name already exists
            BadRequestException: If validation fails
        """
        # Check if role name is unique
        await self._validate_role_name_unique(
            name=role_data.name, organization_id=self.user_context.organization_id
        )
        permission_ids = await self._resolve_permission_ids(role_data.permission_ids)

        # Create the role
        role = await self.role_repository.create_role(
            name=role_data.name,
            description=role_data.description,
            organization_id=self.user_context.organization_id,
        )

        # Assign permissions if any
        if permission_ids:
            await self.role_repository.assign_permissions_to_role(
                role_id=role["id"],
                organization_id=self.user_context.organization_id,
                permission_ids=permission_ids,
            )

        return role

    async def get_role_details(self, role_id: str) -> dict[str, Any]:
        """Get role details by ID.

        Args:
            role_id: Role ID to retrieve

        Returns:
            dict: Role details with permissions

        Raises:
            NotFoundException: If role is not found
        """
        role = await self.role_repository.get_role_by_id(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        )
        if not role:
            raise NotFoundException(
                message_key="roles.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Get role permissions
        permissions = await self.role_repository.get_role_permissions(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        )

        # Format permissions data
        formatted_permissions = format_permissions_data(permissions)
        # Format response using RoleDetailItem
        return RoleDetailItem(
            id=str(role["id"]),
            name=role["name"],
            description=role["description"],
            is_default=role["is_default"],
            permissions=formatted_permissions,
            created_at=format_iso_datetime(role["created_at"]) or "",
            updated_at=format_iso_datetime(role.get("updated_at", "")) or "",
        )

    async def update_role(self, role_id: str, update_data: UpdateRoleRequest) -> None:
        """Update an existing role.

        Args:
            role_id: Role ID to update
            update_data: Update data

        Raises:
            NotFoundException: If role is not found
            ConflictException: If role name already exists
            BadRequestException: If permission does not exists
        """
        # Check if role exists
        if not await self.role_repository.check_role_exists(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        ):
            raise NotFoundException(
                message_key="roles.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        update_payload = {}

        # Check name uniqueness if name is being updated
        if update_data.name:
            await self._validate_role_name_unique(
                name=update_data.name,
                organization_id=self.user_context.organization_id,
                exclude_role_id=role_id,
            )

            update_payload["name"] = update_data.name

        # Update other fields if provided
        if update_data.description is not None:
            update_payload["description"] = update_data.description

        if update_payload:
            await self.role_repository.update_role(
                role_id=role_id,
                organization_id=self.user_context.organization_id,
                update_data=update_payload,
            )

        # Handle permission updates if provided
        if update_data.permission_ids:
            resolved_permission_ids = await self._resolve_permission_ids(update_data.permission_ids)

            # Compute permission changes to avoid full replace
            current_permission_ids = set(
                await self.role_repository.get_role_permission_ids(
                    role_id=role_id,
                    organization_id=self.user_context.organization_id,
                )
            )
            new_permission_ids = set(resolved_permission_ids)
            permissions_to_add, permissions_to_remove = self._compute_permission_changes(
                current_permission_ids, new_permission_ids
            )

            if permissions_to_remove:
                await self.role_repository.remove_permissions_from_role(
                    role_id=role_id,
                    organization_id=self.user_context.organization_id,
                    permission_ids=list(permissions_to_remove),
                )

            if permissions_to_add:
                await self.role_repository.assign_permissions_to_role(
                    role_id=role_id,
                    organization_id=self.user_context.organization_id,
                    permission_ids=list(permissions_to_add),
                )

    async def delete_role(self, role_id: str) -> None:
        """Delete a role.

        Args:
            role_id: Role ID to delete

        Raises:
            NotFoundException: If role is not found
            ForbiddenException: If role is in use or default
        """
        # Check if role exists
        role = await self.role_repository.check_role_exists(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        )
        if not role:
            raise NotFoundException(
                message_key="roles.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Check if role is in use
        user_count = await self.role_repository.check_role_usage(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        )
        if user_count > 0:
            raise ForbiddenException(
                message_key="errors.role_in_use",
                custom_code=CustomStatusCode.FORBIDDEN,
                params={"count": user_count},
            )

        # Delete the role
        await self.role_repository.delete_role(
            role_id=role_id,
            organization_id=self.user_context.organization_id,
        )

    async def list_roles(
        self, search: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[RoleItem], int]:
        """List roles with pagination and search.

        Args:
            search: Optional search term
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            tuple: A tuple containing:
                - List of RoleItem objects
                - Total count of roles matching the criteria
        """
        # Get paginated roles with enriched data
        roles = await self.role_repository.get_roles_list_enriched(
            organization_id=self.user_context.organization_id,
            search=search,
            limit=limit,
            offset=offset,
        )

        # Get total count for pagination
        total = await self.role_repository.get_roles_count(
            organization_id=self.user_context.organization_id,
            search=search,
        )

        # Get all permissions in a single query if needed
        all_permissions = await self._get_all_role_permissions([str(role["id"]) for role in roles])
        # Format roles using RoleItem model
        formatted_roles = [
            RoleItem(
                id=str(role["id"]),
                name=role["name"],
                description=role["description"],
                is_default=role["is_default"],
                created_at=format_iso_datetime(role["created_at"]) or "",
                user_count=role.get("user_count", 0),
                permission_count=len(all_permissions.get(str(role["id"]), [])),
                permission_ids=all_permissions.get(str(role["id"]), []),
                permission_categories=safe_json_loads(role.get("permission_categories", "{}"), {}),
            )
            for role in roles
        ]

        return formatted_roles, total

    async def _get_all_role_permissions(self, role_ids: list[str]) -> dict[str, list[str]]:
        """Get all permissions for multiple roles in a single query.

        Args:
            role_ids: List of role IDs to fetch permissions for

        Returns:
            dict: Mapping of role_id to list of permission IDs
        """
        if not role_ids:
            return {}

        permissions = await self.role_repository.get_permissions_for_roles(
            role_ids=role_ids, organization_id=self.user_context.organization_id
        )

        # Group permissions by role_id
        permissions_by_role = {}
        for perm in permissions:
            role_id = str(perm["role_id"])
            if role_id not in permissions_by_role:
                permissions_by_role[role_id] = []
            permissions_by_role[role_id].append(str(perm["permission_id"]))

        return permissions_by_role

    @staticmethod
    def _compute_permission_changes(
        current_permission_ids: set[str],
        new_permission_ids: set[str],
    ) -> tuple[set[str], set[str]]:
        """Compute permissions to add and remove based on new selection."""
        to_add = new_permission_ids - current_permission_ids
        to_remove = current_permission_ids - new_permission_ids
        return to_add, to_remove

    async def _resolve_permission_ids(self, permission_ids: list[str] | None) -> list[str]:
        """Validate permission IDs and append implied custom-fields permissions.

        Args:
            permission_ids: Permission IDs submitted for a role

        Returns:
            Deduplicated permission IDs including any required custom-fields permissions

        Raises:
            BadRequestException: If any permission ID is invalid or doesn't exist
        """
        if not permission_ids:
            return []

        unique_permission_ids = list(dict.fromkeys(permission_ids))

        for permission_id in unique_permission_ids:
            validate_uuid_format(permission_id, "permission ID")

        rows = await self.role_repository.get_permissions_by_ids_or_codes(
            permission_ids=unique_permission_ids,
            organization_id=self.user_context.organization_id,
            codes=list(ALL_CUSTOM_FIELDS_MANAGEMENT_PERMISSION_CODES),
        )

        id_to_code = {str(row["id"]): row["code"] for row in rows}
        code_to_id = {row["code"]: str(row["id"]) for row in rows}

        if len(id_to_code) < len(unique_permission_ids):
            raise BadRequestException(
                message_key="permissions.errors.invalid_permissions",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        selected_codes = {id_to_code[permission_id] for permission_id in unique_permission_ids}
        missing_custom_field_codes = custom_fields_permission_codes_to_add(selected_codes)

        resolved_permission_ids = list(unique_permission_ids)
        for code in sorted(missing_custom_field_codes):
            custom_field_permission_id = code_to_id.get(code)
            if not custom_field_permission_id:
                raise BadRequestException(
                    message_key="permissions.errors.invalid_permissions",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            if custom_field_permission_id not in resolved_permission_ids:
                resolved_permission_ids.append(custom_field_permission_id)

        return resolved_permission_ids

    async def _validate_role_name_unique(
        self, name: str, organization_id: str, exclude_role_id: str = None
    ) -> None:
        """Validate that a role name is unique within the organization.

        Args:
            name: Role name to check
            organization_id: Organization ID
            exclude_role_id: Optional role ID to exclude from the check

        Raises:
            ConflictException: If a role with this name already exists
        """
        is_name_unique = await self.role_repository.check_role_name_unique(
            name=name,
            organization_id=organization_id,
            exclude_role_id=exclude_role_id,
        )
        if not is_name_unique:
            raise ConflictException(
                message_key="roles.errors.role_name_already_exists",
                params={"role_name": name},
                custom_code=CustomStatusCode.CONFLICT,
            )
