"""Custom Field Database Repository Module - AsyncPG Implementation

This module contains all custom field-related database operations using asyncpg.
All SQL queries for custom field management are centralized here.
"""

import json
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import EntityType
from libs.shared_utils.logger import get_logger

logger = get_logger("custom_field_repository")


class CustomFieldRepository:
    """Database operations class for custom field management using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    async def create_custom_field(self, field_data: dict) -> dict:
        """Create a new custom field definition.

        Args:
            field_data: Dictionary containing field data:
                - organization_id (required)
                - entity_type (required for top-level)
                - parent_id (required for sub-fields)
                - field_name (required)
                - field_key (required)
                - description (optional)
                - field_type (required)
                - show_on_create (optional, default True)
                - show_on_detail (optional, default False)
                - is_required (optional, default False)
                - type_config (optional, default {})
                - sort_order (optional, default 0)
                - is_active (optional, default True)
                - created_by (optional)

        Returns:
            dict: Created custom field record

        Raises:
            ValueError: If required fields are missing
        """
        # Validate required fields
        organization_id = field_data.get("organization_id")
        field_name = field_data.get("field_name")
        field_key = field_data.get("field_key")
        field_type = field_data.get("field_type")

        # Serialize type_config to JSON string
        type_config = field_data.get("type_config", {})
        type_config_json = json.dumps(type_config)

        # Build dynamic query
        fields = [
            "organization_id",
            "field_name",
            "field_key",
            "field_type",
            "type_config",
        ]
        placeholders = ["$1", "$2", "$3", "$4", "$5"]
        values = [
            organization_id,
            field_name,
            field_key,
            field_type,
            type_config_json,
        ]
        param_index = 6

        # Optional fields
        optional_fields = [
            "entity_type",
            "parent_id",
            "description",
            "show_on_create",
            "show_on_detail",
            "is_required",
            "sort_order",
            "is_active",
            "created_by",
        ]

        for field_name_key in optional_fields:
            if field_name_key in field_data and field_data[field_name_key] is not None:
                fields.append(field_name_key)
                placeholders.append(f"${param_index}")
                values.append(field_data[field_name_key])
                param_index += 1

        query = f"""
            INSERT INTO custom_fields ({", ".join(fields)})
            VALUES ({", ".join(placeholders)})
            RETURNING id
        """

        row = await self.db_connection.fetchrow(query, *values)
        return {"id": row["id"]}

    async def get_custom_field_with_descendants(
        self, field_id: str, organization_id: str
    ) -> list[dict]:
        """Get custom field and all its descendants by parent_id (subtree).

        Uses a recursive CTE to fetch the field and every descendant in one query.
        Caller can build the tree from the flat list using parent_id.

        Args:
            field_id: Custom field ID (root of subtree)
            organization_id: Organization ID

        Returns:
            list[dict]: Flat list: the field row plus all descendant rows
        """
        query = """
            WITH RECURSIVE subtree AS (
                SELECT * FROM custom_fields
                WHERE id = $1::uuid AND organization_id = $2
                UNION ALL
                SELECT c.* FROM custom_fields c
                INNER JOIN subtree s ON c.parent_id = s.id
                WHERE c.organization_id = $2
            )
            SELECT * FROM subtree ORDER BY parent_id NULLS FIRST, sort_order ASC
        """
        rows = await self.db_connection.fetch(query, field_id, organization_id)
        return [dict(row) for row in rows]

    async def get_custom_fields_by_entity_type(
        self,
        organization_id: str,
        entity_type: EntityType,
    ) -> list[dict]:
        """Get all custom fields for an organization in one query.

        Caller filters by entity_type and builds tree in memory.

        Args:
            organization_id: Organization ID
            entity_type: Entity type
        Returns:
            list[dict]: Flat list of custom field records
        """
        query = """
            SELECT *
            FROM custom_fields
            WHERE organization_id = $1
            AND is_active = TRUE
            AND entity_type = $2
            ORDER BY parent_id NULLS FIRST, sort_order ASC
        """
        rows = await self.db_connection.fetch(query, organization_id, entity_type.value)
        return [dict(row) for row in rows]

    async def check_field_key_exists(
        self, organization_id: str, entity_type: str, field_key: str
    ) -> bool:
        """Check if field_key already exists for entity type.

        Args:
            organization_id: Organization ID
            entity_type: Entity type
            field_key: Field key to check

        Returns:
            bool: True if exists, False otherwise
        """
        query = """
            SELECT EXISTS(
                SELECT 1
                FROM custom_fields
                WHERE organization_id = $1
                    AND entity_type = $2
                    AND parent_id IS NULL
                    AND field_key = $3
                    AND is_active = TRUE
            )
        """
        row = await self.db_connection.fetchrow(query, organization_id, entity_type, field_key)
        return row["exists"] if row else False

    async def bulk_create_custom_fields(self, fields_data: list[dict]) -> list[str]:
        """Bulk create multiple custom field definitions.

        Args:
            fields_data: List of dictionaries, each containing field data:
                - organization_id (required)
                - entity_type (required for top-level)
                - parent_id (required for sub-fields)
                - field_name (required)
                - field_key (required)
                - description (optional)
                - field_type (required)
                - show_on_create (optional, default True)
                - show_on_detail (optional, default False)
                - is_required (optional, default False)
                - type_config (optional, default {})
                - sort_order (optional, default 0)
                - is_active (optional, default True)
                - created_by (optional)

        Returns:
            list[str]: List of created custom field IDs (order matches fields_data).

        Raises:
            ValueError: If required fields are missing
        """
        if not fields_data:
            return []

        # Build bulk insert query
        all_fields = set()
        for field_data in fields_data:
            all_fields.update(field_data.keys())

        fields_list = sorted(all_fields)
        num_fields = len(fields_list)

        # Build VALUES clause with placeholders
        values_clauses = []
        all_values = []
        param_index = 1

        for field_data in fields_data:
            # Serialize type_config to JSON string
            type_config = field_data.get("type_config", {})
            if isinstance(type_config, dict):
                type_config_json = json.dumps(type_config)
            else:
                type_config_json = json.dumps(type_config)

            # Prepare values for this row
            row_values = []
            for field_name_key in fields_list:
                if field_name_key == "type_config":
                    row_values.append(type_config_json)
                elif field_name_key in field_data:
                    row_values.append(field_data[field_name_key])
                else:
                    # Use default values
                    defaults = {
                        "show_on_create": True,
                        "show_on_detail": False,
                        "is_required": False,
                        "type_config": "{}",
                        "sort_order": 0,
                        "is_active": True,
                    }
                    row_values.append(defaults.get(field_name_key, None))

            placeholders = [f"${param_index + i}" for i in range(num_fields)]
            values_clauses.append(f"({', '.join(placeholders)})")
            all_values.extend(row_values)
            param_index += num_fields

        query = f"""
            INSERT INTO custom_fields ({", ".join(fields_list)})
            VALUES {", ".join(values_clauses)}
            RETURNING id
        """

        rows = await self.db_connection.fetch(query, *all_values)
        return [str(row["id"]) for row in rows]

    async def update_custom_field(
        self, field_id: str, organization_id: str, update_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a custom field by ID. Only provided keys are updated.

        Args:
            field_id: Custom field ID
            organization_id: Organization ID
            update_data: Dictionary of fields to update

        Returns:
            Updated custom field or None if not found
        """
        if not update_data:
            return None

        # Serialize type_config to JSON string if present
        if "type_config" in update_data:
            type_config = update_data["type_config"]
            if isinstance(type_config, dict):
                update_data = {**update_data, "type_config": json.dumps(type_config)}

        set_parts = []
        values: list[Any] = []
        param_index = 1

        for key, value in update_data.items():
            if key == "type_config":
                set_parts.append(f"{key} = ${param_index}::jsonb")
            else:
                set_parts.append(f"{key} = ${param_index}")
            values.append(value)
            param_index += 1

        if not set_parts:
            return None

        set_parts.append("updated_at = NOW()")
        values.extend([field_id, organization_id])

        query = f"""
            UPDATE custom_fields
            SET {", ".join(set_parts)}
            WHERE id = ${param_index}::uuid AND organization_id = ${param_index + 1}::uuid
            AND is_active = TRUE
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else None

    async def bulk_update_custom_fields(
        self, organization_id: str, updates: list[dict[str, Any]]
    ) -> None:
        """Bulk update multiple custom fields efficiently using a single UPDATE...FROM VALUES query.

        Uses PostgreSQL's UPDATE...FROM VALUES syntax with COALESCE to handle different
        field sets per update in a single database round-trip.

        Args:
            organization_id: Organization ID
            updates: List of update dictionaries, each must contain 'id' and update fields

        Raises:
            ValueError: If any update dict is missing 'id'
        """
        if not updates:
            return

        # Validate all updates have 'id' before processing
        for update_data in updates:
            if "id" not in update_data:
                raise ValueError("Each update dict must contain 'id'")

        # Build VALUES clause with all updates
        # Each row: (id, organization_id, field_name, description, field_type, show_on_create,
        #            show_on_detail, is_required, type_config, sort_order, updated_by)
        values_clauses = []
        all_values = []
        param_index = 1

        field_order = [
            "field_name",
            "description",
            "field_type",
            "show_on_create",
            "show_on_detail",
            "is_required",
            "type_config",
            "sort_order",
            "updated_by",
        ]

        # Define type casts for each field
        type_casts = {
            "field_name": "text",
            "description": "text",
            "field_type": "text",
            "show_on_create": "boolean",
            "show_on_detail": "boolean",
            "is_required": "boolean",
            "type_config": "jsonb",
            "sort_order": "integer",
            "updated_by": "uuid",
        }

        for update_data in updates:
            field_id = update_data["id"]
            update_copy = {**update_data}
            update_copy.pop("id")

            # Serialize type_config to JSON string if present
            if "type_config" in update_copy and isinstance(update_copy["type_config"], dict):
                update_copy["type_config"] = json.dumps(update_copy["type_config"])

            # Build placeholders with CAST for each value: id, organization_id, then all fields
            placeholders = [f"CAST(${param_index} AS uuid)", f"CAST(${param_index + 1} AS uuid)"]
            row_values = [field_id, organization_id]

            for i, field in enumerate(field_order):
                cast_type = type_casts[field]
                placeholders.append(f"CAST(${param_index + 2 + i} AS {cast_type})")
                row_values.append(update_copy.get(field))

            all_values.extend(row_values)
            values_clauses.append(f"({', '.join(placeholders)})")
            param_index += len(row_values)

        # Build single UPDATE query with COALESCE to only update non-NULL values.
        # Column types are inferred from VALUES expressions (which use ::uuid, ::jsonb casts).
        query = f"""
            UPDATE custom_fields cf
            SET
                field_name = COALESCE(v.field_name, cf.field_name),
                description = COALESCE(v.description, cf.description),
                field_type = COALESCE(v.field_type, cf.field_type),
                show_on_create = COALESCE(v.show_on_create::boolean, cf.show_on_create),
                show_on_detail = COALESCE(v.show_on_detail::boolean, cf.show_on_detail),
                is_required = COALESCE(v.is_required::boolean, cf.is_required),
                type_config = COALESCE(v.type_config::jsonb, cf.type_config),
                sort_order = COALESCE(v.sort_order::integer, cf.sort_order),
                updated_by = COALESCE(v.updated_by, cf.updated_by),
                updated_at = NOW()
            FROM (VALUES {", ".join(values_clauses)}) AS v(
                id, organization_id, field_name, description, field_type,
                show_on_create, show_on_detail, is_required,
                type_config, sort_order, updated_by
            )
            WHERE cf.id = v.id
                AND cf.organization_id = v.organization_id
                AND cf.is_active = TRUE
        """

        await self.db_connection.execute(query, *all_values)

    async def bulk_delete_custom_fields_with_descendants(
        self, organization_id: str, field_ids: list[str]
    ) -> None:
        """Bulk delete custom fields and all their descendants.

        Uses a recursive CTE to find all descendants, then hard-deletes them.

        Args:
            organization_id: Organization ID.
            field_ids: List of field IDs to delete (each and all its descendants).
        """
        if not field_ids:
            return

        query = """
            WITH RECURSIVE to_delete AS (
                SELECT id
                FROM custom_fields
                WHERE id = ANY($1::uuid[])
                  AND organization_id = $2::uuid
                UNION ALL
                SELECT c.id
                FROM custom_fields c
                INNER JOIN to_delete d ON c.parent_id = d.id
                WHERE c.organization_id = $2::uuid
            )
            DELETE FROM custom_fields
            WHERE id IN (SELECT id FROM to_delete)
        """
        await self.db_connection.execute(query, field_ids, organization_id)
