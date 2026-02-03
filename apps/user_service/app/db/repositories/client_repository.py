"""Client Database Repository Module - AsyncPG Implementation

This module contains all client-related database operations using asyncpg.
All SQL queries for client management are centralized here with proper
transaction handling and efficient batch operations.
"""

import asyncpg

from libs.shared_utils.logger import get_logger

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

        Returns:
            dict: Created client user record

        Raises:
            ValueError: If required fields (client_id, organization_id) are missing
        """
        # Validate required fields
        client_id = client_user_data.get("client_id")
        organization_id = client_user_data.get("organization_id")

        if not client_id or not organization_id:
            raise ValueError("client_id and organization_id are required fields")

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
