"""
Audit Database Operations Module

This module contains all audit-related database operations.
All SQL queries for audit log management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Audit log CRUD operations
- Audit log search and filtering
- Audit log batch operations
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from .exception_handling import handle_database_errors, create_error_messages

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _get_supabase_client():
    """Get Supabase admin client."""
    return await get_supabase_admin_client()


def _has_result_data(result) -> bool:
    """Check if result has data."""
    return len(result.data) > 0 if result.data else False


def _get_result_data(result, default=None):
    """Get result data with default fallback."""
    return result.data if result.data else (default or [])


def _build_audit_record(audit_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build audit record from audit data."""
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
        "category": audit_data.get("category")
    }

    # Add optional fields
    if audit_data.get("retention_date"):
        audit_record["retention_date"] = audit_data.get("retention_date")

    return audit_record


@dataclass
class AuditLogFilter:
    """Data class for audit log filtering parameters."""
    organization_id: str
    search: Optional[str] = None
    action_type: Optional[str] = None
    table_name: Optional[str] = None
    user_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = 20
    offset: int = 0


# ============================================================================
# AUDIT LOG CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_audit_log",
    custom_messages=create_error_messages("create_audit_log", "creating"))
async def create_audit_log(audit_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new audit log entry."""
    supabase = await _get_supabase_client()

    audit_record = _build_audit_record(audit_data)
    result = await supabase.table("audit_logs").insert(audit_record).execute()

    if _has_result_data(result):
        return result.data[0]
    return {}


@handle_database_errors(
    "get_audit_log_by_id",
    custom_messages=create_error_messages("get_audit_log_by_id", "getting"))
async def get_audit_log_by_id(audit_log_id: str) -> Optional[Dict[str, Any]]:
    """Get audit log by ID."""
    supabase = await _get_supabase_client()

    result = await supabase.table("audit_logs").select(
        "id, organization_id, user_id, user_email, user_role, "
        "action_type, data_classification, table_name, record_id, "
        "old_values, new_values, changed_fields, compliance_tags, "
        "risk_level, ip_address, description, timestamp, "
        "hash_signature, previous_hash, retention_date, "
        "status_code, category"
    ).eq("id", audit_log_id).limit(1).execute()

    if _has_result_data(result):
        return result.data[0]
    return None


@handle_database_errors(
    "delete_all_audit_logs",
    custom_messages=create_error_messages("delete_all_audit_logs", "deleting"))
async def delete_all_audit_logs() -> int:
    """Delete all audit logs from database."""
    supabase = await _get_supabase_client()

    # First get count for return value
    count_result = await supabase.table("audit_logs").select("id", count="exact").execute()
    total_count = count_result.count if count_result.count is not None else 0

    # Delete all audit logs
    await supabase.table("audit_logs").delete().neq("id", "").execute()

    return total_count


# ============================================================================
# AUDIT LOG LISTING AND SEARCH
# ============================================================================

@handle_database_errors(
    "get_audit_logs_list",
    custom_messages=create_error_messages("get_audit_logs_list", "getting"))
async def get_audit_logs_list(filter_params: AuditLogFilter) -> List[Dict[str, Any]]:
    """Get paginated list of audit logs with optional search and filtering."""
    supabase = await _get_supabase_client()

    # Build the query with filters
    query = supabase.table("audit_logs").select(
        "id, organization_id, user_id, user_email, user_role, "
        "action_type, data_classification, table_name, record_id, "
        "old_values, new_values, changed_fields, compliance_tags, "
        "risk_level, ip_address, description, timestamp, "
        "status_code, category"
    )

    # Apply filters
    if filter_params.organization_id:
        query = query.eq("organization_id", filter_params.organization_id)

    if filter_params.action_type:
        query = query.eq("action_type", filter_params.action_type)

    if filter_params.table_name:
        query = query.eq("table_name", filter_params.table_name)

    if filter_params.user_id:
        query = query.eq("user_id", filter_params.user_id)

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

    # Apply pagination and ordering
    result = await query.order("timestamp", desc=True).range(
        filter_params.offset, filter_params.offset + filter_params.limit - 1
    ).execute()

    return _get_result_data(result)


@handle_database_errors(
    "get_audit_logs_count",
    custom_messages=create_error_messages("get_audit_logs_count", "getting"))
async def get_audit_logs_count(filter_params: AuditLogFilter) -> int:
    """Get total count of audit logs matching search criteria."""
    supabase = await _get_supabase_client()

    # Build the count query with filters
    query = supabase.table("audit_logs").select("id", count="exact")

    # Apply filters
    if filter_params.organization_id:
        query = query.eq("organization_id", filter_params.organization_id)

    if filter_params.action_type:
        query = query.eq("action_type", filter_params.action_type)

    if filter_params.table_name:
        query = query.eq("table_name", filter_params.table_name)

    if filter_params.user_id:
        query = query.eq("user_id", filter_params.user_id)

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


@handle_database_errors(
    "get_last_audit_log_hash",
    custom_messages=create_error_messages("get_last_audit_log_hash", "getting"))
async def get_last_audit_log_hash(organization_id: str) -> Optional[str]:
    """Get the last audit log hash signature for an organization."""
    supabase = await _get_supabase_client()

    result = await supabase.table("audit_logs").select("hash_signature").eq(
        "organization_id", organization_id
    ).order("timestamp", desc=True).order("id", desc=True).limit(1).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]["hash_signature"]
    return None


# ============================================================================
# AUDIT LOG QUERY BUILDING
# ============================================================================

# Note: Query building functions have been removed as Supabase SDK
# provides built-in query methods that are more efficient and type-safe.
# The filtering logic is now handled directly in the respective functions.


# ============================================================================
# AUDIT LOG BATCH OPERATIONS
# ============================================================================

@handle_database_errors(
    "bulk_create_audit_logs",
    custom_messages=create_error_messages("bulk_create_audit_logs", "bulk creating"))
async def bulk_create_audit_logs(audit_logs_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bulk create multiple audit log entries."""
    if not audit_logs_data:
        return []

    supabase = await _get_supabase_client()

    # Prepare all audit records using helper function
    audit_records = []
    for audit_data in audit_logs_data:
        audit_record = _build_audit_record(audit_data)
        # Handle timestamp conversion for bulk operations
        if isinstance(audit_data.get("timestamp"), datetime):
            audit_record["timestamp"] = audit_data["timestamp"].isoformat()
        if audit_data.get("retention_date") and isinstance(
            audit_data.get("retention_date"), datetime):
            audit_record["retention_date"] = audit_data.get("retention_date").isoformat()
        audit_records.append(audit_record)


    # Bulk insert all records
    result = await supabase.table("audit_logs").insert(audit_records).execute()
    print(f"Bulk create audit logs result: {result}")
    return _get_result_data(result)
