"""Verification Code Repository Module

This module provides database operations for the `verification_codes` table
using asyncpg in a FastAPI project.

Designed for asynchronous operations with an existing asyncpg connection.
"""

import json
from datetime import datetime, timedelta, timezone

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("verification_code_repository")


class VerificationCodeRepository:
    """Repository class for managing verification_codes table.

    Attributes:
        db_connection (asyncpg.Connection): Active asyncpg connection,
        potentially within a transaction.
    """

    def __init__(self, db_connection: asyncpg.Connection):
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection
        """
        self.db_connection = db_connection

    async def get_verification_code_by_id(self, verification_id: str) -> dict | None:
        """Get a verification code record by its ID.

        Args:
            verification_id: The ID of the verification code record.

        Returns:
            dict: The verification code record as a dictionary if found, else None.
        """
        query = """
            SELECT *
            FROM verification_codes
            WHERE id = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, verification_id)
        return dict(row) if row else None

    async def update_verification_code(
        self, verification_id: str, verified: bool, attempts: list[dict]
    ) -> dict:
        """Update a verification code record.

        Args:
            verification_id: The ID of the verification code record.
            verified: Boolean indicating whether the code was verified.
            attempts: List of attempt records to update.

        Returns:
            dict: The updated verification code record if successful
        Raises:
            DatabaseOperationError: If the update fails.
        """
        query = """
            UPDATE verification_codes
            SET verified = $1,
                attempts = $2
            WHERE id = $3
            RETURNING *
        """
        attempts_json = json.dumps(attempts)

        row = await self.db_connection.fetchrow(query, verified, attempts_json, verification_id)
        if row:
            return dict(row)

        logger.error("Failed to update verification code: %s", verification_id)

    async def get_recent_verification_codes(
        self, type_text: str, given_input: str, limit: int = 5, window_hours: int | None = None
    ) -> list[dict]:
        """Get recent verification codes for a given type and input from the database.
        Used to check verification attempt counts.

        Args:
            type_text (str): Type of verification (EMAIL or PHONE_NUMBER)
            given_input (str): The input value (email or phone number)
            limit (int, optional): Maximum number of records to return. Defaults to 5.
            window_hours (int, optional): Time window in hours to filter recent codes.
            Defaults to None.

        Returns:
            List[Dict]: List of verification code records
        """

        # Base query and parameters
        query = """
            SELECT *
            FROM verification_codes
            WHERE type_text = $1
                AND given_input = $2
        """
        params = [type_text, given_input]

        # Add optional time window
        if window_hours is not None:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            query += " AND created_at >= $3"
            params.append(cutoff_time)

        # Add ordering and limit
        query += " ORDER BY created_at DESC LIMIT $4"
        params.append(limit)

        # Execute query
        rows = await self.db_connection.fetch(query, *params)

        # Convert asyncpg records to dicts
        return [dict(row) for row in rows]

    async def insert_verification_code(self, verification_data: dict) -> dict:
        """Insert a new verification code record into the database.

        Args:
            verification_data: Dictionary containing all fields to insert

        Returns:
            The inserted verification code record as a dict
        """
        query = """
            INSERT INTO verification_codes (
                type_text,
                given_input,
                triggered_text,
                verification_code,
                verified,
                expiry_at,
                attempts,
                user_id,
                ip_address
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
        """

        # Prepare values in order (None for optional fields if missing)
        values = [
            verification_data["type_text"],
            verification_data["given_input"],
            verification_data["triggered_text"],
            verification_data["verification_code"],
            verification_data.get("verified", False),
            verification_data["expiry_at"],
            json.dumps(verification_data.get("attempts", [])),
            verification_data.get("user_id"),
            verification_data.get("ip_address"),
        ]

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row)
