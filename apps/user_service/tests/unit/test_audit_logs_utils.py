"""Unit tests for audit log formatting utilities."""

from __future__ import annotations

from datetime import datetime, timezone

from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    format_audit_log_data,
    format_audit_log_detail_data,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"
LOG_ID = "770e8400-e29b-41d4-a716-446655440002"


def _row(**overrides) -> dict:
    """Build a minimal audit log DB row."""
    row = {
        "id": LOG_ID,
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "user_email": "admin@example.com",
        "user_role": "admin",
        "action_type": "UPDATE",
        "data_classification": "internal",
        "table_name": "users",
        "record_id": USER_ID,
        "old_values": '{"name": "Old"}',
        "new_values": '{"name": "New"}',
        "changed_fields": '["name"]',
        "compliance_tags": ["audit_required"],
        "risk_level": "low",
        "ip_address": "127.0.0.1",
        "description": "Updated user",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "status_code": 200,
        "category": "USER",
        "hash_signature": "abc123",
        "previous_hash": "def456",
        "retention_date": datetime(2027, 1, 1, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_format_audit_log_data_parses_json_fields():
    """Valid JSON columns are parsed into response objects."""
    result = format_audit_log_data(_row())

    assert result["id"] == LOG_ID
    assert result["old_values"] == {"name": "Old"}
    assert result["new_values"] == {"name": "New"}
    assert result["changed_fields"] == ["name"]
    assert result["compliance_tags"] == ["audit_required"]
    assert result["timestamp"] == "2026-01-01T00:00:00+00:00"


def test_format_audit_log_data_invalid_json_becomes_none():
    """Malformed JSON values are safely ignored."""
    result = format_audit_log_data(
        _row(
            old_values="{bad",
            new_values=None,
            changed_fields="not-json",
        )
    )

    assert result["old_values"] is None
    assert result["new_values"] is None
    assert result["changed_fields"] is None


def test_format_audit_log_data_missing_timestamp():
    """Missing timestamp returns None instead of raising."""
    result = format_audit_log_data(_row(timestamp=None))

    assert result["timestamp"] is None


def test_format_audit_log_detail_data_includes_hash_fields():
    """Detail formatter adds hash and retention metadata."""
    result = format_audit_log_detail_data(_row())

    assert result["hash_signature"] == "abc123"
    assert result["previous_hash"] == "def456"
    assert result["retention_date"] == "2027-01-01T00:00:00+00:00"
    assert result["user_email"] == "admin@example.com"


class _ComplianceTagsRow(dict):
    """Row mapping where subscript access on compliance_tags raises."""

    def __getitem__(self, key):
        if key == "compliance_tags":
            raise TypeError("bad compliance_tags access")
        return super().__getitem__(key)


def test_format_audit_log_data_invalid_compliance_tags_type_error():
    """TypeError while reading compliance_tags becomes None (lines 47-48)."""
    row = _ComplianceTagsRow(_row(compliance_tags=["audit_required"]))

    result = format_audit_log_data(row)

    assert result["compliance_tags"] is None


def test_format_audit_log_data_invalid_compliance_tags_attribute_error():
    """AttributeError while reading compliance_tags becomes None."""

    class _AttrErrorRow(dict):
        def __getitem__(self, key):
            if key == "compliance_tags":
                raise AttributeError("bad compliance_tags access")
            return super().__getitem__(key)

    row = _AttrErrorRow(_row(compliance_tags=["gdpr"]))

    result = format_audit_log_data(row)

    assert result["compliance_tags"] is None
