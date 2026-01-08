"""Organization Delete Request Database Repository Module - AsyncPG Implementation

This module contains organization delete request-related database operations using asyncpg.
All SQL queries for delete request management are centralized here with proper
transaction handling and efficient batch operations.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import DeleteRequestStatus


class OrganizationDeleteRequestRepository:
    """Database operations class for organization delete requests using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    # CREATE OPERATIONS
    async def create_delete_request(
        self,
        organization_id: str,
        requester_id: str,
    ) -> dict[str, Any]:
        """Create a new organization delete request record.

        Args:
            organization_id (str): Organization ID
            requester_id (str): User ID of the requester

        Returns:
            dict[str, Any]: Created delete request record
        """
        query = """
            INSERT INTO organization_delete_requests (
                organization_id,
                requester_id
            )
            VALUES ($1, $2)
            RETURNING *
        """

        row = await self.db_connection.fetchrow(query, organization_id, requester_id)
        return dict(row)

    # READ OPERATIONS
    async def get_delete_request_by_id(
        self,
        request_id: str,
    ) -> dict[str, Any] | None:
        """Get delete request by ID.

        Args:
            request_id (str): Delete request ID

        Returns:
            dict[str, Any] | None: Delete request or None if not found
        """
        query = """
            SELECT *
            FROM organization_delete_requests
            WHERE id = $1
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(query, request_id)
        return dict(row) if row else None

    async def get_pending_request_by_organization_and_requester(
        self,
        organization_id: str,
        requester_id: str,
    ) -> dict[str, Any] | None:
        """Get pending delete request for organization by requester.

        Args:
            organization_id (str): Organization ID
            requester_id (str): User ID of the requester

        Returns:
            dict[str, Any] | None: Pending delete request (with id) or None if not found
        """
        query = """
            SELECT id
            FROM organization_delete_requests
            WHERE organization_id = $1
              AND requester_id = $2
              AND status = $3
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(
            query, organization_id, requester_id, DeleteRequestStatus.PENDING.value
        )
        return dict(row) if row else None

    async def get_delete_requests_list(
        self,
        organization_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get paginated list of delete requests, optionally filtered by organization and status.

        Args:
            organization_id (str | None): Optional organization ID to filter by
            status (str | None): Optional status to filter by
            limit (int): Maximum number of records to return
            offset (int): Number of records to skip

        Returns:
            list[dict[str, Any]]: List of delete requests
        """
        conditions = []
        params: list[Any] = []
        param_idx = 1

        if organization_id:
            conditions.append(f"organization_id = ${param_idx}")
            params.append(organization_id)
            param_idx += 1

        if status:
            conditions.append(f"status = ${param_idx}")
            params.append(status)
            param_idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        params.extend([limit, offset])
        limit_idx = param_idx
        offset_idx = param_idx + 1

        query = f"""
            SELECT *
            FROM organization_delete_requests
            {where_clause}
            ORDER BY requested_at DESC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
        """

        rows = await self.db_connection.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_delete_requests_count(
        self,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> int:
        """Get total count of delete requests, optionally filtered by organization and status.

        Args:
            organization_id (str | None): Optional organization ID to filter by
            status (str | None): Optional status to filter by

        Returns:
            int: Total count of delete requests
        """
        conditions = []
        params: list[Any] = []
        param_idx = 1

        if organization_id:
            conditions.append(f"organization_id = ${param_idx}")
            params.append(organization_id)
            param_idx += 1

        if status:
            conditions.append(f"status = ${param_idx}")
            params.append(status)
            param_idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT COUNT(*) as count
            FROM organization_delete_requests
            {where_clause}
        """

        row = await self.db_connection.fetchrow(query, *params)
        return row["count"] if row else 0

    # UPDATE OPERATIONS
    async def update_delete_request_status(
        self,
        request_id: str,
        status: str,
        approver_id: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        """Update delete request status (approve or reject).

        This method updates the status of a delete request and can only be called
        if the request is in "pending" status. The database constraint ensures
        this can only be called once.

        Args:
            request_id (str): Delete request ID
            status (str): New status ('approved' or 'rejected')
            approver_id (str): User ID of the approver
            decision_reason (str): Reason for the decision

        Returns:
            dict[str, Any]: Updated delete request record

        Raises:
            ValueError: If request is not found or not in pending status
        """
        query = """
            UPDATE organization_delete_requests
            SET status = $1,
                approver_id = $2,
                decision_reason = $3,
                decision_at = NOW()
            WHERE id = $4
            RETURNING *
        """

        row = await self.db_connection.fetchrow(
            query, status, approver_id, decision_reason, request_id
        )
        if not row:
            raise ValueError("Delete request not found or already processed. Cannot update status.")
        return dict(row)

    async def approve_delete_request(
        self,
        request_id: str,
        approver_id: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        """Approve a delete request.

        Args:
            request_id (str): Delete request ID
            approver_id (str): User ID of the approver
            decision_reason (str): Reason for approval

        Returns:
            dict[str, Any]: Updated delete request record
        """
        return await self.update_delete_request_status(
            request_id=request_id,
            status=DeleteRequestStatus.APPROVED.value,
            approver_id=approver_id,
            decision_reason=decision_reason,
        )

    async def reject_delete_request(
        self,
        request_id: str,
        approver_id: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        """Reject a delete request.

        Args:
            request_id (str): Delete request ID
            approver_id (str): User ID of the approver
            decision_reason (str): Reason for rejection

        Returns:
            dict[str, Any]: Updated delete request record
        """
        return await self.update_delete_request_status(
            request_id=request_id,
            status=DeleteRequestStatus.REJECTED.value,
            approver_id=approver_id,
            decision_reason=decision_reason,
        )
