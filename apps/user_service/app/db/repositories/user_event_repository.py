"""User event database repository.

Provides update of user_events status by user_id within the current transaction.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import UserEventStatus


class UserEventRepository:
    """Repository for user_events table operations."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    async def get_user_event_by_user_id(
        self,
        user_id: str,
        select_columns: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Get user_event row by user_id.

        Args:
            user_id: User ID to look up.
            select_columns: Optional list of column names to select.
                If None or empty, selects all columns (*).

        Returns:
            User event details if found, else None.
        """
        if not select_columns:
            columns_str = "*"
        else:
            columns_str = ", ".join(select_columns)

        query = f"""
            SELECT {columns_str}
            FROM user_events
            WHERE user_id = $1
        """
        row = await self.db_connection.fetchrow(query, user_id)
        if row:
            return dict(row)
        return None

    async def update_status_by_user_id(self, user_id: str, status: UserEventStatus) -> None:
        """Update status of user_events row(s) for the given user_id.

        Args:
            user_id: User ID to match
            status: New status value
        """
        query = """
            UPDATE user_events
            SET status = $1, processed_at = NOW()
            WHERE user_id = $2
        """
        await self.db_connection.execute(query, status.value, user_id)
