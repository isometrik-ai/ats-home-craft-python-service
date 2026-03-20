"""Client Database Repository Module - AsyncPG Implementation

This module contains all client-related database operations using asyncpg.
All SQL queries for client management are centralized here with proper
transaction handling and efficient batch operations.

Requires client_users.phones JSONB (default '[]') for primary contact phone list.
"""

import json
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import ClientStatus, ClientUserStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("client_repository")

PRIMARY_CONTACT_JOIN_PREDICATE = """
(
    (c.client_type = 'person' AND cu.client_id = c.id)
    OR (
        c.client_type = 'company'
        AND cu.client_company_id = c.id
        AND cu.is_primary_contact = true
    )
)
""".strip()


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
        "sales_intelligence",
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

    @staticmethod
    def _build_primary_contact_join(
        client_user_status_condition: str,
        company_status_condition: str,
    ) -> str:
        """Build SQL LEFT JOIN snippet for contact user and linked company.

        The caller is responsible for providing fully-formed conditions that may
        use either literal values or positional parameters.
        """
        join_conditions = [client_user_status_condition, PRIMARY_CONTACT_JOIN_PREDICATE]
        join_on_clause = " AND ".join(join_conditions)

        return f"""
            LEFT JOIN client_users cu ON {join_on_clause}
            LEFT JOIN clients company_c ON company_c.id = cu.client_company_id
                AND {company_status_condition}
            LEFT JOIN auth.users au ON au.id = cu.user_id
        """

    # CREATE OPERATIONS
    async def create_client(self, clients_data: list[dict]) -> list[dict]:
        """Bulk insert client rows in a single INSERT statement.

        Only includes columns that appear in at least one row of clients_data.
        Required fields in each row: organization_id, client_type. Optional
        fields (name, industry, status, profile_photo_url, tags, websites,
        billing_preferences, custom_fields, portal_access, additional_data,
        social_pages) are included only when present. Callers must pass
        JSON strings for JSONB columns (websites, billing_preferences, etc.).

        Args:
            clients_data: List of client row dictionaries. Each dict must
                contain organization_id and client_type; other keys are optional.

        Returns:
            list[dict]: Created client records in the same order as clients_data,
                or empty list if clients_data is empty.
        """
        if not clients_data:
            return []

        optional_fields = [
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
        present_optional = set()
        for row in clients_data:
            present_optional.update(k for k in optional_fields if k in row)

        columns = ["organization_id", "client_type"] + [
            c for c in optional_fields if c in present_optional
        ]
        ncols = len(columns)
        placeholders = [
            "(" + ", ".join(f"${i * ncols + j + 1}" for j in range(ncols)) + ")"
            for i in range(len(clients_data))
        ]
        values_flat = []
        for row in clients_data:
            for col in columns:
                values_flat.append(row.get(col))

        query = (
            f"INSERT INTO clients ({', '.join(columns)}) "
            f"VALUES {', '.join(placeholders)} RETURNING *"
        )
        rows = await self.db_connection.fetch(query, *values_flat)
        return [dict(r) for r in rows]

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
            "client_company_id",
            "phones",
        ]

        for field_name in optional_field_mapping:
            if field_name in client_user_data and client_user_data[field_name] is not None:
                fields.append(field_name)
                placeholders.append(
                    f"${param_index}::jsonb" if field_name == "phones" else f"${param_index}"
                )
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

    async def is_active_client_user_for_organization(
        self, user_id: str, organization_id: str
    ) -> bool:
        """Check if user has an active client_user record for the given organization.

        Used when type=client to validate organization from client_users before
        updating session.

        Args:
            user_id: User ID (auth.users id)
            organization_id: Organization ID to validate

        Returns:
            True if user has an active (non-deleted) client_user for the org, False otherwise
        """
        query = """
            SELECT EXISTS(
                SELECT 1 FROM client_users
                WHERE user_id = $1 AND organization_id = $2 AND status != $3
            )
        """
        exists = await self.db_connection.fetchval(
            query, user_id, organization_id, ClientUserStatus.DELETED.value
        )
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

        # Add join for contact and linked company.
        # When listing contacts (client_type='person'), include all non-deleted client_users.
        # For companies (client_type='company') or mixed, restrict to primary contacts only.
        deleted_client_user_status = ClientUserStatus.DELETED.value
        deleted_client_status = ClientStatus.DELETED.value
        primary_contact_join = self._build_primary_contact_join(
            f"cu.status != '{deleted_client_user_status}'",
            f"company_c.status != '{deleted_client_status}'",
        )

        query = f"""
            SELECT
                c.id,
                c.client_type,
                c.name,
                company_c.id AS company_id,
                company_c.name AS company_name,
                c.status,
                c.industry AS industry,
                c.tags,
                c.profile_photo_url,
                c.created_at,
                c.updated_at,
                cu.first_name,
                cu.last_name,
                cu.title,
                au.email,
                cu.phones
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

    async def _delete_addresses_by_ids(self, client_id: str, address_ids: list[str]) -> None:
        """Delete addresses by ids for a client.

        This is an internal helper used by service-layer batch address operations.
        """
        if not address_ids:
            return
        query = """
            DELETE FROM client_addresses
            WHERE client_id = $1 AND id = ANY($2::uuid[])
        """
        await self.db_connection.execute(query, client_id, address_ids)

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
            FOR UPDATE
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

    async def clear_primary_addresses(
        self, client_id: str, exclude_address_id: str | None = None
    ) -> None:
        """Clear primary flag for a client's addresses.

        Args:
            client_id: Client ID.
            exclude_address_id: Optional address ID to keep untouched.
        """
        # Note: this statement can transiently produce 0 primaries; correctness under concurrent
        # updates is guaranteed by the DB unique constraint for primary address per client.
        await self.db_connection.execute(
            """
            UPDATE client_addresses
            SET is_primary = FALSE, updated_at = NOW()
            WHERE client_id = $1
              AND is_primary = TRUE
              AND ($2::uuid IS NULL OR id != $2::uuid)
            """,
            client_id,
            exclude_address_id,
        )

    # VALIDATION OPERATIONS

    async def _check_client_email_exists(
        self,
        email: str,
        organization_id: str,
        exclude_client_id: str | None = None,
    ) -> bool:
        """Check if a client with the given email exists for this organization.

        Email is resolved via the linked auth user for active client_users.

        Args:
            email: Email address to check (case-insensitive).
            organization_id: Organization ID.
            exclude_client_id: Optional client ID to exclude from the check
                (useful when updating an existing client).

        Returns:
            True if a non-deleted client in this organization is already linked
            to an auth user with the given email, False otherwise.
        """
        conditions = [
            "LOWER(au.email) = LOWER($1)",
            "cu.organization_id = $2",
            "cu.status != $3",
            "c.status != $4",
        ]
        params: list[Any] = [
            email,
            organization_id,
            ClientUserStatus.DELETED.value,
            ClientStatus.DELETED.value,
        ]
        next_index = 5

        if exclude_client_id is not None:
            conditions.append(f"c.id != ${next_index}")
            params.append(exclude_client_id)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT EXISTS(
                SELECT 1
                FROM client_users cu
                JOIN clients c ON c.id = cu.client_id
                JOIN auth.users au ON au.id = cu.user_id
                WHERE {where_clause}
            )
        """
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
        deleted_client_status = ClientStatus.DELETED.value
        primary_contact_join = self._build_primary_contact_join(
            "cu.status != $3",
            "company_c.status != $4",
        )
        query = f"""
            SELECT
                c.id,
                c.organization_id,
                c.client_type,
                c.name,
                c.portal_access,
                company_c.id AS company_id,
                company_c.name AS company_name,
                c.status,
                c.industry,
                c.profile_photo_url,
                c.tags,
                c.websites,
                c.billing_preferences,
                c.custom_fields,
                c.additional_data,
                c.sales_intelligence,
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
                cu.prefix,
                cu.first_name,
                cu.middle_name,
                cu.last_name,
                cu.title,
                cu.profile_photo_url AS contact_profile_photo_url,
                au.email,
                cu.phones,
                l.id as lead_id,
                l.lead_status,
                l.intake_stage,
                l.lead_source,
                l.referral_source,
                NULLIF(l.lead_score, '')::int AS lead_score,
                l.converted_at,
                l.notes as lead_notes,
                l.created_at as lead_created_at,
                l.updated_at as lead_updated_at
            FROM clients c
            {primary_contact_join}
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
            deleted_client_status,
        )
        return dict(row) if row else None

    async def get_company_contacts(
        self,
        company_client_id: str,
        organization_id: str,
    ) -> list[dict]:
        """Get all active contacts linked to a company client via client_company_id."""
        query = """
            SELECT
                cu.first_name,
                cu.last_name,
                cu.title,
                au.email,
                cu.is_primary_contact
            FROM client_users cu
            LEFT JOIN auth.users au ON au.id = cu.user_id
            WHERE cu.client_company_id = $1
                AND cu.organization_id = $2
                AND cu.status != $3
        """
        rows = await self.db_connection.fetch(
            query,
            company_client_id,
            organization_id,
            ClientUserStatus.DELETED.value,
        )
        return [dict(row) for row in rows]

    async def _get_primary_contact_for_update(
        self, client_id: str, organization_id: str
    ) -> dict | None:
        """Get contact client_user id and phones for update (e.g. phones batch).

        Returns one contact row for this client:
        for person client, the contact linked by client_id;
        for company client, only the primary contact linked by client_company_id.

        Returns:
            dict with id, phones (raw JSONB), and name parts; or None if no contact.
        """
        query = f"""
            SELECT cu.id, cu.phones, cu.first_name, cu.middle_name, cu.last_name
            FROM client_users cu
            JOIN clients c ON c.id = $1
            WHERE c.id = $1 AND c.organization_id = $2
                AND {PRIMARY_CONTACT_JOIN_PREDICATE}
                AND cu.status != $3
                AND c.status != $4
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(
            query,
            client_id,
            organization_id,
            ClientUserStatus.DELETED.value,
            ClientStatus.DELETED.value,
        )
        return dict(row) if row else None

    async def _update_client_user(self, client_user_id: str, update_data: dict[str, Any]) -> bool:
        """Update a client_user by id. Only provided keys are updated.

        Args:
            client_user_id: Client user ID
            update_data: Keys to update (e.g. phones). For phones pass JSON string.

        Returns:
            True if a row was updated.
        """
        if not update_data:
            return True
        set_parts = [
            f"{k} = ${i}::jsonb" if k == "phones" else f"{k} = ${i}"
            for i, k in enumerate(update_data, start=1)
        ]
        set_expr = ", ".join(set_parts) + ", updated_at = NOW()"
        params = list(update_data.values()) + [client_user_id]
        num_params = len(update_data)
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE client_users
            SET {set_expr}
            WHERE id = ${num_params + 1}
            RETURNING id
            """,
            *params,
        )
        return row is not None
