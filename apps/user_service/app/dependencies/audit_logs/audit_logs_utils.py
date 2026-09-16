"""Audit Logs Utility Functions Module.

This module contains utility functions for audit logs API operations.
These functions handle validation, query building, and data formatting.
"""

import json
import re
from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel

_PROJECT_PATH_PATTERN = re.compile(
    r"/projects/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _normalize_project_id(value: Any) -> str | None:
    """Return a canonical UUID string when value is a valid project id."""
    if value is None:
        return None
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


_NESTED_PROJECT_ID_KEYS = (
    "data",
    "new_data",
    "old_data",
    "unit",
    "project",
    "items",
    "units",
    "properties",
    "results",
)


def _nested_audit_payloads(data: dict[str, Any]) -> list[Any]:
    """Collect nested response fragments that may carry project_id."""
    return [data[key] for key in _NESTED_PROJECT_ID_KEYS if data.get(key) is not None]


def extract_project_id_from_data(data: Any) -> str | None:
    """Resolve project_id from service response payloads passed into audit context."""
    queue: list[Any] = [data]
    for _ in range(5):
        if not queue:
            break
        next_queue: list[Any] = []
        for item in queue:
            if item is None:
                continue
            if isinstance(item, BaseModel):
                found = _normalize_project_id(getattr(item, "project_id", None))
                if found:
                    return found
                next_queue.append(item.model_dump(mode="json"))
                continue
            if isinstance(item, dict):
                found = _normalize_project_id(item.get("project_id"))
                if found:
                    return found
                next_queue.extend(_nested_audit_payloads(item))
                continue
            if isinstance(item, (list, tuple)):
                next_queue.extend(item)
        queue = next_queue
    return None


def extract_project_id_from_request(request: Request) -> str | None:
    """Resolve project_id from explicit audit state, URL path, or query params."""
    explicit = getattr(request.state, "audit_project_id", None)
    if explicit:
        return str(explicit)

    path_match = _PROJECT_PATH_PATTERN.search(str(request.url.path))
    if path_match:
        return path_match.group(1)

    query_project_id = request.query_params.get("project_id")
    if query_project_id:
        try:
            return str(UUID(str(query_project_id).strip()))
        except (ValueError, AttributeError, TypeError):
            return None

    return None


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
