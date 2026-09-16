"""Unit tests for audit log formatting utilities."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from starlette.requests import Request

from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    extract_project_id_from_data,
    extract_project_id_from_request,
    format_audit_log_data,
    format_audit_log_detail_data,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import UserContext

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"
LOG_ID = "770e8400-e29b-41d4-a716-446655440002"
PROJECT_ID = "880e8400-e29b-41d4-a716-446655440003"


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


def test_extract_project_id_from_data_top_level_dict():
    """Service payloads with project_id are resolved directly."""
    assert extract_project_id_from_data({"project_id": PROJECT_ID}) == PROJECT_ID


def test_extract_project_id_from_data_nested_units():
    """List payloads expose project_id from the first unit row."""
    assert (
        extract_project_id_from_data({"units": [{"project_id": PROJECT_ID, "id": "unit-1"}]})
        == PROJECT_ID
    )


def test_extract_project_id_from_data_pydantic_model():
    """Pydantic response models are supported."""

    class Payload(BaseModel):
        project_id: str
        id: str

    assert extract_project_id_from_data(Payload(project_id=PROJECT_ID, id="evt-1")) == PROJECT_ID


def test_set_audit_context_resolves_project_id_from_new_data():
    """Explicit project_id param is optional when new_data includes it."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/pets",
            "headers": [],
            "query_string": b"",
        }
    )
    user_context = UserContext(
        user_id=USER_ID,
        email="resident@example.com",
        organization_id=ORG_ID,
    )
    set_audit_context(
        request,
        user_context,
        table="pets",
        description="Created pet",
        new_data={"id": "pet-1", "project_id": PROJECT_ID},
    )
    assert request.state.audit_project_id == PROJECT_ID


def test_extract_project_id_from_request_path():
    """Project id is parsed from /projects/{uuid}/ URL segments."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/v1/projects/{PROJECT_ID}/notices",
            "headers": [],
            "query_string": b"",
        }
    )

    assert extract_project_id_from_request(request) == PROJECT_ID


def test_extract_project_id_from_request_state_and_query():
    """Explicit audit state and query params override path parsing."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/audit-logs",
            "headers": [],
            "query_string": f"project_id={PROJECT_ID}".encode(),
        }
    )
    request.state.audit_project_id = "990e8400-e29b-41d4-a716-446655440004"

    assert extract_project_id_from_request(request) == "990e8400-e29b-41d4-a716-446655440004"


def test_extract_project_id_from_request_rejects_invalid_query_value():
    """Invalid project_id query values are ignored instead of persisted raw."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/audit-logs",
            "headers": [],
            "query_string": b"project_id=not-a-uuid",
        }
    )

    assert extract_project_id_from_request(request) is None


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
