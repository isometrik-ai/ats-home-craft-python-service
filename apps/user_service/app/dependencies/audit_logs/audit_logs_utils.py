"""Audit Logs Utility Functions Module.

This module contains utility functions for audit logs API operations.
These functions handle validation, query building, and data formatting.
"""

import json


def format_audit_log_data(audit_log_row: dict) -> dict:
    """Format audit log data from database row to API response format.

    Args:
        audit_log_row (dict): Database row containing audit log data

    Returns:
        dict: Formatted audit log data
    """
    # pylint: disable=too-complex
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
            audit_log_row["timestamp"].isoformat() if audit_log_row["timestamp"] else None
        ),
        "status_code": audit_log_row.get("status_code"),
        "category": audit_log_row.get("category"),
    }


def format_audit_log_detail_data(audit_log_row: dict) -> dict:
    """Format detailed audit log data from database row to API response format.

    Args:
        audit_log_row (dict): Database row containing detailed audit log data

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
