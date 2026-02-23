"""Service for custom field business logic."""

import re
from collections import deque
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.custom_field_repository import (
    CustomFieldRepository,
)
from apps.user_service.app.schemas.custom_fields import (
    CreateCustomFieldRequest,
    CustomFieldResponse,
    FlatFieldUpdateRequest,
    SubFieldResponse,
    UpdateCustomFieldRequest,
)
from apps.user_service.app.schemas.enums import EntityType, FieldType
from apps.user_service.app.utils.common_utils import UserContext, parse_json_field
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("custom_field_service")


class CustomFieldService:
    """Service for custom field business logic.

    Handles all business logic related to custom fields, including validation,
    formatting, and orchestration of custom field operations.
    """

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
    ) -> None:
        """Initialize CustomFieldService with user context and database connection.

        Args:
            db_connection: database connection for postgresql
            user_context: user context with user_id and organization_id
        """
        self.user_context = user_context
        self.db_connection = db_connection
        self.custom_field_repository = CustomFieldRepository(db_connection=db_connection)

    @staticmethod
    def generate_field_key(field_name: str) -> str:
        """Generate field_key from field_name.

        Converts field_name to snake_case: lowercase, spaces -> underscores,
        special characters stripped.

        Args:
            field_name: Display name of the field

        Returns:
            str: Generated field_key
        """
        # Convert to lowercase
        key = field_name.lower().strip()

        # Replace spaces and hyphens with underscores
        key = re.sub(r"[\s\-]+", "_", key)

        # Remove special characters, keep only alphanumeric and underscores
        key = re.sub(r"[^a-z0-9_]", "", key)

        # Remove consecutive underscores
        key = re.sub(r"_+", "_", key)

        # Remove leading/trailing underscores
        key = key.strip("_")

        return key

    async def _check_field_key_uniqueness(
        self,
        organization_id: str,
        entity_type: str,
        field_key: str,
    ) -> None:
        """Check if root field_key already exists and raise exception if it does.

        Only checks root fields (parent_id IS NULL). Descendant fields are validated
        in memory during creation.

        Args:
            organization_id: Organization ID
            entity_type: Entity type
            field_key: Generated field key

        Raises:
            ConflictException: If field_key already exists
        """
        exists = await self.custom_field_repository.check_field_key_exists(
            organization_id, entity_type, field_key
        )

        if exists:
            raise ConflictException(
                message_key="custom_fields.errors.field_key_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

    def _prepare_field_data(
        self,
        field_request: CreateCustomFieldRequest,
        organization_id: str,
        entity_type: str,
        field_key: str,
        user_id: str,
        parent_id: str | None,
    ) -> dict[str, Any]:
        """Prepare field data dictionary for database insertion.

        Args:
            field_request: Field request data
            organization_id: Organization ID
            entity_type: Entity type
            field_key: Generated field key
            user_id: User ID
            parent_id: Parent field ID (None for top-level fields)

        Returns:
            Dictionary with field data ready for database insertion
        """
        field_data: dict[str, Any] = {
            "organization_id": organization_id,
            "entity_type": entity_type,
            "field_name": field_request.field_name,
            "field_key": field_key,
            "field_type": field_request.field_type.value,
            "type_config": field_request.type_config,
            "show_on_create": field_request.show_on_create,
            "show_on_detail": field_request.show_on_detail,
            "is_required": field_request.is_required,
            "sort_order": field_request.sort_order,
            "created_by": user_id,
        }

        # Set optional fields
        if field_request.description is not None:
            field_data["description"] = field_request.description
        if parent_id:
            field_data["parent_id"] = parent_id

        return field_data

    async def _prepare_and_create_sub_fields(
        self,
        sub_fields: list[CreateCustomFieldRequest],
        organization_id: str,
        entity_type: str,
        user_id: str,
        parent_field_id: str,
    ) -> list[str]:
        """Prepare and bulk create sibling sub-fields.

        Args:
            sub_fields: List of sub-field requests
            organization_id: Organization ID
            entity_type: Entity type
            user_id: User ID
            parent_field_id: Parent field ID

        Returns:
            List of created sub-field IDs (as strings)

        Raises:
            ConflictException: If duplicate field_key found in sub-fields
        """
        # Prepare field data for all siblings; in-memory uniqueness check
        # (parent was just created, so no DB check needed)
        seen_keys: set[str] = set()
        fields_data: list[dict[str, Any]] = []
        for sub_field in sub_fields:
            sf_key = self.generate_field_key(sub_field.field_name)
            if sf_key in seen_keys:
                raise ConflictException(
                    message_key="custom_fields.errors.field_key_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                )
            seen_keys.add(sf_key)
            sf_data: dict[str, Any] = {
                "organization_id": organization_id,
                "entity_type": entity_type,
                "field_name": sub_field.field_name,
                "field_key": sf_key,
                "field_type": sub_field.field_type.value,
                "type_config": sub_field.type_config,
                "show_on_create": sub_field.show_on_create,
                "show_on_detail": sub_field.show_on_detail,
                "is_required": sub_field.is_required,
                "sort_order": sub_field.sort_order,
                "created_by": user_id,
                "parent_id": parent_field_id,
            }
            if sub_field.description is not None:
                sf_data["description"] = sub_field.description
            fields_data.append(sf_data)

        # Bulk create all sibling sub-fields
        created_sub_fields = await self.custom_field_repository.bulk_create_custom_fields(
            fields_data
        )

        return created_sub_fields

    def _queue_grandchildren(
        self,
        queue: deque[tuple[CreateCustomFieldRequest, str | None, int]],
        sub_fields: list[CreateCustomFieldRequest],
        created_sub_field_ids: list[str],
        depth: int,
    ) -> None:
        """Queue grandchildren for processing if they are OBJECT or LIST types.

        Args:
            queue: Queue of fields to process
            sub_fields: List of sub-field requests
            created_sub_field_ids: List of created sub-field IDs (order matches sub_fields)
            depth: Current nesting depth
        """
        # Queue grandchildren: OBJECT/LIST sub-fields' children need to be created
        for sub_field, created_id in zip(sub_fields, created_sub_field_ids, strict=True):
            if sub_field.field_type in (FieldType.OBJECT, FieldType.LIST) and sub_field.sub_fields:
                for grandchild in sub_field.sub_fields:
                    queue.append(
                        (
                            grandchild,
                            created_id,
                            depth + 1,
                        )
                    )

    async def _create_field_iterative(
        self,
        root_field_request: CreateCustomFieldRequest,
        organization_id: str,
        user_id: str,
        entity_type: str,
    ) -> None:
        """Iteratively create a field and all nested sub-fields using a queue.

        Uses iterative approach with a queue to avoid recursion.

        Args:
            root_field_request: Root field request data
            organization_id: Organization ID
            user_id: User ID
            entity_type: Entity type

        Raises:
            ConflictException: If field_key already exists
            ValidationException: If validation fails
        """
        # Queue: (field_request, parent_id, depth)
        # parent_id is None for top-level; use request's parent_id when adding nested via update API
        initial_parent_id = root_field_request.parent_id
        queue: deque[tuple[CreateCustomFieldRequest, str | None, int]] = deque(
            [(root_field_request, initial_parent_id, 0)]
        )

        while queue:
            field_request, parent_id, depth = queue.popleft()

            # Generate field_key from field_name
            field_key = self.generate_field_key(field_request.field_name)

            # Check field_key uniqueness only for root fields
            # Descendant fields are validated in memory during bulk creation
            if parent_id is None:
                await self._check_field_key_uniqueness(organization_id, entity_type, field_key)

            # Prepare field data
            field_data = self._prepare_field_data(
                field_request,
                organization_id,
                entity_type,
                field_key,
                user_id,
                parent_id,
            )

            # Create the field
            created_field_result = await self.custom_field_repository.create_custom_field(
                field_data
            )
            created_field_id = str(created_field_result["id"])

            # Bulk create sibling sub-fields if this is an object or list type with sub_fields
            if (
                field_request.field_type in (FieldType.OBJECT, FieldType.LIST)
                and field_request.sub_fields
            ):
                created_sub_fields = await self._prepare_and_create_sub_fields(
                    field_request.sub_fields,
                    organization_id,
                    entity_type,
                    user_id,
                    created_field_id,
                )

                # Queue grandchildren for processing
                self._queue_grandchildren(
                    queue,
                    field_request.sub_fields,
                    created_sub_fields,
                    depth,
                )

    async def create_custom_field(self, request_data: CreateCustomFieldRequest) -> None:
        """Create a new custom field definition.

        Supports creating:
        - Top-level fields (with entity_type)
        - Object parent fields with nested sub-fields iteratively
        (with entity_type, field_type='object', sub_fields array)
        - List fields with a single child field
        (with entity_type, field_type='list', sub_fields array with exactly one item)

        Args:
            request_data: Request data for creating custom field

        Raises:
            ConflictException: If field_key already exists
            ValidationException: If validation fails or depth exceeded
        """
        # Validate entity_type is provided for top-level fields
        if not request_data.entity_type:
            raise ValidationException(
                message_key="custom_fields.errors.entity_type_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        # Iteratively create field and all nested sub-fields
        await self._create_field_iterative(
            request_data,
            organization_id,
            user_id,
            request_data.entity_type.value,
        )

    def _row_to_sub_field(
        self, row: dict[str, Any], children_map: dict[str | None, list[dict[str, Any]]]
    ) -> SubFieldResponse:
        """Build SubFieldResponse from a DB row."""
        type_config = parse_json_field(row.get("type_config", {}))
        return SubFieldResponse(
            id=str(row["id"]),
            field_name=row["field_name"],
            field_key=row["field_key"],
            description=row.get("description"),
            field_type=row["field_type"],
            show_on_create=row["show_on_create"],
            show_on_detail=row["show_on_detail"],
            is_required=row["is_required"],
            type_config=type_config,
            sort_order=row["sort_order"],
            is_active=row["is_active"],
            entity_type=row.get("entity_type"),
            parent_id=str(row["parent_id"]),
            sub_fields=self._build_sub_fields(str(row["id"]), children_map),
        )

    def _build_sub_fields(
        self,
        parent_id: str,
        children_map: dict[str | None, list[dict[str, Any]]],
    ) -> list[SubFieldResponse]:
        """Build list of SubFieldResponse for a parent from children_map."""
        children = children_map.get(parent_id, [])
        return [self._row_to_sub_field(row, children_map) for row in children]

    def _rows_to_children_map(
        self, rows: list[dict[str, Any]]
    ) -> dict[str | None, list[dict[str, Any]]]:
        """Build parent_id → children lookup from flat rows. Shared by list and by_id."""
        children_map: dict[str | None, list[dict[str, Any]]] = {}
        for row in rows:
            pid = str(row["parent_id"]) if row.get("parent_id") is not None else None
            children_map.setdefault(pid, []).append(row)
        return children_map

    async def get_custom_fields_list(
        self, entity_type: EntityType
    ) -> tuple[list[CustomFieldResponse], int]:
        """Get list of custom fields for an organization.

        Fetches all custom fields for the org in one query, then filters by
        entity_type and builds the tree in memory.
        """
        organization_id = self.user_context.organization_id
        all_rows = await self.custom_field_repository.get_custom_fields_by_entity_type(
            organization_id,
            entity_type,
        )
        children_map = self._rows_to_children_map(all_rows)
        roots = children_map.get(None, [])

        result = [self._row_to_custom_field_response(field, children_map) for field in roots]
        return result, len(result)

    def _row_to_custom_field_response(
        self,
        row: dict[str, Any],
        children_map: dict[str | None, list[dict[str, Any]]],
    ) -> CustomFieldResponse:
        """Build CustomFieldResponse from a DB row with sub_fields from children_map."""
        type_config = parse_json_field(row.get("type_config", {}))
        return CustomFieldResponse(
            id=str(row["id"]),
            field_name=row["field_name"],
            field_key=row["field_key"],
            description=row.get("description"),
            field_type=row["field_type"],
            show_on_create=row["show_on_create"],
            show_on_detail=row["show_on_detail"],
            is_required=row["is_required"],
            type_config=type_config,
            sort_order=row["sort_order"],
            is_active=row["is_active"],
            entity_type=row.get("entity_type"),
            parent_id=(str(row["parent_id"]) if row.get("parent_id") is not None else None),
            sub_fields=self._build_sub_fields(str(row["id"]), children_map),
        )

    async def get_custom_field_by_id(self, field_id: str) -> CustomFieldResponse:
        """Get a single custom field by id with sub_fields populated.

        Uses repository subtree query (field + descendants by parent_id only).
        Raises NotFoundException if field not found or not in organization.
        """
        organization_id = self.user_context.organization_id
        rows = await self.custom_field_repository.get_custom_field_with_descendants(
            field_id, organization_id
        )
        if not rows:
            raise NotFoundException(
                message_key="custom_fields.errors.field_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        children_map = self._rows_to_children_map(rows)
        root_row = next(r for r in rows if str(r["id"]) == str(field_id))
        return self._row_to_custom_field_response(root_row, children_map)

    def _prepare_root_field_update_data(
        self,
        request: UpdateCustomFieldRequest,
        user_id: str,
    ) -> dict[str, Any]:
        """Prepare root field update data dictionary for database update.

        Args:
            request: Update request data
            user_id: User ID

        Returns:
            Dictionary with root field update data ready for database update
        """
        update_data: dict[str, Any] = {}

        if request.field_name is not None:
            update_data["field_name"] = request.field_name
        if request.description is not None:
            update_data["description"] = request.description
        if request.field_type is not None:
            update_data["field_type"] = request.field_type.value
        if request.type_config is not None:
            update_data["type_config"] = request.type_config
        if request.show_on_create is not None:
            update_data["show_on_create"] = request.show_on_create
        if request.show_on_detail is not None:
            update_data["show_on_detail"] = request.show_on_detail
        if request.is_required is not None:
            update_data["is_required"] = request.is_required
        if request.sort_order is not None:
            update_data["sort_order"] = request.sort_order

        if update_data:
            update_data["updated_by"] = user_id

        return update_data

    def _prepare_flat_field_update_data(
        self,
        request: FlatFieldUpdateRequest,
        user_id: str,
    ) -> dict[str, Any]:
        """Prepare flat field update data dictionary for database update.

        Args:
            request: Flat field update request data
            user_id: User ID

        Returns:
            Dictionary with field update data ready for database update
        """
        update_data: dict[str, Any] = {"id": request.id}

        if request.field_name is not None:
            update_data["field_name"] = request.field_name
        if request.description is not None:
            update_data["description"] = request.description
        if request.field_type is not None:
            update_data["field_type"] = request.field_type.value
        if request.type_config is not None:
            update_data["type_config"] = request.type_config
        if request.show_on_create is not None:
            update_data["show_on_create"] = request.show_on_create
        if request.show_on_detail is not None:
            update_data["show_on_detail"] = request.show_on_detail
        if request.is_required is not None:
            update_data["is_required"] = request.is_required
        if request.sort_order is not None:
            update_data["sort_order"] = request.sort_order

        update_data["updated_by"] = user_id

        return update_data

    async def _create_fields_with_nested_children(
        self,
        fields_to_add: list[CreateCustomFieldRequest],
        entity_type: str,
        user_id: str,
    ) -> None:
        """Create fields with nested children recursively (only for object types).

        Groups siblings by parent_id for bulk creation, then processes nested children.

        Args:
            fields_to_add: List of field requests to create
            entity_type: Entity type
            user_id: User ID

        Raises:
            ValidationException: If max nesting depth exceeded
        """
        # Group initial fields by parent_id for bulk creation
        fields_by_parent: dict[str, list[CreateCustomFieldRequest]] = {}
        for field_request in fields_to_add:
            parent_id = field_request.parent_id
            if not parent_id:
                raise ValidationException(
                    message_key="custom_fields.errors.parent_id_required_for_add",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            fields_by_parent.setdefault(parent_id, []).append(field_request)

        # Bulk create siblings grouped by parent_id
        for parent_id, siblings in fields_by_parent.items():
            created_ids = await self._prepare_and_create_sub_fields(
                siblings,
                self.user_context.organization_id,
                entity_type,
                user_id,
                parent_id,
            )

            # Process nested children for each created sibling using the same iterative logic
            for field_request, created_id in zip(siblings, created_ids, strict=True):
                if (
                    field_request.field_type in (FieldType.OBJECT, FieldType.LIST)
                    and field_request.sub_fields
                ):
                    # Process each sub_field using the same iterative creation logic
                    for sub_field in field_request.sub_fields:
                        # Set parent_id on sub_field to link it to the created parent
                        sub_field.parent_id = created_id
                        await self._create_field_iterative(
                            sub_field,
                            self.user_context.organization_id,
                            user_id,
                            entity_type,
                        )

    def _build_subtree_lookup_maps(
        self, subtree_rows: list[dict[str, Any]]
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Build field_type_map and direct_children from subtree rows."""
        field_type_map = {str(row["id"]): row.get("field_type") for row in subtree_rows}
        direct_children: dict[str, list[str]] = {}
        for row in subtree_rows:
            parent_id = row.get("parent_id")
            if parent_id:
                parent_id_str = str(parent_id)
                child_id_str = str(row["id"])
                direct_children.setdefault(parent_id_str, []).append(child_id_str)
        return field_type_map, direct_children

    async def _delete_descendants_if_root_type_change(
        self,
        subtree_rows: list[dict[str, Any]],
        field_id: str,
        new_field_type: str | None,
        direct_children: dict[str, list[str]],
        organization_id: str,
    ) -> None:
        """Auto-delete root's descendants when root changes from OBJECT/LIST.

        Deletes descendants when changing to non-OBJECT/non-LIST.
        """
        if new_field_type is None:
            return
        root_row = next(r for r in subtree_rows if str(r["id"]) == str(field_id))
        current_root_type = root_row.get("field_type")
        if current_root_type not in (
            FieldType.OBJECT.value,
            FieldType.LIST.value,
        ) or new_field_type in (FieldType.OBJECT.value, FieldType.LIST.value):
            return
        root_children = direct_children.get(field_id, [])
        if not root_children:
            return
        await self.custom_field_repository.bulk_delete_custom_fields_with_descendants(
            organization_id, root_children
        )

    async def _delete_descendants_for_object_to_non_object(
        self,
        update_items: list[FlatFieldUpdateRequest] | None,
        field_type_map: dict[str, str],
        direct_children: dict[str, list[str]],
        organization_id: str,
    ) -> None:
        """Auto-delete descendants for update items changing from OBJECT/LIST.

        Deletes descendants when changing to non-OBJECT/non-LIST.
        """
        if not update_items:
            return
        for update_item in update_items:
            if update_item.field_type is None:
                continue
            current_type = field_type_map.get(update_item.id)
            if current_type not in (
                FieldType.OBJECT.value,
                FieldType.LIST.value,
            ) or update_item.field_type in (FieldType.OBJECT, FieldType.LIST):
                continue
            children = direct_children.get(update_item.id, [])
            if not children:
                continue
            await self.custom_field_repository.bulk_delete_custom_fields_with_descendants(
                organization_id, children
            )

    async def update_custom_field(
        self, field_id: str, request_data: UpdateCustomFieldRequest
    ) -> None:
        """Update a custom field definition using flat ID-based design.

        Process order: remove → update → add
        All operations validated against fetched subtree before execution.

        Args:
            field_id: Custom field ID to update (root of subtree)
            request_data: Update request data with flat update/remove/add arrays

        Raises:
            NotFoundException: If field not found or IDs not in subtree
            ValidationException: If validation fails
        """
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        subtree_rows = await self.custom_field_repository.get_custom_field_with_descendants(
            field_id, organization_id
        )
        if not subtree_rows:
            raise NotFoundException(
                message_key="custom_fields.errors.field_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        subtree_ids = {str(row["id"]) for row in subtree_rows}
        await self._validate_ids_in_subtree(subtree_ids, request_data)

        field_type_map, direct_children = self._build_subtree_lookup_maps(subtree_rows)
        new_root_type = (
            request_data.field_type.value if (request_data.field_type is not None) else None
        )
        await self._delete_descendants_if_root_type_change(
            subtree_rows, field_id, new_root_type, direct_children, organization_id
        )
        await self._delete_descendants_for_object_to_non_object(
            request_data.update, field_type_map, direct_children, organization_id
        )

        root_row = next(r for r in subtree_rows if str(r["id"]) == str(field_id))
        entity_type = root_row.get("entity_type")

        root_update_data = self._prepare_root_field_update_data(request_data, user_id)
        if root_update_data:
            await self.custom_field_repository.update_custom_field(
                field_id, organization_id, root_update_data
            )

        if request_data.remove:
            await self.custom_field_repository.bulk_delete_custom_fields_with_descendants(
                organization_id, request_data.remove
            )

        if request_data.update:
            updates = [
                self._prepare_flat_field_update_data(update_item, user_id)
                for update_item in request_data.update
            ]
            if updates:
                await self.custom_field_repository.bulk_update_custom_fields(
                    organization_id, updates
                )

        if request_data.add:
            await self._create_fields_with_nested_children(
                request_data.add,
                entity_type,
                user_id,
            )

    async def _validate_ids_in_subtree(
        self, subtree_ids: set[str], request_data: UpdateCustomFieldRequest
    ) -> None:
        """Validate all IDs exist in subtree."""
        if request_data.update:
            for update_item in request_data.update:
                if update_item.id not in subtree_ids:
                    raise NotFoundException(
                        message_key="custom_fields.errors.field_not_found",
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )

        if request_data.remove:
            for remove_id in request_data.remove:
                if remove_id not in subtree_ids:
                    raise NotFoundException(
                        message_key="custom_fields.errors.field_not_found",
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )

        if request_data.add:
            for add_item in request_data.add:
                if add_item.parent_id not in subtree_ids:
                    raise NotFoundException(
                        message_key="custom_fields.errors.field_not_found",
                        custom_code=CustomStatusCode.NOT_FOUND,
                    )

    async def delete_custom_field(self, field_id: str) -> None:
        """Delete a custom field and all its descendants (hard delete).

        Validates that the field exists and belongs to the organization,
        then performs a cascading hard delete of the field and all descendants.

        Args:
            field_id: Custom field ID to delete

        Raises:
            NotFoundException: If field not found or not in organization
        """
        organization_id = self.user_context.organization_id

        # Validate field exists and belongs to organization
        subtree_rows = await self.custom_field_repository.get_custom_field_with_descendants(
            field_id, organization_id
        )
        if not subtree_rows:
            raise NotFoundException(
                message_key="custom_fields.errors.field_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Hard delete the field and all its descendants
        await self.custom_field_repository.bulk_delete_custom_fields_with_descendants(
            organization_id, [field_id]
        )

    async def validate_and_format_custom_fields(
        self,
        custom_fields: dict[str, Any],
        entity_type: EntityType,
    ) -> dict[str, Any]:
        """Validate and format custom fields against field definitions.

        Args:
            custom_fields: Raw custom fields dictionary from request
            entity_type: Entity type (company or contact)

        Returns:
            Formatted custom fields dictionary ready for storage

        Raises:
            ValidationException: If validation fails
        """
        if not custom_fields:
            return {}

        # Get custom field definitions for this entity type
        field_definitions, _ = await self.get_custom_fields_list(entity_type)

        if not field_definitions:
            raise ValidationException(
                message_key="clients.errors.custom_field_definitions_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"entity_type": entity_type.value},
            )

        # Build maps: top-level fields for required check, all fields for validation
        top_level_fields, all_fields = self._build_field_map(field_definitions)

        # Validate and format each custom field
        formatted_fields: dict[str, Any] = {}
        for field_key, field_value in custom_fields.items():
            if field_key not in all_fields:
                raise ValidationException(
                    message_key="clients.errors.custom_field_not_defined",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key},
                )

            field_def = all_fields[field_key]
            validated_value = self._validate_field_value(field_key, field_value, field_def)
            formatted_fields[field_key] = validated_value

        # Check required fields (only top-level)
        self._validate_required_fields(top_level_fields, formatted_fields)

        return formatted_fields

    def _build_field_map(
        self, field_definitions: list[Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build maps of field_key -> field_definition.

        Args:
            field_definitions: List of CustomFieldResponse objects

        Returns:
            Tuple of (top_level_fields, all_fields) dictionaries mapping
            field_key to field definition
        """
        top_level_fields: dict[str, Any] = {}
        all_fields: dict[str, Any] = {}

        def add_field(field: Any, is_top_level: bool = False) -> None:
            """Recursively add field and its sub-fields to the maps."""
            all_fields[field.field_key] = field
            if is_top_level:
                top_level_fields[field.field_key] = field
            if hasattr(field, "sub_fields") and field.sub_fields:
                for sub_field in field.sub_fields:
                    add_field(sub_field, is_top_level=False)

        for field in field_definitions:
            add_field(field, is_top_level=True)

        return top_level_fields, all_fields

    def _validate_string_field(self, field_key: str, field_value: Any) -> str:
        """Validate a string field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        return field_value

    def _validate_number_field(self, field_key: str, field_value: Any) -> float:
        """Validate a number field value."""
        if not isinstance(field_value, (int, float)):
            try:
                return float(field_value)
            except (ValueError, TypeError) as exc:
                raise ValidationException(
                    message_key="clients.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "expected_type": "number"},
                ) from exc
        return float(field_value)

    def _validate_yes_no_field(self, field_key: str, field_value: Any) -> bool:
        """Validate a yes/no (boolean) field value."""
        if not isinstance(field_value, bool):
            if isinstance(field_value, str):
                return field_value.lower() in ("true", "yes", "1")
            if isinstance(field_value, int):
                return bool(field_value)
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "boolean"},
            )
        return field_value

    def _validate_url_field(self, field_key: str, field_value: Any) -> str:
        """Validate a URL field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        if not (field_value.startswith("http://") or field_value.startswith("https://")):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        return field_value

    def _validate_dropdown_field(self, field_key: str, field_value: Any, field_def: Any) -> str:
        """Validate a dropdown field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        options = field_def.type_config.get("options", [])
        if options and field_value not in options:
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_option",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "value": field_value},
            )
        return field_value

    def _validate_range_slider_field(
        self, field_key: str, field_value: Any, field_def: Any
    ) -> float:
        """Validate a range slider field value."""
        if not isinstance(field_value, (int, float)):
            try:
                field_value = float(field_value)
            except (ValueError, TypeError) as exc:
                raise ValidationException(
                    message_key="clients.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "expected_type": "number"},
                ) from exc
        type_config = field_def.type_config or {}
        min_val = type_config.get("min", 0)
        max_val = type_config.get("max", 100)
        if not min_val <= field_value <= max_val:
            raise ValidationException(
                message_key="clients.errors.custom_field_out_of_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={
                    "field_key": field_key,
                    "value": field_value,
                    "min": min_val,
                    "max": max_val,
                },
            )
        return float(field_value)

    def _validate_currency_field(
        self, field_key: str, field_value: Any, field_def: Any
    ) -> dict[str, Any]:
        """Validate a currency field value."""
        if not isinstance(field_value, dict):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_currency_format",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        amount = field_value.get("amount")
        currency_code = field_value.get("currency_code")
        if amount is None or currency_code is None:
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_currency_format",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        allowed_currencies = field_def.type_config.get("allowed_currencies", [])
        if allowed_currencies and currency_code not in allowed_currencies:
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_currency",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "currency": currency_code},
            )
        return {"amount": float(amount), "currency_code": str(currency_code)}

    def _validate_file_upload_field(self, field_key: str, field_value: Any, field_def: Any) -> Any:
        """Validate a file upload field value."""
        type_config = field_def.type_config or {}
        allow_multiple = type_config.get("allow_multiple", False)
        max_files = type_config.get("max_files", 1)

        if allow_multiple:
            if not isinstance(field_value, list):
                raise ValidationException(
                    message_key="clients.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "expected_type": "array"},
                )
            if len(field_value) > max_files:
                raise ValidationException(
                    message_key="clients.errors.custom_field_too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "max": max_files},
                )
            return field_value

        if isinstance(field_value, list):
            if len(field_value) > 1:
                raise ValidationException(
                    message_key="clients.errors.custom_field_too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "max": 1},
                )
            return field_value[0] if field_value else None
        return field_value

    def _validate_image_field(self, field_key: str, field_value: Any, field_def: Any) -> Any:
        """Validate an image field value."""
        type_config = field_def.type_config or {}
        allow_multiple = type_config.get("allow_multiple", False)
        max_files = type_config.get("max_files", 1)

        if allow_multiple:
            if not isinstance(field_value, list):
                raise ValidationException(
                    message_key="clients.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "expected_type": "array"},
                )
            if len(field_value) > max_files:
                raise ValidationException(
                    message_key="clients.errors.custom_field_too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "max": max_files},
                )
            return field_value

        if isinstance(field_value, list):
            if len(field_value) > 1:
                raise ValidationException(
                    message_key="clients.errors.custom_field_too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "max": 1},
                )
            return field_value[0] if field_value else None
        return field_value

    def _validate_address_field(
        self, field_key: str, field_value: Any, field_def: Any
    ) -> dict[str, Any]:
        """Validate an address field value."""
        if not isinstance(field_value, dict):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "object"},
            )
        type_config = field_def.type_config or {}
        include_lat_long = type_config.get("include_lat_long", False)

        address = {
            "address_line1": field_value.get("address_line1", ""),
            "address_line2": field_value.get("address_line2"),
            "city": field_value.get("city"),
            "state": field_value.get("state"),
            "postal_code": field_value.get("postal_code"),
            "country": field_value.get("country"),
        }

        if include_lat_long:
            address["latitude"] = field_value.get("latitude")
            address["longitude"] = field_value.get("longitude")

        return address

    def _validate_object_field(
        self, field_key: str, field_value: Any, field_def: Any
    ) -> dict[str, Any]:
        """Validate an object field value."""
        if not isinstance(field_value, dict):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "object"},
            )
        validated_object: dict[str, Any] = {}
        if hasattr(field_def, "sub_fields") and field_def.sub_fields:
            for sub_field in field_def.sub_fields:
                sub_key = sub_field.field_key
                if sub_key in field_value:
                    validated_object[sub_key] = self._validate_field_value(
                        sub_key, field_value[sub_key], sub_field
                    )
                elif sub_field.is_required:
                    raise ValidationException(
                        message_key="clients.errors.custom_field_required",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                        params={"field_key": sub_key},
                    )
        return validated_object

    def _validate_list_field(self, field_key: str, field_value: Any, field_def: Any) -> list[Any]:
        """Validate a list field value."""
        if not isinstance(field_value, list):
            raise ValidationException(
                message_key="clients.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "array"},
            )
        if hasattr(field_def, "sub_fields") and field_def.sub_fields:
            child_field = field_def.sub_fields[0]  # List has exactly one child
            validated_list = []
            for item in field_value:
                validated_item = self._validate_field_value(f"{field_key}[item]", item, child_field)
                validated_list.append(validated_item)
            return validated_list
        return field_value

    def _validate_field_value(self, field_key: str, field_value: Any, field_def: Any) -> Any:
        """Validate a single field value against its definition.

        Args:
            field_key: Field key
            field_value: Field value to validate
            field_def: Field definition (CustomFieldResponse or SubFieldResponse)

        Returns:
            Validated and formatted field value

        Raises:
            ValidationException: If validation fails
        """
        field_type = FieldType(field_def.field_type)

        # Handle None values
        if field_value is None:
            if field_def.is_required:
                raise ValidationException(
                    message_key="clients.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key},
                )
            return None

        # Dispatch to specific validator based on field type
        validators = {
            FieldType.TEXT: lambda k, v, d: self._validate_string_field(k, v),
            FieldType.LONG_TEXT: lambda k, v, d: self._validate_string_field(k, v),
            FieldType.RICH_TEXT: lambda k, v, d: self._validate_string_field(k, v),
            FieldType.NUMBER: lambda k, v, d: self._validate_number_field(k, v),
            FieldType.DATE: lambda k, v, d: self._validate_string_field(k, v),
            FieldType.YES_NO: lambda k, v, d: self._validate_yes_no_field(k, v),
            FieldType.URL: lambda k, v, d: self._validate_url_field(k, v),
            FieldType.DROPDOWN: self._validate_dropdown_field,
            FieldType.RANGE_SLIDER: self._validate_range_slider_field,
            FieldType.CURRENCY: self._validate_currency_field,
            FieldType.FILE_UPLOAD: self._validate_file_upload_field,
            FieldType.IMAGE: self._validate_image_field,
            FieldType.ADDRESS: self._validate_address_field,
            FieldType.OBJECT: self._validate_object_field,
            FieldType.LIST: self._validate_list_field,
        }

        validator = validators.get(field_type)
        if validator:
            return validator(field_key, field_value, field_def)

        # Unknown field type - not allowed
        raise ValidationException(
            message_key="clients.errors.custom_field_invalid_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"field_key": field_key, "expected_type": "supported field type"},
        )

    def _validate_required_fields(
        self,
        field_map: dict[str, Any],
        formatted_fields: dict[str, Any],
    ) -> None:
        """Validate that all required fields are present.

        Args:
            field_map: Map of field_key -> field_definition
            formatted_fields: Formatted custom fields dictionary

        Raises:
            ValidationException: If required field is missing
        """
        for field_key, field_def in field_map.items():
            if field_def.is_required and field_key not in formatted_fields:
                raise ValidationException(
                    message_key="clients.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key},
                )
