"""Client Database Repository Module - AsyncPG Implementation

This module contains all client-related database operations using asyncpg.
All SQL queries for client management are centralized here with proper
transaction handling and efficient batch operations.
"""

import asyncpg

from apps.user_service.app.schemas.enums import ClientStatus, ClientUserStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("client_repository")


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

    # VALIDATION OPERATIONS
    async def check_email_exists(
        self, email: str, organization_id: str, exclude_client_id: str | None = None
    ) -> bool:
        """Check if email exists in auth.users for this organization.

        Args:
            email: Email address
            organization_id: Organization ID
            exclude_client_id: Client ID to exclude from check

        Returns:
            bool: True if email exists
        """
        # Check in client_users via user_id -> auth.users
        query = """
            SELECT EXISTS(
                SELECT 1
                FROM client_users cu
                INNER JOIN auth.users au ON au.id = cu.user_id
                WHERE au.email = $1
                AND cu.organization_id = $2
                AND cu.status != $3
        """
        params = [email, organization_id, ClientUserStatus.DELETED.value]
        if exclude_client_id:
            query += " AND cu.client_id != $3"
            params.append(exclude_client_id)
        query += ")"

        exists = await self.db_connection.fetchval(query, *params)
        return bool(exists)

    async def check_client_name_exists(
        self,
        name: str,
        organization_id: str,
        client_type: str | None = None,
        exclude_client_id: str | None = None,
    ) -> bool:
        """Check if client name exists for this organization.

        Args:
            name: Client name
            organization_id: Organization ID
            client_type: Optional client type filter ('person' or 'company')
            exclude_client_id: Client ID to exclude from check

        Returns:
            bool: True if name exists
        """
        query = """
            SELECT EXISTS(
                SELECT 1 FROM clients
                WHERE name = $1
                AND organization_id = $2
                AND status != $3
        """
        params = [name, organization_id, ClientStatus.DELETED.value]
        param_index = 4

        if client_type:
            query += f" AND client_type = ${param_index}"
            params.append(client_type)
            param_index += 1

        if exclude_client_id:
            query += f" AND id != ${param_index}"
            params.append(exclude_client_id)

        query += ")"

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
