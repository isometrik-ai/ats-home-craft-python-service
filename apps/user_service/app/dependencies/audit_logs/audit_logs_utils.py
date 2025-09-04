# pylint: disable=R0902
"""
Audit Logs Utility Functions Module

This module contains utility functions for audit logs API operations.
These functions handle validation, query building, and data formatting.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""
import json
from typing import List, Tuple, Optional
from fastapi import HTTPException, status


def build_audit_logs_filter_query(
    organization_id: str,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[str, List]:
    """
    Build SQL query for filtering audit logs with search and pagination.

    Args:
        organization_id (str): Organization ID to filter audit logs
        search (Optional[str]): Search term to filter by description, action_type, or table_name
        limit (int): Maximum number of records to return
        offset (int): Number of records to skip

    Returns:
        Tuple[str, List]: SQL query and parameters list
    """
    # base_query = """
    #     SELECT
    #         id, organization_id, user_id, user_email, user_role,
    #         action_type, data_classification, table_name, record_id,
    #         old_values, new_values, changed_fields, compliance_tags,
    #         risk_level, ip_address, description, timestamp
    #     FROM public.audit_logs
    #     WHERE organization_id = $1
    # """
    base_query = """
        SELECT
            id, organization_id, user_id, user_email, user_role,
            action_type, data_classification, table_name, record_id,
            old_values, new_values, changed_fields, compliance_tags,
            risk_level, ip_address, description, timestamp,
            status_code, category
        FROM public.audit_logs
    """
    params = [organization_id]
    param_count = 1
    params = []
    param_count = 0

    # Add search condition if provided
    if search:
        param_count += 1
        search_condition = f"""
            WHERE (
                LOWER(description) LIKE LOWER(${param_count}) OR
                LOWER(action_type) LIKE LOWER(${param_count}) OR
                LOWER(table_name) LIKE LOWER(${param_count})
            )
        """
        base_query += search_condition
        params.append(f"%{search}%")

    # Add ordering and pagination
    base_query += (
        """
        ORDER BY timestamp DESC
        LIMIT $"""
        + str(param_count + 1)
        + " OFFSET $"
        + str(param_count + 2)
    )

    params.extend([limit, offset])

    return base_query, params


def build_audit_logs_count_query(
    # organization_id: str,
    search: Optional[str] = None,
) -> Tuple[str, List]:
    """
    Build SQL query for counting total audit logs with search filter.

    Args:
        organization_id (str): Organization ID to filter audit logs
        search (Optional[str]): Search term to filter by description, action_type, or table_name

    Returns:
        Tuple[str, List]: SQL query and parameters list
    """
    base_query = """
        SELECT COUNT(*) as total_count
        FROM public.audit_logs
    """
    # base_query = """
    #     SELECT COUNT(*) as total_count
    #     FROM public.audit_logs
    #     WHERE organization_id = $1
    # """

    params = []

    # Add search condition if provided
    if search:
        search_condition = """
            WHERE (
                LOWER(description) LIKE LOWER($1) OR
                LOWER(action_type) LIKE LOWER($1) OR
                LOWER(table_name) LIKE LOWER($1)
            )
        """
        base_query += search_condition
        params.append(f"%{search}%")

    return base_query, params


def build_audit_log_by_id_query() -> str:
    """
    Build SQL query for getting a single audit log by ID.

    Returns:
        str: SQL query for getting audit log by ID
    """
    return """
        SELECT
            id, organization_id, user_id, user_email, user_role,
            action_type, data_classification, table_name, record_id,
            old_values, new_values, changed_fields, compliance_tags,
            risk_level, ip_address, description, timestamp,
            hash_signature, previous_hash, retention_date,
            status_code, category
        FROM public.audit_logs
        WHERE id = $1
        LIMIT 1;
    """


def build_delete_all_audit_logs_query() -> str:
    """
    Build SQL query for deleting all audit logs.

    Returns:
        str: SQL query for deleting all audit logs
    """
    return """
        DELETE FROM public.audit_logs;
    """


