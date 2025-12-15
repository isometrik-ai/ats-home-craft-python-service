"""Audit Database Operations Module
This module contains all audit-related database operations.
All SQL queries for audit log management should be centralized here.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_db.supabase_db.db import get_supabase_admin_client

logger = get_logger("audit_operations")


async def _get_supabase_client():
    """Get Supabase admin client.
    Returns:
        Supabase admin client
    """
    return await get_supabase_admin_client()


def _has_result_data(result: Any) -> bool:
    """Check if result has data.
    Args:
        result: Result
    Returns:
        bool: True if result has data, False otherwise
    """
    return len(result.data) > 0 if result.data else False


def _get_result_data(result: Any, default: Any = None) -> Any:
    """Get result data with default fallback.
    Args:
        result: Result
        default: Default value
    Returns:
        list: Result data
    """
    return result.data if result.data else (default or [])


def _build_audit_record(audit_data: dict[str, Any]) -> dict[str, Any]:
    """Build audit record from audit data.
    Args:
        audit_data: Audit data
    Returns:
        dict: Audit record
    """
    audit_record = {
        "organization_id": audit_data["organization_id"],
        "user_id": audit_data["user_id"],
        "user_email": audit_data["user_email"],
        "user_role": audit_data["user_role"],
        "action_type": audit_data["action_type"],
        "data_classification": audit_data["data_classification"],
        "table_name": audit_data["table_name"],
        "record_id": audit_data["record_id"],
        "old_values": json.dumps(audit_data.get("old_values", None)),
        "new_values": json.dumps(audit_data.get("new_values", None)),
        "changed_fields": audit_data.get("changed_fields"),
        "compliance_tags": audit_data.get("compliance_tags"),
        "risk_level": audit_data["risk_level"],
        "ip_address": audit_data["ip_address"],
        "timestamp": audit_data["timestamp"],
        "hash_signature": audit_data["hash_signature"],
        "previous_hash": audit_data.get("previous_hash"),
        "description": audit_data["description"],
        "status_code": audit_data.get("status_code"),
        "category": audit_data.get("category"),
    }

    # Add optional fields
    if audit_data.get("retention_date"):
        audit_record["retention_date"] = audit_data.get("retention_date")

    return audit_record


@dataclass
class AuditLogFilter:
    """Data class for audit log filtering parameters.
    Args:
        organization_id: Organization ID
        search: Search
        action_type: Action type
        table_name: Table name
        user_id: User ID
        start_date: Start date
        end_date: End date
        limit: Limit
        offset: Offset
    """

    organization_id: str
    search: str | None = None
    action_type: str | None = None
    table_name: str | None = None
    user_id: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    limit: int = 20
    offset: int = 0


async def create_audit_log(audit_data: dict[str, Any]) -> dict[str, Any]:
    """Create a new audit log entry.
    Args:
        audit_data: Audit data
    Returns:
        dict: Audit record
    """
    supabase = await _get_supabase_client()

    audit_record = _build_audit_record(audit_data)
    result = await supabase.table("audit_logs").insert(audit_record).execute()

    if _has_result_data(result):
        return result.data[0]
    return {}


async def get_audit_log_by_id(
    audit_log_id: str, organization_id: str, user_id: str
) -> dict[str, Any] | None:
    """Get audit log by ID.
    Args:
        audit_log_id: Audit log ID
        organization_id: Organization ID
        user_id: User ID
    Returns:
        dict: Audit record or None if not found
    """
    supabase = await _get_supabase_client()

    result = (
        await supabase.table("audit_logs")
        .select(
            "id, organization_id, user_id, user_email, user_role, "
            "action_type, data_classification, table_name, record_id, "
            "old_values, new_values, changed_fields, compliance_tags, "
            "risk_level, ip_address, description, timestamp, "
            "hash_signature, previous_hash, retention_date, "
            "status_code, category"
        )
        .eq("id", audit_log_id)
        .eq("organization_id", organization_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    if _has_result_data(result):
        return result.data[0]
    return None


async def delete_all_audit_logs() -> int:
    """Delete all audit logs from database.
    Returns:
        int: Total count of audit logs deleted
    """
    supabase = await _get_supabase_client()

    count_result = await supabase.table("audit_logs").select("id", count="exact").execute()
    total_count = count_result.count if count_result.count is not None else 0

    await supabase.table("audit_logs").delete().neq("id", "").execute()

    return total_count


async def get_audit_logs_list(filter_params: AuditLogFilter) -> list[dict[str, Any]]:
    """Get paginated list of audit logs with optional search and filtering.
    Args:
        filter_params: Filter parameters
    Returns:
        list: Audit logs
    """
    supabase = await _get_supabase_client()

    query = (
        supabase.table("audit_logs")
        .select(
            "id, organization_id, user_id, user_email, user_role, "
            "action_type, data_classification, table_name, record_id, "
            "old_values, new_values, changed_fields, compliance_tags, "
            "risk_level, ip_address, description, timestamp, "
            "status_code, category"
        )
        .eq("organization_id", filter_params.organization_id)
        .eq("user_id", filter_params.user_id)
    )

    if filter_params.action_type:
        query = query.eq("action_type", filter_params.action_type)

    if filter_params.table_name:
        query = query.eq("table_name", filter_params.table_name)

    if filter_params.start_date:
        query = query.gte("timestamp", filter_params.start_date.isoformat())

    if filter_params.end_date:
        query = query.lte("timestamp", filter_params.end_date.isoformat())

    if filter_params.search:
        query = query.or_(
            f"description.ilike.%{filter_params.search}%,"
            f"action_type.ilike.%{filter_params.search}%,"
            f"table_name.ilike.%{filter_params.search}%"
        )

    result = (
        await query.order("timestamp", desc=True)
        .range(filter_params.offset, filter_params.offset + filter_params.limit - 1)
        .execute()
    )

    return _get_result_data(result)


async def get_audit_logs_count(
    organization_id: str, user_id: str, filter_params: AuditLogFilter
) -> int:
    """Get total count of audit logs matching search criteria.
    Args:
        organization_id: Organization ID
        user_id: User ID
        filter_params: Filter parameters
    Returns:
        int: Total count of audit logs
    """
    supabase = await _get_supabase_client()

    # Build the count query with mandatory filters for RLS compliance
    query = (
        supabase.table("audit_logs")
        .select("id", count="exact")
        .eq("organization_id", organization_id)
        .eq("user_id", user_id)
    )

    # Apply additional optional filters
    if filter_params.action_type:
        query = query.eq("action_type", filter_params.action_type)

    if filter_params.table_name:
        query = query.eq("table_name", filter_params.table_name)

    if filter_params.start_date:
        query = query.gte("timestamp", filter_params.start_date.isoformat())

    if filter_params.end_date:
        query = query.lte("timestamp", filter_params.end_date.isoformat())

    if filter_params.search:
        query = query.or_(
            f"description.ilike.%{filter_params.search}%,"
            f"action_type.ilike.%{filter_params.search}%,"
            f"table_name.ilike.%{filter_params.search}%"
        )

    result = await query.execute()

    return result.count if result.count is not None else 0


async def get_last_audit_log_hash(organization_id: str) -> str | None:
    """Get the last audit log hash signature for an organization.
    Args:
        organization_id: Organization ID
    Returns:
        str: Last audit log hash signature or None if not found
    """
    supabase = await _get_supabase_client()

    result = (
        await supabase.table("audit_logs")
        .select("hash_signature")
        .eq("organization_id", organization_id)
        .order("timestamp", desc=True)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]["hash_signature"]
    return None


async def bulk_create_audit_logs(
    audit_logs_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bulk create multiple audit log entries.
    Args:
        audit_logs_data: Audit logs data
    Returns:
        list: Audit logs
    """
    if not audit_logs_data:
        return []

    supabase = await _get_supabase_client()

    audit_records = []
    for audit_data in audit_logs_data:
        audit_record = _build_audit_record(audit_data)
        if isinstance(audit_data.get("timestamp"), datetime):
            audit_record["timestamp"] = audit_data["timestamp"].isoformat()
        if audit_data.get("retention_date") and isinstance(
            audit_data.get("retention_date"), datetime
        ):
            audit_record["retention_date"] = audit_data.get("retention_date").isoformat()
        audit_records.append(audit_record)

    result = await supabase.table("audit_logs").insert(audit_records).execute()
    return _get_result_data(result)
