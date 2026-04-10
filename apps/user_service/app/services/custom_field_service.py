"""Service for custom field business logic."""

# pylint: disable=too-many-lines
import copy
import re
import uuid
from collections import deque
from typing import Any, Literal

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
    ) -> str:
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
        root_created_id: str | None = None

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
            if root_created_id is None and depth == 0 and parent_id == initial_parent_id:
                root_created_id = created_field_id

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

        return root_created_id or ""

    async def create_custom_field(self, request_data: CreateCustomFieldRequest) -> str:
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
        return await self._create_field_iterative(
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
            parent_id_key = str(row["parent_id"]) if row.get("parent_id") is not None else None
            children_map.setdefault(parent_id_key, []).append(row)
        return children_map

    async def get_custom_fields_list(
        self,
        entity_type: EntityType,
        *,
        organization_id: str | None = None,
    ) -> tuple[list[CustomFieldResponse], int]:
        """Get list of custom fields for an organization.

        Fetches all custom fields for the org in one query, then filters by
        entity_type and builds the tree in memory.

        These definitions drive ``resolve_fields_for_read`` for FieldCell JSONB.
        """
        organization_id = organization_id or self.user_context.organization_id
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
        root_row = next(row for row in rows if str(row["id"]) == str(field_id))
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
        root_row = next(row for row in subtree_rows if str(row["id"]) == str(field_id))
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

        root_row = next(row for row in subtree_rows if str(row["id"]) == str(field_id))
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

    def _root_id_to_def(self, field_definitions: list[Any]) -> dict[str, Any]:
        """Map root field definition IDs to their definitions."""
        return {str(definition.id): definition for definition in field_definitions}

    def _flatten_field_definitions(self, field_definitions: list[Any]) -> dict[str, Any]:
        """Map every custom field definition id (root or nested) to its definition object."""
        out: dict[str, Any] = {}

        def walk(definition: Any) -> None:
            out[str(definition.id)] = definition
            for sub_definition in definition.sub_fields or []:
                walk(sub_definition)

        for root_definition in field_definitions:
            walk(root_definition)
        return out

    @staticmethod
    def _shortcut_target_instance_ids(
        shortcut_patches: list[dict[str, Any]],
    ) -> frozenset[str]:
        """Instance ids addressed by PATCH shortcuts (strict validation, no reconcile-null)."""
        return frozenset(
            str(shortcut_patch["instance_id"])
            for shortcut_patch in shortcut_patches
            if shortcut_patch.get("instance_id") is not None
        )

    @staticmethod
    def _effective_reconcile(
        for_reconcile: bool,
        instance_id: str,
        explicit_instance_ids: frozenset[str] | None,
    ) -> bool:
        """Reconcile-null stale optional values only when not an explicit shortcut target."""
        if not for_reconcile:
            return False
        if explicit_instance_ids and instance_id in explicit_instance_ids:
            return False
        return True

    def _partition_custom_field_patch_entries(
        self,
        patches: list[dict[str, Any]],
        id_to_def: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split PATCH payload into root FieldCells vs instance_id shortcut updates."""
        normal: list[dict[str, Any]] = []
        shortcuts: list[dict[str, Any]] = []
        for patch_index, patch_dict in enumerate(patches):
            if not isinstance(patch_dict, dict):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": f"custom_fields[{patch_index}]",
                        "expected_type": "object (FieldCell)",
                    },
                )
            fid = patch_dict.get("field_id")
            iid = patch_dict.get("instance_id")
            if fid is not None and str(fid) in id_to_def:
                normal.append(patch_dict)
            elif iid is not None:
                shortcuts.append(patch_dict)
            else:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_patch_root_or_instance_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return normal, shortcuts

    @staticmethod
    def _find_field_cells_by_instance_id(
        roots: list[dict[str, Any]], target_iid: str
    ) -> list[dict[str, Any]]:
        """Return every stored cell dict whose instance_id matches (DFS)."""
        matches: list[dict[str, Any]] = []

        def walk(cell: Any) -> None:
            if not isinstance(cell, dict):
                return
            if str(cell.get("instance_id") or "") == str(target_iid):
                matches.append(cell)
            for list_item_cell in cell.get("items") or []:
                walk(list_item_cell)
            for sub_field_cell in cell.get("sub_fields") or []:
                walk(sub_field_cell)

        for root_cell in roots:
            walk(root_cell)
        return matches

    def _apply_instance_id_shortcut_patch(
        self,
        working_roots: list[dict[str, Any]],
        patch: dict[str, Any],
        flat_defs: dict[str, Any],
    ) -> None:
        """Apply a nested update addressed only by instance_id (optional field_id to verify)."""
        iid = patch.get("instance_id")
        matches = self._find_field_cells_by_instance_id(working_roots, str(iid))
        if not matches:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_instance_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"instance_id": str(iid)},
            )
        if len(matches) > 1:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_instance_ambiguous",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"instance_id": str(iid)},
            )
        leaf = matches[0]
        fid_cell = str(leaf.get("field_id") or "")
        pfid = patch.get("field_id")
        if pfid is not None and str(pfid) != fid_cell:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_instance_field_mismatch",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"instance_id": str(iid), "field_id": str(pfid)},
            )
        field_def = flat_defs.get(fid_cell)
        if field_def is None:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_not_defined",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_id": fid_cell},
            )
        path_key = field_def.field_key
        merged = self._merge_child_cell(field_def, leaf, patch, path_key)
        resolved_field_type = FieldType(field_def.field_type)
        if resolved_field_type not in (FieldType.OBJECT, FieldType.LIST):
            leaf["value"] = merged["value"]
        elif resolved_field_type == FieldType.OBJECT:
            leaf["sub_fields"] = merged["sub_fields"]
        else:
            leaf["items"] = merged["items"]

    @staticmethod
    def _new_instance_id() -> str:
        """Generate a new instance ID."""
        return str(uuid.uuid4())

    @staticmethod
    def _def_type_value(field_def: Any) -> str:
        """Get the type value from a field definition."""
        raw_type = field_def.field_type
        return raw_type.value if isinstance(raw_type, FieldType) else str(raw_type)

    @staticmethod
    def _stored_type_str(cell: dict[str, Any]) -> str | None:
        """Get the stored type string from a cell."""
        type_value = cell.get("type")
        return None if type_value is None else str(type_value)

    def _assert_exactly_one_discriminator(self, cell: dict[str, Any], path_key: str) -> str:
        """Assert that exactly one discriminator is present in a cell."""
        present = [
            discriminator_key
            for discriminator_key in ("value", "sub_fields", "items")
            if discriminator_key in cell
        ]
        if len(present) != 1:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key},
            )
        return present[0]

    def _enforce_create_no_server_keys(self, cell: Any, path_key: str) -> None:
        """Enforce that no server keys are present in a cell."""
        if not isinstance(cell, dict):
            return
        if "type" in cell:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_forbidden_payload_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "forbidden_key": "type"},
            )
        if "instance_id" in cell:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_forbidden_payload_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "forbidden_key": "instance_id"},
            )
        which = self._assert_exactly_one_discriminator(cell, path_key)
        if which == "value":
            return
        if which == "sub_fields":
            subs = cell.get("sub_fields")
            if not isinstance(subs, list):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": path_key, "expected_type": "array (sub_fields)"},
                )
            for child_index, child_cell in enumerate(subs):
                self._enforce_create_no_server_keys(
                    child_cell, f"{path_key}.sub_fields[{child_index}]"
                )
            return
        items = cell.get("items")
        if not isinstance(items, list):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "expected_type": "array (items)"},
            )
        for child_index, child_cell in enumerate(items):
            self._enforce_create_no_server_keys(child_cell, f"{path_key}.items[{child_index}]")

    def _enforce_patch_no_type_key(self, cell: Any, path_key: str) -> None:
        """Enforce that no server keys are present in a cell."""
        if not isinstance(cell, dict):
            return
        if "type" in cell:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_forbidden_payload_key",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "forbidden_key": "type"},
            )
        for nested_array_key in ("sub_fields", "items"):
            if nested_array_key not in cell:
                continue
            nested_cells = cell.get(nested_array_key)
            if not isinstance(nested_cells, list):
                continue
            for child_index, child_cell in enumerate(nested_cells):
                self._enforce_patch_no_type_key(
                    child_cell,
                    f"{path_key}.{nested_array_key}[{child_index}]",
                )

    @staticmethod
    def _cell_explicit_null(cell: Any) -> bool:
        """Check if a cell is explicitly null."""
        return isinstance(cell, dict) and "value" in cell and cell.get("value") is None

    def _parse_roots_create_payload(self, custom_fields: Any) -> list[dict[str, Any]]:
        """Parse the roots create payload."""
        if custom_fields is None:
            return []
        if not isinstance(custom_fields, list):
            raise ValidationException(
                message_key="custom_fields.errors.invalid_custom_fields_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"expected_type": "array"},
            )
        roots = [entry for entry in custom_fields if entry is not None]
        for root_index, root in enumerate(roots):
            if isinstance(root, dict):
                self._enforce_create_no_server_keys(root, f"custom_fields[{root_index}]")
        return roots

    def _parse_patch_roots_payload(self, payload: Any) -> list[dict[str, Any]]:
        """Parse the patch roots payload."""
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise ValidationException(
                message_key="custom_fields.errors.invalid_custom_fields_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"expected_type": "array"},
            )
        roots = [entry for entry in payload if entry is not None]
        for root_index, root in enumerate(roots):
            if isinstance(root, dict):
                self._enforce_patch_no_type_key(root, f"custom_fields[{root_index}]")
        return roots

    @classmethod
    def _parse_roots_storage(cls, stored: Any) -> list[dict[str, Any]]:
        """Parse the roots storage."""
        if stored is None:
            return []
        raw = parse_json_field(stored) if isinstance(stored, str) else stored
        if raw is None:
            return []
        if isinstance(raw, dict):
            return []
        if not isinstance(raw, list):
            return []
        return [
            dict(item)
            for item in raw
            if isinstance(item, dict) and item.get("field_id") is not None
        ]

    def _index_patch_roots(self, roots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Index the patch roots."""
        out: dict[str, dict[str, Any]] = {}
        for item in roots:
            if not isinstance(item, dict):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": "custom_fields", "expected_type": "FieldCell object"},
                )
            fid = item.get("field_id")
            if fid is None:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": "custom_fields",
                        "expected_type": "root FieldCell with field_id",
                    },
                )
            sid = str(fid)
            if sid in out:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_duplicate_root",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_id": sid},
                )
            out[sid] = item
        return out

    @staticmethod
    def _index_stored_roots(roots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Index the stored roots."""
        out: dict[str, dict[str, Any]] = {}
        for item in roots:
            if isinstance(item, dict) and item.get("field_id") is not None:
                out[str(item["field_id"])] = item
        return out

    def _sort_root_cells(
        self, cells: list[dict[str, Any]], field_definitions: list[Any]
    ) -> list[dict[str, Any]]:
        """Sort the root cells."""
        order = {
            str(definition.id): def_index
            for def_index, definition in enumerate(
                sorted(field_definitions, key=lambda field_def: field_def.sort_order)
            )
        }
        return sorted(
            cells,
            key=lambda cell_dict: order.get(str(cell_dict.get("field_id")), 10**9),
        )

    def _index_sub_field_cells(self, cells: list[Any], path_key: str) -> dict[str, dict[str, Any]]:
        """Index the sub field cells."""
        out: dict[str, dict[str, Any]] = {}
        for cell_index, item in enumerate(cells):
            if not isinstance(item, dict):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": f"{path_key}[{cell_index}]",
                        "expected_type": "FieldCell object",
                    },
                )
            fid = item.get("field_id")
            if fid is None:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": f"{path_key}[{cell_index}]",
                        "expected_type": "field_id",
                    },
                )
            sid = str(fid)
            if sid in out:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_duplicate_root",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_id": sid},
                )
            out[sid] = item
        return out

    def _require_instance_id_if_stored(
        self, stored: dict[str, Any] | None, patch: dict[str, Any], field_key: str
    ) -> None:
        """Require an instance ID if stored."""
        if stored and not patch.get("instance_id"):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_patch_instance_id_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )

    def _merge_object_sub_fields(
        self,
        field_def: Any,
        stored_rows: Any,
        patch_rows: Any,
        path_key: str,
    ) -> list[dict[str, Any]]:
        """Merge the object sub fields."""
        s_list = stored_rows if isinstance(stored_rows, list) else []
        p_list = patch_rows if isinstance(patch_rows, list) else []
        patch_map = self._index_sub_field_cells(p_list, f"{path_key}.sub_fields")
        stored_map: dict[str, dict[str, Any]] = {}
        for row in s_list:
            if isinstance(row, dict) and row.get("field_id") is not None:
                stored_map[str(row["field_id"])] = row
        sub_id_to_def = {
            str(sub_field_def.id): sub_field_def for sub_field_def in (field_def.sub_fields or [])
        }
        unknown = set(patch_map) - set(sub_id_to_def)
        if unknown:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_unknown_keys",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "unknown_keys": sorted(unknown)},
            )
        merged: list[dict[str, Any]] = []
        for sub_field_id in sorted(
            sub_id_to_def,
            key=lambda sid_key: sub_id_to_def[sid_key].sort_order,
        ):
            sub_def = sub_id_to_def[sub_field_id]
            stored_sub_cell = stored_map.get(sub_field_id)
            patch_sub_cell = patch_map.get(sub_field_id)
            if patch_sub_cell is not None:
                if (
                    isinstance(patch_sub_cell, dict)
                    and "value" in patch_sub_cell
                    and patch_sub_cell.get("value") is None
                    and FieldType(sub_def.field_type) not in (FieldType.OBJECT, FieldType.LIST)
                ):
                    if sub_def.is_required:
                        raise ValidationException(
                            message_key="custom_fields.errors.custom_field_required",
                            custom_code=CustomStatusCode.VALIDATION_ERROR,
                            params={"field_key": sub_def.field_key},
                        )
                    continue
                merged.append(
                    self._merge_child_cell(
                        sub_def,
                        stored_sub_cell,
                        patch_sub_cell,
                        f"{path_key}.{sub_def.field_key}",
                    )
                )
            elif stored_sub_cell is not None:
                merged.append(copy.deepcopy(stored_sub_cell))
        return merged

    def _merge_list_items(
        self,
        field_def: Any,
        stored_items: Any,
        patch_items: Any,
        path_key: str,
    ) -> list[dict[str, Any]]:
        """Merge the list items."""
        if not field_def.sub_fields:
            return list(patch_items) if isinstance(patch_items, list) else []
        child_def = field_def.sub_fields[0]
        s_rows = stored_items if isinstance(stored_items, list) else []
        p_rows = patch_items if isinstance(patch_items, list) else []
        by_iid: dict[str, dict[str, Any]] = {}
        for row in s_rows:
            if isinstance(row, dict) and row.get("instance_id"):
                by_iid[str(row["instance_id"])] = row
        out: list[dict[str, Any]] = []
        for row_index, patch_row in enumerate(p_rows):
            if not isinstance(patch_row, dict):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": f"{path_key}[{row_index}]",
                        "expected_type": "FieldCell object",
                    },
                )
            iid = patch_row.get("instance_id")
            iid_s = str(iid) if iid else ""
            if iid_s and iid_s in by_iid:
                out.append(
                    self._merge_list_row_cell(
                        child_def,
                        by_iid[iid_s],
                        patch_row,
                        f"{path_key}[{row_index}]",
                    )
                )
            else:
                out.append(
                    self._merge_list_row_cell(
                        child_def, None, patch_row, f"{path_key}[{row_index}]"
                    )
                )
        return out

    def _merge_list_row_cell(
        self,
        child_def: Any,
        stored_row: dict[str, Any] | None,
        patch_row: dict[str, Any],
        path_key: str,
    ) -> dict[str, Any]:
        """Merge the list row cell."""
        self._enforce_patch_no_type_key(patch_row, path_key)
        self._require_instance_id_if_stored(stored_row, patch_row, path_key)
        iid = patch_row.get("instance_id") or (stored_row or {}).get("instance_id")
        if not iid:
            iid = self._new_instance_id()
        iid = str(iid)
        row: dict[str, Any] = {"field_id": str(child_def.id), "instance_id": iid}
        child_field_type = FieldType(child_def.field_type)
        which = self._assert_exactly_one_discriminator(patch_row, path_key)
        if which == "value":
            row["value"] = patch_row["value"]
            return row
        if which == "sub_fields":
            if child_field_type != FieldType.OBJECT:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_discriminator",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": path_key},
                )
            row["sub_fields"] = self._merge_object_sub_fields(
                child_def,
                (stored_row or {}).get("sub_fields"),
                patch_row["sub_fields"],
                path_key,
            )
            return row
        if child_field_type != FieldType.LIST:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key},
            )
        row["items"] = self._merge_list_items(
            child_def,
            (stored_row or {}).get("items"),
            patch_row["items"],
            path_key,
        )
        return row

    def _merge_child_cell_scalar(
        self,
        stored_d: dict[str, Any] | None,
        patch: dict[str, Any],
        path_key: str,
        fid: str,
        iid: str,
        which: str,
    ) -> dict[str, Any]:
        """Merge the child cell for scalar part."""
        if which != "value":
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key},
            )
        if "value" not in patch:
            val = (stored_d or {}).get("value") if stored_d else None
        else:
            val = patch["value"]
        return {"field_id": fid, "instance_id": iid, "value": val}

    def _merge_child_cell_object(
        self,
        sub_def: Any,
        stored_d: dict[str, Any] | None,
        patch: dict[str, Any],
        path_key: str,
        fid: str,
        iid: str,
        which: str,
    ) -> dict[str, Any]:
        """Merge the child cell for object part."""
        if which in {"value", "items"}:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key},
            )
        if "sub_fields" not in patch:
            inner = copy.deepcopy((stored_d or {}).get("sub_fields") or [])
        else:
            inner = self._merge_object_sub_fields(
                sub_def,
                (stored_d or {}).get("sub_fields") if stored_d else None,
                patch["sub_fields"],
                path_key,
            )
        return {"field_id": fid, "instance_id": iid, "sub_fields": inner}

    def _merge_child_cell_list(
        self,
        sub_def: Any,
        stored_d: dict[str, Any] | None,
        patch: dict[str, Any],
        path_key: str,
        fid: str,
        iid: str,
        which: str,
    ) -> dict[str, Any]:
        """Merge the child cell for list part."""
        if which != "items":
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key},
            )
        if "items" not in patch:
            inner = copy.deepcopy((stored_d or {}).get("items") or [])
        else:
            inner = self._merge_list_items(
                sub_def,
                (stored_d or {}).get("items") if stored_d else None,
                patch["items"],
                path_key,
            )
        return {"field_id": fid, "instance_id": iid, "items": inner}

    def _merge_child_cell(
        self,
        sub_def: Any,
        stored: Any,
        patch: Any,
        path_key: str,
    ) -> dict[str, Any]:
        """Merge the child cell."""
        if not isinstance(patch, dict):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "expected_type": "object (FieldCell)"},
            )
        self._enforce_patch_no_type_key(patch, path_key)
        stored_d = stored if isinstance(stored, dict) else None
        self._require_instance_id_if_stored(stored_d, patch, path_key)
        iid = patch.get("instance_id") or (
            (stored_d or {}).get("instance_id") if stored_d else None
        )
        if not iid:
            iid = self._new_instance_id()
        iid = str(iid)
        fid = str(sub_def.id)
        sub_field_type = FieldType(sub_def.field_type)
        which = self._assert_exactly_one_discriminator(patch, path_key)
        if sub_field_type not in (FieldType.OBJECT, FieldType.LIST):
            return self._merge_child_cell_scalar(stored_d, patch, path_key, fid, iid, which)
        if sub_field_type == FieldType.OBJECT:
            return self._merge_child_cell_object(
                sub_def,
                stored_d,
                patch,
                path_key,
                fid,
                iid,
                which,
            )
        return self._merge_child_cell_list(
            sub_def,
            stored_d,
            patch,
            path_key,
            fid,
            iid,
            which,
        )

    def _merge_root_cell_scalar(
        self,
        field_def: Any,
        stored: dict[str, Any],
        patch: dict[str, Any],
        out: dict[str, Any],
        which: str,
    ) -> dict[str, Any]:
        """Merge the child cell for scalar part."""
        if which != "value":
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_def.field_key},
            )
        if "value" not in patch:
            out["value"] = copy.deepcopy(stored.get("value"))
        else:
            out["value"] = patch["value"]
        return out

    def _merge_root_cell_object(
        self,
        field_def: Any,
        stored: dict[str, Any],
        patch: dict[str, Any],
        out: dict[str, Any],
        which: str,
    ) -> dict[str, Any]:
        """Merge the root cell for object part."""
        if which in {"value", "items"}:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_def.field_key},
            )
        if "sub_fields" not in patch:
            out["sub_fields"] = copy.deepcopy(stored.get("sub_fields") or [])
        else:
            out["sub_fields"] = self._merge_object_sub_fields(
                field_def, stored.get("sub_fields"), patch["sub_fields"], field_def.field_key
            )
        return out

    def _merge_root_cell_list(
        self,
        field_def: Any,
        stored: dict[str, Any],
        patch: dict[str, Any],
        out: dict[str, Any],
        which: str,
    ) -> dict[str, Any]:
        """Merge the root cell for list part."""
        if which != "items":
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_discriminator",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_def.field_key},
            )
        if "items" not in patch:
            out["items"] = copy.deepcopy(stored.get("items") or [])
        else:
            out["items"] = self._merge_list_items(
                field_def, stored.get("items"), patch["items"], field_def.field_key
            )
        return out

    def _merge_root_cell(
        self,
        field_def: Any,
        stored: dict[str, Any] | None,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge the root cell."""
        sid = str(field_def.id)
        self._enforce_patch_no_type_key(patch, field_def.field_key)
        if stored is None:
            merged_patch = copy.deepcopy(patch)
            if merged_patch.get("field_id") is None:
                merged_patch["field_id"] = sid
            if not merged_patch.get("instance_id"):
                merged_patch["instance_id"] = self._new_instance_id()
            return merged_patch
        self._require_instance_id_if_stored(stored, patch, field_def.field_key)
        iid = patch.get("instance_id") or stored.get("instance_id") or self._new_instance_id()
        out: dict[str, Any] = {"field_id": sid, "instance_id": str(iid)}
        root_field_type = FieldType(field_def.field_type)
        which = self._assert_exactly_one_discriminator(patch, field_def.field_key)
        if root_field_type not in (FieldType.OBJECT, FieldType.LIST):
            return self._merge_root_cell_scalar(field_def, stored, patch, out, which)
        if root_field_type == FieldType.OBJECT:
            return self._merge_root_cell_object(field_def, stored, patch, out, which)
        return self._merge_root_cell_list(field_def, stored, patch, out, which)

    def _field_cell_resolve_identity(
        self,
        field_def: Any,
        cell: Any,
        path_key: str,
        effective_reconcile: bool,
        role: Literal["root", "list_item", "object_child"],
    ) -> dict[str, Any] | tuple[str, str]:
        """Normalize FieldCell identity; return nulled dict or (instance_id, field_id)."""
        if not isinstance(cell, dict):
            if effective_reconcile and not field_def.is_required:
                return self._nulled_cell_for_optional_reconcile({}, field_def)
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": path_key, "expected_type": "object (FieldCell)"},
            )
        instance_id = str(cell.get("instance_id") or self._new_instance_id())
        fid = cell.get("field_id")
        if fid is not None and str(fid) == str(field_def.id):
            return (instance_id, str(field_def.id))
        if effective_reconcile and not field_def.is_required:
            return self._nulled_cell_for_optional_reconcile(cell, field_def)
        expected_labels = {
            "root": f"root FieldCell with field_id={field_def.id}",
            "list_item": f"list row FieldCell with field_id={field_def.id}",
            "object_child": f"object child FieldCell with field_id={field_def.id}",
        }
        raise ValidationException(
            message_key="custom_fields.errors.custom_field_invalid_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={
                "field_key": path_key,
                "expected_type": expected_labels[role],
            },
        )

    def _reconcile_or_raise_discriminator(
        self,
        path_key: str,
        field_def: Any,
        cell: dict[str, Any],
        effective_reconcile: bool,
    ) -> dict[str, Any]:
        """Reconcile or raise discriminator."""
        exc = ValidationException(
            message_key="custom_fields.errors.custom_field_discriminator",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"field_key": path_key},
        )
        return self._nulled_on_optional_reconcile_or_raise(
            exc,
            for_reconcile=effective_reconcile,
            field_def=field_def,
            stored_cell=cell,
        )

    def _reconcile_or_raise_invalid_type(
        self,
        path_key: str,
        field_def: Any,
        cell: dict[str, Any],
        effective_reconcile: bool,
        *,
        expected_type: str,
    ) -> dict[str, Any]:
        """Reconcile or raise invalid type."""
        exc = ValidationException(
            message_key="custom_fields.errors.custom_field_invalid_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"field_key": path_key, "expected_type": expected_type},
        )
        return self._nulled_on_optional_reconcile_or_raise(
            exc,
            for_reconcile=effective_reconcile,
            field_def=field_def,
            stored_cell=cell,
        )

    def _validate_field_cell_scalar_part(
        self,
        field_def: Any,
        cell: dict[str, Any],
        path_key: str,
        effective_reconcile: bool,
        *,
        which: str,
        instance_id: str,
        out_fid: str,
        type_snap: str,
    ) -> dict[str, Any]:
        """Validate the field cell for scalar part."""
        if which != "value":
            return self._reconcile_or_raise_discriminator(
                path_key, field_def, cell, effective_reconcile
            )
        if effective_reconcile and not field_def.is_required:
            if self._scalar_value_stale_against_def(cell, field_def):
                return {
                    "field_id": out_fid,
                    "instance_id": instance_id,
                    "type": type_snap,
                    "value": None,
                }
        raw_val = cell.get("value")
        try:
            coerced = self._coerce_field_value(path_key, raw_val, field_def)
        except ValidationException:
            if effective_reconcile and not field_def.is_required:
                coerced = None
            else:
                raise
        return {
            "field_id": out_fid,
            "instance_id": instance_id,
            "type": type_snap,
            "value": coerced,
        }

    def _object_sub_field_present_cell(
        self,
        work_map: dict[str, Any],
        sid_key: str,
        sub_def: Any,
    ) -> Any:
        """Return stored child cell to validate, or None if omitted and optional."""
        if sid_key not in work_map:
            if sub_def.is_required:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": sub_def.field_key},
                )
            return None
        child_cell = work_map[sid_key]
        if child_cell is None:
            if sub_def.is_required:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": sub_def.field_key},
                )
            return None
        return child_cell

    def _validate_field_cell_object_child(
        self,
        sub_def: Any,
        child_cell: Any,
        for_reconcile: bool,
        explicit_instance_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        """Validate the field cell for object child."""
        child_iid = str(child_cell.get("instance_id") or "") if isinstance(child_cell, dict) else ""
        child_effective = self._effective_reconcile(for_reconcile, child_iid, explicit_instance_ids)
        try:
            return self._validate_field_cell(
                sub_def,
                child_cell,
                path_key=sub_def.field_key,
                for_reconcile=for_reconcile,
                role="object_child",
                explicit_instance_ids=explicit_instance_ids,
            )
        except ValidationException as exc:
            return self._nulled_on_optional_reconcile_or_raise(
                exc,
                for_reconcile=child_effective,
                field_def=sub_def,
                stored_cell=child_cell,
            )

    def _object_sub_fields_work_map(
        self,
        field_def: Any,
        cell: dict[str, Any],
        path_key: str,
        effective_reconcile: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, Any]:
        """Build the object sub fields work map."""
        raw = cell.get("sub_fields")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return self._reconcile_or_raise_invalid_type(
                path_key,
                field_def,
                cell,
                effective_reconcile,
                expected_type="array (sub_fields)",
            )
        work_map = self._index_sub_field_cells(raw, f"{path_key}.sub_fields")
        sub_id_to_def = {
            str(sub_field_def.id): sub_field_def for sub_field_def in (field_def.sub_fields or [])
        }
        if not effective_reconcile:
            unknown = set(work_map) - set(sub_id_to_def)
            if unknown:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_unknown_keys",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": path_key, "unknown_keys": sorted(unknown)},
                )
        else:
            work_map = {
                sub_field_id: cell_data
                for sub_field_id, cell_data in work_map.items()
                if sub_field_id in sub_id_to_def
            }
        return work_map, sub_id_to_def

    def _validated_object_sub_fields_list(
        self,
        work_map: dict[str, Any],
        sub_id_to_def: dict[str, Any],
        for_reconcile: bool,
        explicit_instance_ids: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        """Validate the object sub fields list."""
        out_list: list[dict[str, Any]] = []
        for sid_key in sorted(
            sub_id_to_def,
            key=lambda sub_id: sub_id_to_def[sub_id].sort_order,
        ):
            sub_def = sub_id_to_def[sid_key]
            child_cell = self._object_sub_field_present_cell(work_map, sid_key, sub_def)
            if child_cell is None:
                continue
            out_list.append(
                self._validate_field_cell_object_child(
                    sub_def, child_cell, for_reconcile, explicit_instance_ids
                )
            )
        return out_list

    def _validate_field_cell_object_part(
        self,
        field_def: Any,
        cell: dict[str, Any],
        path_key: str,
        effective_reconcile: bool,
        *,
        which: str,
        instance_id: str,
        out_fid: str,
        type_snap: str,
        for_reconcile: bool,
        explicit_instance_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        """Validate the field cell for object part."""
        if which != "sub_fields":
            return self._reconcile_or_raise_discriminator(
                path_key, field_def, cell, effective_reconcile
            )
        wm_or_early = self._object_sub_fields_work_map(
            field_def, cell, path_key, effective_reconcile
        )
        if isinstance(wm_or_early, dict):
            return wm_or_early
        work_map, sub_id_to_def = wm_or_early
        out_list = self._validated_object_sub_fields_list(
            work_map, sub_id_to_def, for_reconcile, explicit_instance_ids
        )
        return {
            "field_id": out_fid,
            "instance_id": instance_id,
            "type": type_snap,
            "sub_fields": out_list,
        }

    def _validate_field_cell_list_part(
        self,
        field_def: Any,
        cell: dict[str, Any],
        path_key: str,
        effective_reconcile: bool,
        *,
        which: str,
        instance_id: str,
        out_fid: str,
        type_snap: str,
        for_reconcile: bool,
        explicit_instance_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        """Validate the field cell for list part."""
        if which != "items":
            return self._reconcile_or_raise_discriminator(
                path_key, field_def, cell, effective_reconcile
            )
        raw = cell.get("items")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return self._reconcile_or_raise_invalid_type(
                path_key,
                field_def,
                cell,
                effective_reconcile,
                expected_type="array (items)",
            )
        if not field_def.sub_fields:
            inner: list[Any] | Any = raw
        else:
            child_def = field_def.sub_fields[0]
            seen_iids: set[str] = set()
            inner_list: list[dict[str, Any]] = []
            for list_index, row in enumerate(raw):
                validated_row = self._validate_field_cell(
                    child_def,
                    row,
                    path_key=f"{path_key}[{list_index}]",
                    for_reconcile=for_reconcile,
                    role="list_item",
                    explicit_instance_ids=explicit_instance_ids,
                )
                row_instance_id = validated_row["instance_id"]
                if row_instance_id in seen_iids:
                    raise ValidationException(
                        message_key="custom_fields.errors.custom_field_duplicate_instance_id",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                        params={"field_key": path_key, "instance_id": row_instance_id},
                    )
                seen_iids.add(row_instance_id)
                inner_list.append(validated_row)
            inner = inner_list
        return {
            "field_id": out_fid,
            "instance_id": instance_id,
            "type": type_snap,
            "items": inner,
        }

    def _validate_field_cell(
        self,
        field_def: Any,
        cell: Any,
        *,
        path_key: str,
        for_reconcile: bool,
        role: Literal["root", "list_item", "object_child"],
        explicit_instance_ids: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Validate the field cell."""
        raw_iid = str(cell.get("instance_id") or "") if isinstance(cell, dict) else ""
        effective_reconcile = self._effective_reconcile(
            for_reconcile, raw_iid, explicit_instance_ids
        )
        identity = self._field_cell_resolve_identity(
            field_def, cell, path_key, effective_reconcile, role
        )
        if isinstance(identity, dict):
            return identity
        instance_id, out_fid = identity
        type_snap = self._def_type_value(field_def)
        assert isinstance(cell, dict)
        cell_dict = cell
        cell_field_type = FieldType(field_def.field_type)
        try:
            which = self._assert_exactly_one_discriminator(cell_dict, path_key)
        except ValidationException as exc:
            return self._nulled_on_optional_reconcile_or_raise(
                exc,
                for_reconcile=effective_reconcile,
                field_def=field_def,
                stored_cell=cell_dict,
            )
        if cell_field_type not in (FieldType.OBJECT, FieldType.LIST):
            return self._validate_field_cell_scalar_part(
                field_def,
                cell_dict,
                path_key,
                effective_reconcile,
                which=which,
                instance_id=instance_id,
                out_fid=out_fid,
                type_snap=type_snap,
            )
        if cell_field_type == FieldType.OBJECT:
            return self._validate_field_cell_object_part(
                field_def,
                cell_dict,
                path_key,
                effective_reconcile,
                which=which,
                instance_id=instance_id,
                out_fid=out_fid,
                type_snap=type_snap,
                for_reconcile=for_reconcile,
                explicit_instance_ids=explicit_instance_ids,
            )
        return self._validate_field_cell_list_part(
            field_def,
            cell_dict,
            path_key,
            effective_reconcile,
            which=which,
            instance_id=instance_id,
            out_fid=out_fid,
            type_snap=type_snap,
            for_reconcile=for_reconcile,
            explicit_instance_ids=explicit_instance_ids,
        )

    @staticmethod
    def _typesense_facet_walk_object(field_def: Any, cell: dict[str, Any], walk: Any) -> None:
        """Walk the object for typesense facets."""
        sub_map = {
            str(sub_field_def.id): sub_field_def for sub_field_def in (field_def.sub_fields or [])
        }
        for sub_field_cell in cell.get("sub_fields") or []:
            if not isinstance(sub_field_cell, dict):
                continue
            matching_sub_def = sub_map.get(str(sub_field_cell.get("field_id") or ""))
            if matching_sub_def:
                walk(matching_sub_def, sub_field_cell)

    @staticmethod
    def _typesense_facet_walk_list(field_def: Any, cell: dict[str, Any], walk: Any) -> None:
        """Walk the list for typesense facets."""
        if not field_def.sub_fields:
            return
        list_item_definition = field_def.sub_fields[0]
        for row in cell.get("items") or []:
            if isinstance(row, dict):
                walk(list_item_definition, row)

    @staticmethod
    def field_cells_typesense_facets(
        roots: list[dict[str, Any]],
        id_to_def: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        """Field cells typesense facets."""
        keys: list[str] = []
        vals: list[str] = []

        def walk(field_def: Any, cell: dict[str, Any]) -> None:
            keys.append(field_def.field_key)
            facet_field_type = FieldType(field_def.field_type)
            if facet_field_type not in (FieldType.OBJECT, FieldType.LIST):
                raw_value = cell.get("value")
                if raw_value is not None:
                    vals.append(str(raw_value))
                return
            if facet_field_type == FieldType.OBJECT:
                CustomFieldService._typesense_facet_walk_object(field_def, cell, walk)
                return
            CustomFieldService._typesense_facet_walk_list(field_def, cell, walk)

        for root in roots:
            field_id_str = str(root.get("field_id") or "")
            root_definition = id_to_def.get(field_id_str)
            if root_definition:
                walk(root_definition, root)
        return keys, vals

    async def validate_for_create(
        self,
        custom_fields: list[dict[str, Any]] | None,
        entity_type: EntityType,
    ) -> list[dict[str, Any]]:
        """Validate for create."""
        roots = self._parse_roots_create_payload(custom_fields)
        field_definitions, _ = await self.get_custom_fields_list(entity_type)
        id_to_def = self._root_id_to_def(field_definitions)
        if not id_to_def:
            if roots:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_definitions_not_found",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"entity_type": entity_type.value},
                )
            return []
        payload_by_id = self._index_patch_roots(roots)
        unknown = set(payload_by_id) - set(id_to_def)
        if unknown:
            first_unknown_field_id = sorted(unknown)[0]
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_not_defined",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_id": first_unknown_field_id},
            )
        out_cells: list[dict[str, Any]] = []
        for field_def in sorted(id_to_def.values(), key=lambda definition: definition.sort_order):
            sid = str(field_def.id)
            if field_def.is_required:
                if sid not in payload_by_id:
                    raise ValidationException(
                        message_key="custom_fields.errors.custom_field_required",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                        params={"field_key": field_def.field_key},
                    )
                cell = payload_by_id[sid]
                if self._cell_explicit_null(cell):
                    raise ValidationException(
                        message_key="custom_fields.errors.custom_field_cannot_be_null",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                        params={"field_key": field_def.field_key},
                    )
                out_cells.append(
                    self._validate_field_cell(
                        field_def,
                        cell,
                        path_key=field_def.field_key,
                        for_reconcile=False,
                        role="root",
                    )
                )
            elif sid in payload_by_id:
                cell = payload_by_id[sid]
                if self._cell_explicit_null(cell):
                    continue
                out_cells.append(
                    self._validate_field_cell(
                        field_def,
                        cell,
                        path_key=field_def.field_key,
                        for_reconcile=False,
                        role="root",
                    )
                )
        return self._sort_root_cells(out_cells, field_definitions)

    def _scalar_value_stale_against_def(self, cell: dict[str, Any], field_def: Any) -> bool:
        """Check if the scalar value is stale against the field definition."""
        stored_type_str = self._stored_type_str(cell)
        if stored_type_str is not None and stored_type_str != self._def_type_value(field_def):
            return True
        if FieldType(field_def.field_type) != FieldType.DROPDOWN:
            return False
        raw = cell.get("value")
        if raw is None:
            return False
        options = (field_def.type_config or {}).get("options", [])
        return bool(options) and raw not in options

    def _nulled_cell_for_optional_reconcile(
        self, stored_cell: dict[str, Any], field_def: Any
    ) -> dict[str, Any]:
        """Null the cell for optional reconcile."""
        iid = str(stored_cell.get("instance_id") or self._new_instance_id())
        cur_t = self._def_type_value(field_def)
        fid = str(field_def.id)
        reconcile_field_type = FieldType(field_def.field_type)
        if reconcile_field_type == FieldType.OBJECT:
            return {"field_id": fid, "instance_id": iid, "type": cur_t, "sub_fields": []}
        if reconcile_field_type == FieldType.LIST:
            return {"field_id": fid, "instance_id": iid, "type": cur_t, "items": []}
        return {"field_id": fid, "instance_id": iid, "type": cur_t, "value": None}

    def _nulled_on_optional_reconcile_or_raise(
        self,
        exc: ValidationException,
        *,
        for_reconcile: bool,
        field_def: Any,
        stored_cell: Any,
    ) -> dict[str, Any]:
        """Replace invalid optional stored cells with null during reconcile;
        re-raise for required fields."""
        if for_reconcile and not field_def.is_required:
            return self._nulled_cell_for_optional_reconcile(
                stored_cell if isinstance(stored_cell, dict) else {},
                field_def,
            )
        raise exc

    def _merge_for_update_append_stored_only(
        self,
        field_def: Any,
        stored_cell: dict[str, Any] | None,
        out_cells: list[dict[str, Any]],
        *,
        explicit_instance_ids: frozenset[str] | None = None,
    ) -> None:
        """Merge for update append stored only."""
        if field_def.is_required:
            if not stored_cell:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_def.field_key},
                )
            try:
                out_cells.append(
                    self._validate_field_cell(
                        field_def,
                        stored_cell,
                        path_key=field_def.field_key,
                        for_reconcile=False,
                        role="root",
                        explicit_instance_ids=explicit_instance_ids,
                    )
                )
            except ValidationException:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={
                        "field_key": field_def.field_key,
                        "expected_type": "value matching the field definition",
                    },
                ) from None
            return
        if not stored_cell:
            return
        if self._scalar_value_stale_against_def(stored_cell, field_def):
            out_cells.append(self._nulled_cell_for_optional_reconcile(stored_cell, field_def))
            return
        out_cells.append(
            self._validate_field_cell(
                field_def,
                stored_cell,
                path_key=field_def.field_key,
                for_reconcile=True,
                role="root",
                explicit_instance_ids=explicit_instance_ids,
            )
        )

    # pylint: disable=too-complex
    async def merge_for_update(
        self,
        payload: list[dict[str, Any]] | None,
        stored: Any,
        entity_type: EntityType,
    ) -> list[dict[str, Any]]:
        """Merge for update."""
        stored_roots = self._parse_roots_storage(stored)
        field_definitions, _ = await self.get_custom_fields_list(entity_type)
        if not field_definitions:
            return stored_roots
        id_to_def = self._root_id_to_def(field_definitions)
        flat_defs = self._flatten_field_definitions(list(id_to_def.values()))
        stored_by_id = self._index_stored_roots(stored_roots)
        patch_roots = self._parse_patch_roots_payload(payload) if payload is not None else None
        normal_patch_roots: list[dict[str, Any]] | None = None
        shortcut_patches: list[dict[str, Any]] = []
        if patch_roots is not None:
            normal_patch_roots, shortcut_patches = self._partition_custom_field_patch_entries(
                patch_roots, id_to_def
            )
            if shortcut_patches:
                stored_roots = copy.deepcopy(stored_roots)
                for shortcut_patch in shortcut_patches:
                    self._apply_instance_id_shortcut_patch(stored_roots, shortcut_patch, flat_defs)
                stored_by_id = self._index_stored_roots(stored_roots)
        else:
            normal_patch_roots = None

        shortcut_explicit_ids: frozenset[str] | None = (
            self._shortcut_target_instance_ids(shortcut_patches) if shortcut_patches else None
        )
        # Any root that is carried forward from storage may contain shortcut-mutated cells.
        append_stored_explicit_ids: frozenset[str] | None = (
            shortcut_explicit_ids if payload is not None and shortcut_explicit_ids else None
        )

        patch_by_id = (
            self._index_patch_roots(normal_patch_roots)
            if normal_patch_roots is not None and len(normal_patch_roots) > 0
            else None
        )

        if patch_by_id is not None:
            unknown = set(patch_by_id) - set(id_to_def)
            if unknown:
                first_unknown_field_id = sorted(unknown)[0]
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_not_defined",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_id": first_unknown_field_id},
                )

        out_cells: list[dict[str, Any]] = []
        for field_def in sorted(id_to_def.values(), key=lambda definition: definition.sort_order):
            sid = str(field_def.id)
            stored_cell = stored_by_id.get(sid)
            patch_cell = patch_by_id.get(sid) if patch_by_id is not None else None

            if patch_by_id is None:
                self._merge_for_update_append_stored_only(
                    field_def,
                    stored_cell,
                    out_cells,
                    explicit_instance_ids=append_stored_explicit_ids,
                )
                continue

            if patch_cell is None:
                self._merge_for_update_append_stored_only(
                    field_def,
                    stored_cell,
                    out_cells,
                    explicit_instance_ids=append_stored_explicit_ids,
                )
                continue

            if field_def.is_required and self._cell_explicit_null(patch_cell):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_cannot_be_null",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_def.field_key},
                )
            if not field_def.is_required and self._cell_explicit_null(patch_cell):
                continue

            merged = self._merge_root_cell(field_def, stored_cell, patch_cell)
            out_cells.append(
                self._validate_field_cell(
                    field_def,
                    merged,
                    path_key=field_def.field_key,
                    for_reconcile=False,
                    role="root",
                )
            )
        return self._sort_root_cells(out_cells, field_definitions)

    async def validate_and_format_custom_fields(
        self,
        custom_fields: list[dict[str, Any]],
        entity_type: EntityType,
        required_custom_fields_for_presence: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate and format custom fields."""
        if not custom_fields:
            if required_custom_fields_for_presence is not None:
                field_definitions, _ = await self.get_custom_fields_list(entity_type)
                self._validate_required_fields(
                    self._root_id_to_def(field_definitions),
                    required_custom_fields_for_presence,
                )
            return []
        return await self.validate_for_create(custom_fields, entity_type)

    async def reconcile_stored_custom_fields_for_write(
        self,
        stored: Any,
        entity_type: EntityType,
    ) -> list[dict[str, Any]]:
        """Reconcile stored custom fields for write."""
        return await self.merge_for_update(None, stored, entity_type)

    def resolve_fields_for_read(
        self,
        stored: Any,
        id_to_def: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Resolve fields for read."""
        roots = [
            root_cell
            for root_cell in self._parse_roots_storage(stored)
            if str(root_cell.get("field_id") or "") in id_to_def
        ]
        defs_by_id = {str(definition.id): definition for definition in id_to_def.values()}
        roots_sorted = sorted(
            roots,
            key=lambda root_cell: defs_by_id[str(root_cell["field_id"])].sort_order,
        )
        return [
            self._resolve_node_read(root_cell, defs_by_id[str(root_cell["field_id"])])
            for root_cell in roots_sorted
        ]

    def _stale_scalar_entry(
        self,
        field_def: Any,
        cell: dict[str, Any],
        *,
        old_type: str,
        old_value: Any,
    ) -> dict[str, Any]:
        """Stale scalar entry."""
        return {
            "field_id": str(field_def.id),
            "instance_id": str(cell.get("instance_id") or self._new_instance_id()),
            "type": self._def_type_value(field_def),
            "field_key": field_def.field_key,
            "label": field_def.field_name,
            "value": None,
            "_stale": True,
            "old_type": old_type,
            "old_value": old_value,
        }

    def _resolve_node_read_type_mismatch(
        self,
        field_def: Any,
        cell: dict[str, Any],
        base_meta: dict[str, Any],
        stored_t: str,
        field_type: FieldType,
    ) -> dict[str, Any]:
        """Resolve the node read for type mismatch."""
        if field_type == FieldType.OBJECT:
            return {
                **base_meta,
                "sub_fields": [],
                "_stale": True,
                "old_type": stored_t,
                "old_value": cell.get("sub_fields"),
            }
        if field_type == FieldType.LIST:
            return {
                **base_meta,
                "items": [],
                "_stale": True,
                "old_type": stored_t,
                "old_value": cell.get("items"),
            }
        return self._stale_scalar_entry(
            field_def, cell, old_type=stored_t, old_value=cell.get("value")
        )

    def _resolve_node_read_scalar(
        self,
        field_def: Any,
        cell: dict[str, Any],
        base_meta: dict[str, Any],
        stored_t: str | None,
        cur_t: str,
    ) -> dict[str, Any]:
        """Resolve the node read for scalar value."""
        raw_val = cell.get("value")
        if FieldType(field_def.field_type) == FieldType.DROPDOWN and raw_val is not None:
            options = (field_def.type_config or {}).get("options", [])
            if options and raw_val not in options:
                return self._stale_scalar_entry(
                    field_def,
                    cell,
                    old_type=cur_t,
                    old_value=raw_val,
                )
        try:
            coerced = self._coerce_field_value(field_def.field_key, raw_val, field_def)
            return {**base_meta, "value": coerced}
        except (ValidationException, ValueError, TypeError, AttributeError):
            type_config_dict = field_def.type_config or {}
            return {
                **base_meta,
                "value": type_config_dict.get("default_value"),
                "_stale": True,
                "old_type": stored_t or cur_t,
                "old_value": raw_val,
            }

    def _resolve_node_read_object_children(
        self,
        field_def: Any,
        cell: dict[str, Any],
        base_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve the node read for object children."""
        subs = cell.get("sub_fields")
        if not isinstance(subs, list):
            return {**base_meta, "sub_fields": []}
        sub_id_to_def = {
            str(sub_field_def.id): sub_field_def for sub_field_def in (field_def.sub_fields or [])
        }
        by_id: dict[str, dict[str, Any]] = {}
        for sub_cell in subs:
            if isinstance(sub_cell, dict) and sub_cell.get("field_id") is not None:
                by_id[str(sub_cell["field_id"])] = sub_cell
        ordered: list[dict[str, Any]] = []
        for sub_field_id in sorted(
            sub_id_to_def,
            key=lambda sid_key: sub_id_to_def[sid_key].sort_order,
        ):
            sub_definition = sub_id_to_def[sub_field_id]
            stored_sub_cell = by_id.get(sub_field_id)
            if stored_sub_cell is None:
                continue
            ordered.append(self._resolve_node_read(stored_sub_cell, sub_definition))
        return {**base_meta, "sub_fields": ordered}

    def _resolve_node_read_list_children(
        self,
        field_def: Any,
        cell: dict[str, Any],
        base_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve the node read for list children."""
        items = cell.get("items")
        if not isinstance(items, list) or not field_def.sub_fields:
            return {**base_meta, "items": []}
        list_row_definition = field_def.sub_fields[0]
        out_items: list[dict[str, Any]] = []
        for row in items:
            if isinstance(row, dict) and str(row.get("field_id") or "") == str(
                list_row_definition.id
            ):
                out_items.append(self._resolve_node_read(row, list_row_definition))
        return {**base_meta, "items": out_items}

    def _resolve_node_read(self, cell: dict[str, Any], field_def: Any) -> dict[str, Any]:
        """Resolve the node read."""
        cur_t = self._def_type_value(field_def)
        stored_t = self._stored_type_str(cell)
        read_field_type = FieldType(field_def.field_type)
        base_meta = {
            "field_id": str(field_def.id),
            "instance_id": str(cell.get("instance_id") or self._new_instance_id()),
            "type": cur_t,
            "field_key": field_def.field_key,
            "label": field_def.field_name,
        }
        if stored_t is not None and stored_t != cur_t:
            return self._resolve_node_read_type_mismatch(
                field_def, cell, base_meta, stored_t, read_field_type
            )
        if read_field_type not in (FieldType.OBJECT, FieldType.LIST):
            return self._resolve_node_read_scalar(field_def, cell, base_meta, stored_t, cur_t)
        if read_field_type == FieldType.OBJECT:
            return self._resolve_node_read_object_children(field_def, cell, base_meta)
        return self._resolve_node_read_list_children(field_def, cell, base_meta)

    def _validate_string_field(self, field_key: str, field_value: Any) -> str:
        """Validate a string field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        return field_value

    @staticmethod
    def _strict_json_number_to_float(field_key: str, field_value: Any) -> float:
        """Coerce only JSON number primitives (int/float). Reject str, bool, etc."""
        if isinstance(field_value, bool):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "number"},
            )
        if isinstance(field_value, int):
            return float(field_value)
        if isinstance(field_value, float):
            return field_value
        raise ValidationException(
            message_key="custom_fields.errors.custom_field_invalid_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"field_key": field_key, "expected_type": "number"},
        )

    def _validate_number_field(self, field_key: str, field_value: Any) -> float:
        """Validate a number field value."""
        return self._strict_json_number_to_float(field_key, field_value)

    def _validate_yes_no_field(self, field_key: str, field_value: Any) -> bool:
        """Validate a yes/no (boolean) field value."""
        if not isinstance(field_value, bool):
            if isinstance(field_value, str):
                return field_value.lower() in ("true", "yes", "1")
            if isinstance(field_value, int):
                return bool(field_value)
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "boolean"},
            )
        return field_value

    def _validate_url_field(self, field_key: str, field_value: Any) -> str:
        """Validate a URL field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        if not (field_value.startswith("http://") or field_value.startswith("https://")):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        return field_value

    def _validate_dropdown_field(self, field_key: str, field_value: Any, field_def: Any) -> str:
        """Validate a dropdown field value."""
        if not isinstance(field_value, str):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "string"},
            )
        type_config = field_def.type_config or {}
        options = type_config.get("options", [])
        if options and field_value not in options:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_option",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "value": field_value},
            )
        return field_value

    def _validate_range_slider_field(
        self, field_key: str, field_value: Any, field_def: Any
    ) -> float:
        """Validate a range slider field value."""
        field_value = self._strict_json_number_to_float(field_key, field_value)
        type_config = field_def.type_config or {}
        min_val = type_config.get("min", 0)
        max_val = type_config.get("max", 100)
        if not min_val <= field_value <= max_val:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_out_of_range",
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
                message_key="custom_fields.errors.custom_field_invalid_currency_format",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        amount = field_value.get("amount")
        currency_code = field_value.get("currency_code")
        if amount is None or currency_code is None:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_currency_format",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key},
            )
        type_config = field_def.type_config or {}
        allowed_currencies = type_config.get("allowed_currencies", [])
        if allowed_currencies and currency_code not in allowed_currencies:
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_currency",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "currency": currency_code},
            )
        return {"amount": float(amount), "currency_code": str(currency_code)}

    def _validate_file_or_image_field(
        self,
        field_key: str,
        field_value: Any,
        field_def: Any,
    ) -> Any:
        """Validate file_upload or image field value (same rules)."""
        type_config = field_def.type_config or {}
        allow_multiple = type_config.get("allow_multiple", False)
        max_files = type_config.get("max_files", 1)

        if allow_multiple:
            if not isinstance(field_value, list):
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_invalid_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "expected_type": "array"},
                )
            if len(field_value) > max_files:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key, "max": max_files},
                )
            return field_value

        if isinstance(field_value, list):
            if len(field_value) > 1:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_too_many_files",
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
                message_key="custom_fields.errors.custom_field_invalid_type",
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

    def _coerce_field_value(
        self,
        field_key: str,
        field_value: Any,
        field_def: Any,
    ) -> Any:
        """Coerce a scalar (non-object, non-list) logical value for the field definition."""
        field_type = FieldType(field_def.field_type)

        if field_value is None:
            if field_def.is_required:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_key},
                )
            return None

        if field_type in (FieldType.OBJECT, FieldType.LIST):
            raise ValidationException(
                message_key="custom_fields.errors.custom_field_invalid_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field_key": field_key, "expected_type": "scalar"},
            )

        validators = {
            FieldType.TEXT: lambda field_key, field_value, _field_def: self._validate_string_field(
                field_key, field_value
            ),
            FieldType.LONG_TEXT: lambda field_key,
            field_value,
            _field_def: self._validate_string_field(field_key, field_value),
            FieldType.RICH_TEXT: lambda field_key,
            field_value,
            _field_def: self._validate_string_field(field_key, field_value),
            FieldType.NUMBER: lambda field_key,
            field_value,
            _field_def: self._validate_number_field(field_key, field_value),
            FieldType.DATE: lambda field_key, field_value, _field_def: self._validate_string_field(
                field_key, field_value
            ),
            FieldType.YES_NO: lambda field_key,
            field_value,
            _field_def: self._validate_yes_no_field(field_key, field_value),
            FieldType.URL: lambda field_key, field_value, _field_def: self._validate_url_field(
                field_key, field_value
            ),
            FieldType.DROPDOWN: self._validate_dropdown_field,
            FieldType.RANGE_SLIDER: self._validate_range_slider_field,
            FieldType.CURRENCY: self._validate_currency_field,
            FieldType.FILE_UPLOAD: self._validate_file_or_image_field,
            FieldType.IMAGE: self._validate_file_or_image_field,
            FieldType.ADDRESS: self._validate_address_field,
        }

        validator = validators.get(field_type)
        if validator:
            return validator(field_key, field_value, field_def)

        # Unknown field type - not allowed
        raise ValidationException(
            message_key="custom_fields.errors.custom_field_invalid_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"field_key": field_key, "expected_type": "supported field type"},
        )

    def _validate_required_fields(
        self,
        id_to_def: dict[str, Any],
        presence: dict[str, Any],
    ) -> None:
        """Ensure every required root field id is present in ``presence``."""
        for field_id, field_def in id_to_def.items():
            if field_def.is_required and field_id not in presence:
                raise ValidationException(
                    message_key="custom_fields.errors.custom_field_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"field_key": field_def.field_key},
                )