def build_audit_logs_filter_message(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> str:
    """
    Build response message for audit logs filter operation.

    Args:
        search (Optional[str]): Search term used in filtering
        skip (int): Number of records skipped
        limit (int): Number of records returned

    Returns:
        str: Formatted response message
    """
    if search:
        return (
            f"Audit logs retrieved successfully "
            f"(search: '{search}', showing {limit} records "
            f"starting from position {skip + 1})"
        )

    return (
        f"Audit logs retrieved successfully "
        f"(showing {limit} records starting from position {skip + 1})"
    )


def check_audit_logs_view_permission(
    user_context,
    # db_conn,
) -> dict:
    """
    Check if user has permission to view audit logs.

    Args:
        user_context: User context from JWT token
        db_conn: Database connection

    Returns:
        dict: User context if permission is granted

    Raises:
        HTTPException: If user doesn't have permission
    """
    # For audit logs, typically only admin users or users with specific audit permissions
    # should be able to view them. This is a basic implementation.
    # You may want to add more sophisticated permission checking based on your requirements.

    if not user_context or not user_context.get("organization_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user context: missing organization_id",
        )

    # Add your specific permission logic here
    # For example, check if user has "audit.view" or "admin" permissions

    return user_context


def format_audit_log_data(audit_log_row) -> dict:
    """
    Format audit log data from database row to API response format.

    Args:
        audit_log_row: Database row containing audit log data

    Returns:
        dict: Formatted audit log data
    """
    # Parse JSON fields if they exist
    old_values = None
    new_values = None
    changed_fields = None
    compliance_tags = None

    if audit_log_row.get("old_values"):
        try:
            old_values = json.loads(audit_log_row["old_values"])
        except (json.JSONDecodeError, TypeError):
            old_values = None

    if audit_log_row.get("new_values"):
        try:
            new_values = json.loads(audit_log_row["new_values"])
        except (json.JSONDecodeError, TypeError):
            new_values = None

    if audit_log_row.get("changed_fields"):
        try:
            changed_fields = json.loads(audit_log_row["changed_fields"])
        except (json.JSONDecodeError, TypeError):
            changed_fields = None

    if audit_log_row.get("compliance_tags"):
        try:
            compliance_tags = audit_log_row["compliance_tags"]
        except (TypeError, AttributeError):
            compliance_tags = None

    return {
        "id": str(audit_log_row["id"]),
        "organization_id": str(audit_log_row["organization_id"]),
        "user_id": str(audit_log_row["user_id"]),
        "user_email": audit_log_row["user_email"],
        "user_role": audit_log_row["user_role"],
        "action_type": audit_log_row["action_type"],
        "data_classification": audit_log_row["data_classification"],
        "table_name": audit_log_row["table_name"],
        "record_id": audit_log_row["record_id"],
        "old_values": old_values,
        "new_values": new_values,
        "changed_fields": changed_fields,
        "compliance_tags": compliance_tags,
        "risk_level": audit_log_row["risk_level"],
        "ip_address": audit_log_row["ip_address"],
        "description": audit_log_row["description"],
        "timestamp": (
            audit_log_row["timestamp"].isoformat()
            if audit_log_row["timestamp"]
            else None
        ),
        "status_code": audit_log_row.get("status_code"),
        "category": audit_log_row.get("category"),
    }


def format_audit_log_detail_data(audit_log_row) -> dict:
    """
    Format detailed audit log data from database row to API response format.

    Args:
        audit_log_row: Database row containing detailed audit log data

    Returns:
        dict: Formatted detailed audit log data
    """
    # Get basic formatted data
    basic_data = format_audit_log_data(audit_log_row)

    # Add additional fields for detailed view
    basic_data.update(
        {
            "hash_signature": audit_log_row.get("hash_signature"),
            "previous_hash": audit_log_row.get("previous_hash"),
            "retention_date": (
                audit_log_row["retention_date"].isoformat()
                if audit_log_row.get("retention_date")
                else None
            ),
        }
    )

    return basic_data


async def check_audit_log_exists(audit_log_id: str, db_conn) -> dict:
    """
    Check if audit log exists and return its data.

    Args:
        audit_log_id (str): ID of the audit log to check
        db_conn: Database connection

    Returns:
        dict: Audit log data if exists

    Raises:
        HTTPException: If audit log not found
    """
    query = build_audit_log_by_id_query()
    audit_log_data = await db_conn.fetchrow(query, audit_log_id)

    if not audit_log_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit log not found",
        )

    return audit_log_data


async def get_audit_logs_count(db_conn) -> int:
    """
    Get the total count of audit logs in the system.

    Args:
        db_conn: Database connection

    Returns:
        int: Total number of audit logs
    """
    count_query = "SELECT COUNT(*) as total_count FROM public.audit_logs;"
    count_result = await db_conn.fetchrow(count_query)
    return count_result["total_count"] if count_result else 0
