"""Client Database Repository Module - AsyncPG Implementation

This module contains all client-related database operations using asyncpg.
All SQL queries for client management are centralized here with proper
transaction handling and efficient batch operations.
"""

import json
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import ClientStatus, ClientUserStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("client_repository")

# Columns that are JSONB in DB (object/dict or array of objects). Values are serialized
CLIENT_JSONB_COLUMNS = frozenset(
    {
        "websites",
        "billing_preferences",
        "custom_fields",
        "additional_data",
        "social_pages",
        "work_history",
        "educational_history",
        "linked_pages",
        "products",
        "key_people",
    }
)


class ClientRepository:
    """Database operations class for client management using asyncpg.
    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    @staticmethod
    def _serialize_jsonb_param(key: str, value: Any) -> Any:
        """Serialize JSONB column values to JSON string for asyncpg; pass others through."""
        if key in CLIENT_JSONB_COLUMNS and isinstance(value, (list, dict)):
            return json.dumps(value)
        return value

    # CREATE OPERATIONS
    async def create_client(self, client_data: dict) -> dict:
        """Create a new client record.

        Only includes fields that are explicitly provided in client_data.
        Database defaults are used for fields not provided (status='active',
        tags='{}', websites='[]', billing_preferences='{}', custom_fields='{}').

        Args:
            client_data: Dictionary containing client fields:
                - organization_id (required): Organization ID
                - client_type (required): Client type ('person' or 'company')
                - name (optional): Client name
                - industry (optional): Industry sector (for companies)
                - status (optional): Client status, defaults to 'active' in DB
                - profile_photo_url (optional): URL to profile photo
                - tags (optional): Array of tags, defaults to '{}' in DB
                - websites (optional): JSONB array of website objects, defaults to '[]' in DB
                - billing_preferences (optional): JSONB billing settings, defaults to '{}' in DB
                - custom_fields (optional):
                     JSONB custom field key-value pairs, defaults to '{}' in DB

        Returns:
            dict: Created client record

        Raises:
            ValueError: If required fields (organization_id, client_type) are missing
        """
        # Validate required fields
        organization_id = client_data.get("organization_id")
        client_type = client_data.get("client_type")

        if not organization_id or not client_type:
            raise ValueError("organization_id and client_type are required fields")

        # Build dynamic query - only include fields that are explicitly provided
        fields = []
        placeholders = []
        values = []
        param_index = 1

        # Required fields
        fields.append("organization_id")
        placeholders.append(f"${param_index}")
        values.append(organization_id)
        param_index += 1

        fields.append("client_type")
        placeholders.append(f"${param_index}")
        values.append(client_type)
        param_index += 1

        # Optional fields - only include if explicitly provided (not None)
        optional_field_mapping = [
            "name",
            "industry",
            "status",
            "profile_photo_url",
            "tags",
            "websites",
            "billing_preferences",
            "custom_fields",
            "portal_access",
            "additional_data",
            "social_pages",
        ]

        for field_name in optional_field_mapping:
            if field_name in client_data and client_data[field_name] is not None:
                fields.append(field_name)
                placeholders.append(f"${param_index}")
                values.append(client_data[field_name])
                param_index += 1

        # Build and execute query
        query = f"""
            INSERT INTO clients ({", ".join(fields)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)

    async def create_client_user(self, client_user_data: dict) -> dict:
        """Create a new client user record.

        Only includes fields that are explicitly provided in client_user_data.
        Database defaults are used for fields not provided (status='active').

        Args:
            client_user_data: Dictionary containing client user fields:
                - client_id (required): Client ID
                - organization_id (required): Organization ID
                - user_id (optional): User ID from auth.users (can be NULL)
                - prefix (optional): Name prefix (Mr., Mrs., Dr., etc.)
                - first_name (optional): First name
                - middle_name (optional): Middle name
                - last_name (optional): Last name
            - title (optional): Job title/position
            - date_of_birth (optional): Date of birth
            - profile_photo_url (optional): URL to profile photo
            - status (optional): Client user status, defaults to 'active' in DB
            - isometrik_user_id (required): Isometrik user ID
            - is_primary_contact (optional): Primary contact flag, defaults to False in DB
        Returns:
            dict: Created client user record

        Raises:
            ValueError: If required fields (client_id, organization_id) are missing
        """
        # Validate required fields
        client_id = client_user_data.get("client_id")
        organization_id = client_user_data.get("organization_id")
        isometrik_user_id = client_user_data.get("isometrik_user_id")

        # Build dynamic query - only include fields that are explicitly provided
        fields = []
        placeholders = []
        values = []
        param_index = 1

        # Required fields
        fields.append("client_id")
        placeholders.append(f"${param_index}")
        values.append(client_id)
        param_index += 1

        fields.append("organization_id")
        placeholders.append(f"${param_index}")
        values.append(organization_id)
        param_index += 1

        fields.append("isometrik_user_id")
        placeholders.append(f"${param_index}")
        values.append(isometrik_user_id)
        param_index += 1

        # Optional fields - only include if explicitly provided (not None)
        # Note: user_id can be None, so we check if key exists in dict
        optional_field_mapping = [
            "user_id",
            "prefix",
            "first_name",
            "middle_name",
            "last_name",
            "title",
            "date_of_birth",
            "profile_photo_url",
            "status",
            "is_primary_contact",
        ]

        for field_name in optional_field_mapping:
            if field_name in client_user_data and client_user_data[field_name] is not None:
                fields.append(field_name)
                placeholders.append(f"${param_index}")
                values.append(client_user_data[field_name])
                param_index += 1

        # Build and execute query
        query = f"""
            INSERT INTO client_users ({", ".join(fields)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)

    async def check_client_user_exists(self, user_id: str, organization_id: str) -> bool:
        """Check if a client user exists for a given user and organization.

        Args:
            user_id: User ID
            organization_id: Organization ID

        Returns:
            bool: True if client user exists, False otherwise
        """
        query = """
            SELECT EXISTS(
                SELECT 1 FROM client_users WHERE user_id = $1 AND organization_id = $2
            )
        """
        exists = await self.db_connection.fetchval(query, user_id, organization_id)
        return bool(exists)

    # READ OPERATIONS
    async def get_clients_list(
        self,
        organization_id: str,
        filter_params: dict,
    ) -> list[dict]:
        """Get paginated list of clients with filtering.

        Args:
            organization_id: Organization ID
            filter_params: Dictionary containing filter parameters:
                - search: Search term (searches in client name)
                - client_type: Filter by client type
                - status: Filter by status
                - limit: Page size
                - offset: Offset for pagination

        Returns:
            list[dict]: List of client records
        """
        search = filter_params.get("search")
        client_type = filter_params.get("client_type")
        status = filter_params.get("status")
        limit = filter_params.get("limit", 20)
        offset = filter_params.get("offset", 0)

        param_index = 2
        conditions = ["c.organization_id = $1", f"c.status != ${param_index}"]
        params = [organization_id, ClientStatus.DELETED.value]
        param_index = 3

        if search:
            conditions.append(f"c.name ILIKE ${param_index}")
            params.append(f"%{search}%")
            param_index += 1

        if client_type:
            conditions.append(f"c.client_type = ${param_index}")
            params.append(client_type)
            param_index += 1

        if status:
            conditions.append(f"c.status = ${param_index}")
            params.append(status)
            param_index += 1

        where_clause = " AND ".join(conditions)

        # Add join for primary contact
        deleted_client_user_status = ClientUserStatus.DELETED.value
        primary_contact_join = f"""
            LEFT JOIN client_users cu ON cu.client_id = c.id
                AND cu.is_primary_contact = true
                AND cu.status != '{deleted_client_user_status}'
            LEFT JOIN auth.users au ON au.id = cu.user_id
        """

        query = f"""
            SELECT
                c.id,
                c.client_type,
                c.name,
                c.status,
                c.tags,
                c.created_at,
                c.updated_at,
                cu.first_name,
                cu.last_name,
                cu.title,
                au.email,
                au.raw_user_meta_data->>'phone_isd_code' as phone_isd_code,
                au.raw_user_meta_data->>'phone_number' as phone
            FROM clients c
            {primary_contact_join}
            WHERE {where_clause}
            ORDER BY c.created_at DESC
            LIMIT ${param_index} OFFSET ${param_index + 1}
        """
        params.extend([limit, offset])

        rows = await self.db_connection.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_clients_count(
        self,
        organization_id: str,
        filter_params: dict,
    ) -> int:
        """Get total count of clients matching filters.

        Args:
            organization_id: Organization ID
            filter_params: Dictionary containing filter parameters:
                - search: Search term (searches in client name)
                - client_type: Filter by client type
                - status: Filter by status

        Returns:
            int: Total count
        """
        search = filter_params.get("search")
        client_type = filter_params.get("client_type")
        status = filter_params.get("status")

        param_index = 2
        conditions = ["c.organization_id = $1", f"c.status != ${param_index}"]
        params = [organization_id, ClientStatus.DELETED.value]
        param_index = 3

        if search:
            conditions.append(f"c.name ILIKE ${param_index}")
            params.append(f"%{search}%")
            param_index += 1

        if client_type:
            conditions.append(f"c.client_type = ${param_index}")
            params.append(client_type)
            param_index += 1

        if status:
            conditions.append(f"c.status = ${param_index}")
            params.append(status)
            param_index += 1

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT COUNT(*)::int
            FROM clients c
            WHERE {where_clause}
        """

        count = await self.db_connection.fetchval(query, *params)
        return count or 0

    # DELETE OPERATIONS
    async def delete_client(self, client_id: str, organization_id: str) -> bool:
        """Soft delete client and related records.

        Args:
            client_id: Client ID
            organization_id: Organization ID

        Returns:
            bool: True if deleted successfully

        Raises:
            NotFoundException: If client not found
        """
        # Soft delete client - check existence using RETURNING clause
        query = """
            UPDATE clients
            SET status = $3, updated_at = NOW()
            WHERE id = $1 AND organization_id = $2 AND status != $3
            RETURNING id
        """
        row = await self.db_connection.fetchrow(
            query, client_id, organization_id, ClientStatus.DELETED.value
        )

        # If no row was returned, client doesn't exist
        if not row:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        return True

    async def delete_client_users(self, client_id: str) -> bool:
        """Soft delete all client users for a client.

        Args:
            client_id: Client ID

        Returns:
            bool: True if deleted successfully
        """
        query = """
            UPDATE client_users
            SET status = $2, updated_at = NOW()
            WHERE client_id = $1 AND status != $2
        """
        await self.db_connection.execute(query, client_id, ClientUserStatus.DELETED.value)
        return True

    async def delete_leads(self, client_id: str) -> bool:
        """Hard delete lead for a client.

        Args:
            client_id: Client ID

        Returns:
            bool: True if deleted successfully
        """
        query = """
            DELETE FROM leads
            WHERE client_id = $1
        """
        await self.db_connection.execute(query, client_id)
        return True

    async def delete_addresses(self, client_id: str) -> bool:
        """Delete addresses for a client.

        Args:
            client_id: Client ID

        Returns:
            bool: True if deleted successfully
        """
        query = """
            DELETE FROM client_addresses
            WHERE client_id = $1
        """
        await self.db_connection.execute(query, client_id)
        return True

    async def delete_addresses_by_ids(self, client_id: str, address_ids: list[str]) -> None:
        """Delete addresses by ids for a client."""
        if not address_ids:
            return
        query = """
            DELETE FROM client_addresses
            WHERE client_id = $1 AND id = ANY($2::uuid[])
        """
        await self.db_connection.execute(query, client_id, address_ids)

    async def get_client_by_enrichment_request_id(self, enrichment_request_id: str) -> dict | None:
        """Fetch client id and organization_id by enrichment request id (webhook callback).

        Returns:
            dict | None: Row with id, organization_id or None if not found.
        """
        query = """
            SELECT id, organization_id
            FROM clients
            WHERE enrichment_request_id = $1 AND status != $2
        """
        row = await self.db_connection.fetchrow(
            query, enrichment_request_id, ClientStatus.DELETED.value
        )
        return dict(row) if row else None

    # UPDATE OPERATIONS
    async def get_client_for_update(
        self,
        client_id: str | None = None,
        organization_id: str | None = None,
        enrichment_request_id: str | None = None,
    ) -> dict | None:
        """Fetch full client row for update merges and audit logging.

        Call with either (client_id, organization_id) or enrichment_request_id=...
        (e.g. for enrichment webhooks). Return shape includes id, organization_id,
        and all merge fields.

        Returns:
            dict | None: Full client row or None if not found.
        """
        if enrichment_request_id is not None:
            where = "enrichment_request_id = $1 AND status != $2"
            params = (enrichment_request_id, ClientStatus.DELETED.value)
        else:
            if client_id is None or organization_id is None:
                raise ValueError(
                    "Provide either (client_id, organization_id) or enrichment_request_id"
                )
            where = "id = $1 AND organization_id = $2 AND status != $3"
            params = (client_id, organization_id, ClientStatus.DELETED.value)

        query = f"""
            SELECT id, organization_id, client_type, name, industry, profile_photo_url,
                   portal_access, tags, websites, billing_preferences, custom_fields,
                   additional_data, social_pages, enrichment_done, enrichment_status, last_enriched_at,
                   work_history, educational_history, skills,
                   target_market_segments, current_tech_stack, description,
                   preferred_communication_channels, industry_specific_terminologies,
                   linked_pages, products, key_people
            FROM clients
            WHERE {where}
        """
        row = await self.db_connection.fetchrow(query, *params)
        return dict(row) if row else None

    async def update_client(
        self, client_id: str, organization_id: str, update_data: dict
    ) -> dict | None:
        """Update client by id and organization_id. Only provided keys are updated."""
        set_parts = [
            f"{k} = ${i}::jsonb" if k in CLIENT_JSONB_COLUMNS else f"{k} = ${i}"
            for i, k in enumerate(update_data, start=1)
        ]
        set_expr = (
            ", ".join(set_parts) + ", updated_at = NOW()" if set_parts else "updated_at = NOW()"
        )
        index = len(update_data)
        params = [self._serialize_jsonb_param(k, v) for k, v in update_data.items()] + [
            client_id,
            organization_id,
            ClientStatus.DELETED.value,
        ]
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE clients
            SET {set_expr}
            WHERE id = ${index + 1} AND organization_id = ${index + 2} AND status != ${index + 3}
            RETURNING *
            """,
            *params,
        )
        return dict(row) if row else None

    async def update_lead(self, lead_id: str, client_id: str, update_data: dict) -> bool:
        """Update lead by id and client_id. Only provided keys are updated."""
        set_parts = [f"{k} = ${i}" for i, k in enumerate(update_data, start=1)]
        set_expr = (
            ", ".join(set_parts) + ", updated_at = NOW()" if set_parts else "updated_at = NOW()"
        )
        index = len(update_data)
        params = list(update_data.values()) + [lead_id, client_id]
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE leads
            SET {set_expr}
            WHERE id = ${index + 1} AND client_id = ${index + 2}
            RETURNING id
            """,
            *params,
        )
        return row is not None

    async def update_address(self, address_id: str, client_id: str, update_data: dict) -> bool:
        """Update address by id and client_id. Only provided keys are updated."""
        set_parts = [f"{k} = ${i}" for i, k in enumerate(update_data, start=1)]
        set_expr = (
            ", ".join(set_parts) + ", updated_at = NOW()" if set_parts else "updated_at = NOW()"
        )
        index = len(update_data)
        params = list(update_data.values()) + [address_id, client_id]
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE client_addresses
            SET {set_expr}
            WHERE id = ${index + 1} AND client_id = ${index + 2}
            RETURNING id
            """,
            *params,
        )
        return row is not None

    # VALIDATION OPERATIONS
    async def check_client_name_exists(
        self,
        name: str,
        organization_id: str,
        exclude_client_id: str | None = None,
    ) -> bool:
        """Check if client name exists for this organization (any client type).

        Args:
            name: Client name (normalized, e.g. full name for person or company name).
            organization_id: Organization ID.
            exclude_client_id: Client ID to exclude from check (e.g. current client on update).

        Returns:
            True if a non-deleted client with this name exists; False otherwise.
        """
        conditions = [
            "LOWER(name) = $1",
            "organization_id = $2",
            "status != $3",
        ]
        params: list[str] = [name.lower(), organization_id, ClientStatus.DELETED.value]
        next_index = 4

        if exclude_client_id is not None:
            conditions.append(f"id != ${next_index}")
            params.append(exclude_client_id)

        query = "SELECT EXISTS(SELECT 1 FROM clients WHERE " + " AND ".join(conditions) + ")"
        exists = await self.db_connection.fetchval(query, *params)
        return bool(exists)

    # LEAD OPERATIONS
    async def create_lead(self, lead_data: dict) -> dict:
        """Create a new lead record.

        Args:
            lead_data: Dictionary containing lead fields

        Returns:
            dict: Created lead record
        """
        fields = []
        placeholders = []
        values = []
        param_index = 1

        required_fields = ["client_id", "lead_status"]
        for field in required_fields:
            if field not in lead_data:
                raise ValueError(f"{field} is required")

        for field in required_fields:
            fields.append(field)
            placeholders.append(f"${param_index}")
            values.append(lead_data[field])
            param_index += 1

        optional_fields = ["intake_stage", "lead_source", "referral_source", "lead_score", "notes"]
        for field in optional_fields:
            if field in lead_data and lead_data[field] is not None:
                fields.append(field)
                placeholders.append(f"${param_index}")
                values.append(lead_data[field])
                param_index += 1

        query = f"""
            INSERT INTO leads ({", ".join(fields)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)

    # ADDRESS OPERATIONS
    async def bulk_create_addresses(self, addresses_data: list[dict]) -> list[dict]:
        """Bulk create multiple client address records using efficient multi-row INSERT.

        This method uses a single INSERT statement with multiple VALUES clauses
        for optimal performance with asyncpg.

        Args:
            addresses_data: List of address data dictionaries

        Returns:
            list[dict]: List of created address records
        """
        if not addresses_data:
            return []

        # Extract all columns that have non-None values across all addresses
        all_columns = set()
        for addr in addresses_data:
            all_columns.update(key for key, value in addr.items() if value is not None)

        # Define column order (client_id and address_line1 are required, then optional)
        required_columns = ["client_id", "address_line1"]
        optional_columns = [
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "place_id",
            "latitude",
            "longitude",
            "address_type",
            "address_data",
            "is_primary",
        ]

        # Build columns list: required columns + optional columns that exist in data
        columns = required_columns + [col for col in optional_columns if col in all_columns]

        # Build VALUES clauses and parameters
        values_clauses = []
        all_params = []
        param_index = 1

        for addr in addresses_data:
            placeholders = []
            params = []

            for col in columns:
                placeholders.append(f"${param_index}")
                params.append(addr.get(col))
                param_index += 1

            values_clauses.append(f"({', '.join(placeholders)})")
            all_params.extend(params)

        query = f"""
            INSERT INTO client_addresses ({", ".join(columns)})
            VALUES {", ".join(values_clauses)}
            RETURNING *
        """

        rows = await self.db_connection.fetch(query, *all_params)
        return [dict(row) for row in rows]

    async def get_client_addresses(self, client_id: str) -> list[dict]:
        """Get all addresses for a client.

        Args:
            client_id: Client ID

        Returns:
            list[dict]: List of address records
        """
        query = """
            SELECT * FROM client_addresses
            WHERE client_id = $1
            ORDER BY is_primary DESC, created_at ASC
        """
        rows = await self.db_connection.fetch(query, client_id)
        return [dict(row) for row in rows]

    async def get_client_details_with_primary_contact(
        self, client_id: str, organization_id: str
    ) -> dict | None:
        """Get client details with primary contact and lead information.

        Args:
            client_id: Client ID
            organization_id: Organization ID

        Returns:
            dict | None: Client record with primary contact and lead info or None if not found
        """
        deleted_client_user_status = ClientUserStatus.DELETED.value
        query = """
            SELECT
                c.id,
                c.organization_id,
                c.client_type,
                c.name,
                c.status,
                c.industry,
                c.profile_photo_url,
                c.tags,
                c.websites,
                c.billing_preferences,
                c.custom_fields,
                c.additional_data,
                c.social_pages,
                c.enrichment_done,
                c.last_enriched_at,
                c.work_history,
                c.educational_history,
                c.skills,
                c.target_market_segments,
                c.current_tech_stack,
                c.description,
                c.preferred_communication_channels,
                c.industry_specific_terminologies,
                c.linked_pages,
                c.products,
                c.key_people,
                c.created_at,
                c.updated_at,
                cu.first_name,
                cu.last_name,
                cu.title,
                au.email,
                au.raw_user_meta_data->>'phone_isd_code' as phone_isd_code,
                au.raw_user_meta_data->>'phone_number' as phone,
                l.id as lead_id,
                l.lead_status,
                l.intake_stage,
                l.lead_source,
                l.referral_source,
                l.lead_score,
                l.converted_at,
                l.notes as lead_notes,
                l.created_at as lead_created_at,
                l.updated_at as lead_updated_at
            FROM clients c
            LEFT JOIN client_users cu ON cu.client_id = c.id
                AND cu.is_primary_contact = true
                AND cu.status != $3
            LEFT JOIN auth.users au ON au.id = cu.user_id
            LEFT JOIN leads l ON l.client_id = c.id
            WHERE c.id = $1
                AND c.organization_id = $2
                AND c.status != $4
        """
        row = await self.db_connection.fetchrow(
            query,
            client_id,
            organization_id,
            deleted_client_user_status,
            ClientStatus.DELETED.value,
        )
        return dict(row) if row else None
