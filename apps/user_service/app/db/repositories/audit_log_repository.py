"""Audit Log Database Repository Module - AsyncPG Implementation

This module contains all audit log-related database operations using asyncpg.
All SQL queries for audit log management are centralized here with proper
transaction handling and efficient batch operations.

Note: This repository expects pre-formatted data from the service layer.
Data preparation (JSONB serialization, normalization) is handled in AuditLogService.
"""

from typing import Any

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.schemas.audit_logs import AuditLogFilter

logger = get_logger("audit_log_repository")

# Common field list for audit log list queries
AUDIT_LOG_LIST_FIELDS = (
    "id, organization_id, user_id, user_email, user_role, "
    "action_type, data_classification, table_name, record_id, "
    "old_values, new_values, changed_fields, compliance_tags, "
    "risk_level, ip_address, description, timestamp, "
    "status_code, category"
)

# Common field list for audit log detail queries (includes hash fields)
AUDIT_LOG_DETAIL_FIELDS = (
    "id, organization_id, user_id, user_email, user_role, "
    "action_type, data_classification, table_name, record_id, "
    "old_values, new_values, changed_fields, compliance_tags, "
    "risk_level, ip_address, description, timestamp, "
    "hash_signature, previous_hash, retention_date, "
    "status_code, category"
)

# JSONB columns that need ::jsonb casting
JSONB_COLUMNS = {"old_values", "new_values"}

# Standard column order for consistent query building
COLUMN_ORDER = [
    "organization_id",
    "user_id",
    "user_email",
    "user_role",
    "action_type",
    "data_classification",
    "table_name",
    "record_id",
    "old_values",
    "new_values",
    "changed_fields",
    "compliance_tags",
    "risk_level",
    "ip_address",
    "timestamp",
    "hash_signature",
    "previous_hash",
    "description",
    "retention_date",
    "status_code",
    "category",
]


