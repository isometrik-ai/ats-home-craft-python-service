"""Unit tests for AuditLogService with mocked repository."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.audit_logs import AuditLogFilter
from apps.user_service.app.services.audit_log_service import AuditLogService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"
LOG_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    """Build user context for audit log tests."""
    return UserContext(user_id=USER_ID, email="user@example.com", organization_id=ORG_ID)


def _raw_audit_data(**overrides):
    """Build raw audit log dict for DB preparation tests."""
    base = {
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "user_email": "user@example.com",
        "user_role": "admin",
        "action_type": "UPDATE",
        "data_classification": "general",
        "table_name": "leads",
        "record_id": "rec-1",
        "old_values": {"name": "Old"},
        "new_values": {"name": "New"},
        "changed_fields": ["name"],
        "compliance_tags": ["gdpr"],
        "risk_level": "low",
        "ip_address": "127.0.0.1",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "hash_signature": "abc123",
        "previous_hash": "prev123",
        "description": "Updated lead",
        "retention_date": date(2029, 1, 1),
        "status_code": 200,
        "category": "crm",
    }
    base.update(overrides)
    return base


def _db_row(**overrides):
    """Build raw audit log row from database."""
    row = {
        "id": LOG_ID,
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "user_email": "user@example.com",
        "user_role": "admin",
        "action_type": "UPDATE",
        "data_classification": "general",
        "table_name": "leads",
        "record_id": "rec-1",
        "old_values": {"name": "Old"},
        "new_values": {"name": "New"},
        "changed_fields": ["name"],
        "compliance_tags": ["gdpr"],
        "risk_level": "low",
        "ip_address": "127.0.0.1",
        "description": "Updated lead",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor_name": "Jane Doe",
        "hash_signature": "abc123",
        "previous_hash": "prev123",
        "retention_date": "2029-01-01T00:00:00+00:00",
        "status_code": 200,
        "category": "crm",
    }
    row.update(overrides)
    return row


class _FakeAuditLogRepo:
    """Configurable fake AuditLogRepository."""

    def __init__(
        self,
        *,
        list_rows: list | None = None,
        total: int = 0,
        detail_row: dict | None = None,
        deleted_count: int = 0,
    ) -> None:
        self.list_rows = list_rows or []
        self.total = total
        self.detail_row = detail_row
        self.deleted_count = deleted_count
        self.last_filter: AuditLogFilter | None = None
        self.last_detail_kwargs: dict | None = None

    async def get_audit_logs_list(self, filter_params: AuditLogFilter):
        """Return configured list rows."""
        self.last_filter = filter_params
        return self.list_rows

    async def get_audit_logs_count(self, filter_params: AuditLogFilter):
        """Return configured total count."""
        return self.total

    async def get_audit_log_by_id(self, *, audit_log_id: str, organization_id: str, user_id: str):
        """Return configured detail row."""
        self.last_detail_kwargs = {
            "audit_log_id": audit_log_id,
            "organization_id": organization_id,
            "user_id": user_id,
        }
        return self.detail_row

    async def delete_all_audit_logs(self):
        """Return configured delete count."""
        return self.deleted_count


def _service(*, repo: _FakeAuditLogRepo | None = None) -> AuditLogService:
    """Build AuditLogService with fake repository."""
    svc = AuditLogService(user_context=_ctx(), db_connection=MagicMock())
    svc.audit_log_repository = repo or _FakeAuditLogRepo()
    return svc


def test_prepare_audit_log_for_db_serializes_jsonb():
    """JSONB fields are serialized; UUID/date/Decimal converted."""
    uid = uuid.UUID(ORG_ID)
    prepared = AuditLogService.prepare_audit_log_for_db(
        _raw_audit_data(
            old_values={"amount": Decimal("10.5"), "org": uid},
            new_values=None,
        )
    )

    assert prepared["organization_id"] == ORG_ID
    assert json.loads(prepared["old_values"]) == {"amount": 10.5, "org": ORG_ID}
    assert prepared["new_values"] is None


def test_prepare_bulk_audit_logs_empty():
    """Bulk prepare returns empty list for empty input."""
    assert AuditLogService.prepare_bulk_audit_logs_for_db([]) == []


def test_prepare_bulk_audit_logs_multiple():
    """Bulk prepare normalizes each record."""
    prepared = AuditLogService.prepare_bulk_audit_logs_for_db(
        [_raw_audit_data(), _raw_audit_data(action_type="CREATE")]
    )

    assert len(prepared) == 2
    assert prepared[0]["action_type"] == "UPDATE"
    assert prepared[1]["action_type"] == "CREATE"


def test_parse_json_field_from_dict():
    """Dict values pass through with embedded _json normalization."""
    value = {"companies_json": '[{"id": "c1"}]'}
    parsed = AuditLogService._parse_json_field(value)  # pylint: disable=protected-access

    assert parsed["companies_json"] == [{"id": "c1"}]


def test_parse_json_field_double_encoded_string():
    """Double-encoded JSON strings decode once or twice."""
    encoded = json.dumps(json.dumps({"x": 1}))
    parsed = AuditLogService._parse_json_field(encoded)  # pylint: disable=protected-access

    assert parsed == {"x": 1}


def test_parse_json_field_invalid_returns_default():
    """Invalid JSON returns default."""
    assert AuditLogService._parse_json_field("not-json", default=[]) == []  # pylint: disable=protected-access
    assert AuditLogService._parse_json_field(None, default={}) == {}  # pylint: disable=protected-access
    assert AuditLogService._parse_json_field("", default=None) is None  # pylint: disable=protected-access


def test_format_user_email_and_ip():
    """None email/IP become empty strings."""
    assert AuditLogService._format_user_email(None) == ""  # pylint: disable=protected-access
    assert AuditLogService._format_ip_address(None) == ""  # pylint: disable=protected-access
    assert AuditLogService._format_user_email("a@b.com") == "a@b.com"  # pylint: disable=protected-access


def test_format_audit_log_item():
    """List item formatter parses JSON fields and timestamps."""
    item = AuditLogService._format_audit_log_item(_db_row())  # pylint: disable=protected-access

    assert item.id == LOG_ID
    assert item.old_values == {"name": "Old"}
    assert item.timestamp == "2026-01-01T00:00:00+00:00"


def test_format_audit_log_detail_datetime_fields():
    """Detail formatter handles datetime retention_date."""
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    detail = AuditLogService._format_audit_log_detail(  # pylint: disable=protected-access
        _db_row(timestamp=ts, retention_date=ts, changed_fields=["name"])
    )

    assert detail.hash_signature == "abc123"
    assert detail.changed_fields == ["name"]
    assert detail.retention_date is not None


@pytest.mark.asyncio
async def test_get_audit_logs_paginated():
    """get_audit_logs formats rows and returns total count."""
    repo = _FakeAuditLogRepo(list_rows=[_db_row()], total=1)
    svc = _service(repo=repo)

    result = await svc.get_audit_logs(AuditLogFilter(organization_id=ORG_ID, limit=10, offset=0))

    assert result["total_count"] == 1
    assert len(result["audit_logs"]) == 1
    assert result["audit_logs"][0].id == LOG_ID
    assert repo.last_filter is not None


@pytest.mark.asyncio
async def test_get_audit_log_by_id_found():
    """get_audit_log_by_id returns formatted detail."""
    repo = _FakeAuditLogRepo(detail_row=_db_row())
    svc = _service(repo=repo)

    detail = await svc.get_audit_log_by_id(LOG_ID)

    assert detail.id == LOG_ID
    assert repo.last_detail_kwargs["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_get_audit_log_by_id_not_found():
    """Missing audit log raises NotFoundException."""
    svc = _service(repo=_FakeAuditLogRepo(detail_row=None))

    with pytest.raises(NotFoundException):
        await svc.get_audit_log_by_id(LOG_ID)


@pytest.mark.asyncio
async def test_delete_all_audit_logs():
    """delete_all_audit_logs delegates to repository."""
    repo = _FakeAuditLogRepo(deleted_count=7)
    svc = _service(repo=repo)

    count = await svc.delete_all_audit_logs()

    assert count == 7