class AuditLogRepository:
    """Database operations class for audit log management using asyncpg.

    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    def _build_audit_log_filters(self, filter_params: AuditLogFilter) -> tuple[str, list[Any]]:
        """Build WHERE clause and parameters for audit log queries.

        Args:
            filter_params: Filter parameters

        Returns:
            Tuple containing (where_clause, params) for use in SQL query
        """
        conditions = [
            "organization_id = $1",
        ]
        params = [filter_params.organization_id]
        param_index = 2

        # Apply user_id filter
        if filter_params.user_id:
            conditions.append(f"user_id = ${param_index}")
            params.append(filter_params.user_id)
            param_index += 1

        # Apply action_type filter
        if filter_params.action_type:
            conditions.append(f"action_type = ${param_index}")
            params.append(filter_params.action_type)
            param_index += 1

        # Apply table_name filter
        if filter_params.table_name:
            conditions.append(f"table_name = ${param_index}")
            params.append(filter_params.table_name)
            param_index += 1

        # Apply start_date filter
        if filter_params.start_date:
            conditions.append(f"timestamp >= ${param_index}")
            params.append(filter_params.start_date)
            param_index += 1

        # Apply end_date filter
        if filter_params.end_date:
            conditions.append(f"timestamp <= ${param_index}")
            params.append(filter_params.end_date)
            param_index += 1

        # Apply search filter (searches in description, action_type, and table_name)
        if filter_params.search:
            search_term = f"%{filter_params.search}%"
            conditions.append(
                f"(description ILIKE ${param_index} OR "
                f"action_type ILIKE ${param_index} OR "
                f"table_name ILIKE ${param_index})"
            )
            params.append(search_term)
            param_index += 1

        where_clause = " AND ".join(conditions)
        return where_clause, params

    async def get_audit_logs_list(self, filter_params: AuditLogFilter) -> list[dict[str, Any]]:
        """Get paginated list of audit logs with optional search and filtering.

        Args:
            filter_params: Filter parameters

        Returns:
            List of audit log records
        """
        where_clause, params = self._build_audit_log_filters(filter_params)

        # Add pagination parameters
        limit_param = len(params) + 1
        offset_param = len(params) + 2
        query_params = params + [filter_params.limit, filter_params.offset]

        query = f"""
            SELECT {AUDIT_LOG_LIST_FIELDS}
            FROM audit_logs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
        """

        rows = await self.db_connection.fetch(query, *query_params)
        return [dict(row) for row in rows]

    async def get_audit_logs_count(self, filter_params: AuditLogFilter) -> int:
        """Get total count of audit logs matching search criteria.

        Args:
            filter_params: Filter parameters

        Returns:
            Total count of audit logs
        """
        where_clause, params = self._build_audit_log_filters(filter_params)

        query = f"""
            SELECT COUNT(*)
            FROM audit_logs
            WHERE {where_clause}
        """

        count = await self.db_connection.fetchval(query, *params) or 0
        return int(count)

    async def get_audit_log_by_id(
        self, audit_log_id: str, organization_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Get audit log by ID.

        Args:
            audit_log_id: Audit log ID
            organization_id: Organization ID
            user_id: User ID

        Returns:
            Audit record or None if not found
        """
        query = f"""
            SELECT {AUDIT_LOG_DETAIL_FIELDS}
            FROM audit_logs
            WHERE id = $1
            AND organization_id = $2
            AND user_id = $3
            LIMIT 1
        """

        row = await self.db_connection.fetchrow(query, audit_log_id, organization_id, user_id)
        return dict(row) if row else None

    async def delete_all_audit_logs(self) -> int:
        """Delete all audit logs from database.

        Returns:
            Total count of audit logs deleted
        """
        # Get count before deletion
        count_query = "SELECT COUNT(*) FROM audit_logs"
        total_count = await self.db_connection.fetchval(count_query) or 0

        # Delete all audit logs
        delete_query = "DELETE FROM audit_logs"
        await self.db_connection.execute(delete_query)

        return int(total_count)

    async def get_last_audit_log_hash(self, organization_id: str) -> str | None:
        """Get the last audit log hash signature for an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Last audit log hash signature or None if not found
        """
        query = """
            SELECT hash_signature
            FROM audit_logs
            WHERE organization_id = $1
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
        """

        result = await self.db_connection.fetchval(query, organization_id)
        return result

    async def create_audit_log(self, audit_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new audit log entry.

        Args:
            audit_data: Pre-formatted audit data dictionary (from service layer)

        Returns:
            Created audit record
        """
        # Build insert query dynamically using only non-None values
        columns = [k for k, v in audit_data.items() if v is not None]
        if not columns:
            return {}

        values = [audit_data[col] for col in columns]
        # Add ::jsonb cast for JSONB columns
        placeholders = [
            f"${i + 1}::jsonb" if col in ("old_values", "new_values") else f"${i + 1}"
            for i, col in enumerate(columns)
        ]

        query = f"""
            INSERT INTO audit_logs ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
            RETURNING {AUDIT_LOG_DETAIL_FIELDS}
        """

        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else {}

    def _extract_columns(self, audit_logs_data: list[dict[str, Any]]) -> list[str]:
        """Extract all non-None columns from audit log records.

        Args:
            audit_logs_data: List of audit log data dictionaries

        Returns:
            Ordered list of column names that have non-None values
        """
        all_columns = set()
        for record in audit_logs_data:
            all_columns.update(key for key, value in record.items() if value is not None)

        return [col for col in COLUMN_ORDER if col in all_columns]

    def _build_placeholder(self, column: str, param_index: int) -> str:
        """Build a SQL placeholder for a column, with ::jsonb cast if needed.

        Args:
            column: Column name
            param_index: Parameter index for SQL query

        Returns:
            SQL placeholder string (e.g., "$1" or "$1::jsonb")
        """
        placeholder = f"${param_index}"
        return f"{placeholder}::jsonb" if column in JSONB_COLUMNS else placeholder

    def _build_row_values(
        self, record: dict[str, Any], columns: list[str], start_param_index: int
    ) -> tuple[list[str], list[Any]]:
        """Build placeholders and parameter values for a single row.

        Args:
            record: Single audit log data dictionary
            columns: List of column names to include
            start_param_index: Starting parameter index

        Returns:
            Tuple of (placeholders_list, params_list)
        """
        placeholders = []
        params = []

        for idx, col in enumerate(columns):
            param_index = start_param_index + idx
            placeholders.append(self._build_placeholder(col, param_index))
            params.append(record.get(col))

        return placeholders, params

    def _build_bulk_insert_query(
        self, columns: list[str], audit_logs_data: list[dict[str, Any]]
    ) -> tuple[str, list[Any]]:
        """Build the bulk INSERT query with VALUES clauses and parameters.

        Args:
            columns: List of column names to insert
            audit_logs_data: List of audit log data dictionaries

        Returns:
            Tuple of (query_string, params_list)
        """
        values_clauses = []
        all_params = []
        param_index = 1

        for record in audit_logs_data:
            placeholders, params = self._build_row_values(record, columns, param_index)
            values_clauses.append(f"({', '.join(placeholders)})")
            all_params.extend(params)
            param_index += len(columns)

        query = f"""
            INSERT INTO audit_logs ({", ".join(columns)})
            VALUES {", ".join(values_clauses)}
            RETURNING {AUDIT_LOG_DETAIL_FIELDS}
        """

        return query, all_params

    async def bulk_create_audit_logs(
        self, audit_logs_data: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Bulk create multiple audit log entries using efficient multi-row INSERT.

        This method uses a single INSERT statement with multiple VALUES clauses
        for optimal performance with asyncpg.

        Args:
            audit_logs_data: List of pre-formatted audit log data dictionaries (from service layer)

        Returns:
            List of created audit records
        """
        if not audit_logs_data:
            return []

        # Extract columns that have non-None values
        columns = self._extract_columns(audit_logs_data)
        if not columns:
            return []

        # Build the bulk INSERT query
        query, params = self._build_bulk_insert_query(columns, audit_logs_data)

        try:
            rows = await self.db_connection.fetch(query, *params)
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Error in bulk_create_audit_logs: %s", str(e), exc_info=True)
            raise
