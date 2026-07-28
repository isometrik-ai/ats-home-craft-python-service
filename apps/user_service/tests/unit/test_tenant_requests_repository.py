"""Unit tests for TenantRequestsRepository query building with fake connection."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from apps.user_service.app.db.repositories.tenant_requests_repository import (
    TenantRequestsRepository,
)
from apps.user_service.app.schemas.enums import TenantRequestStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
REQUEST_ID = "880e8400-e29b-41d4-a716-446655440003"
CONTACT_ID = "990e8400-e29b-41d4-a716-446655440004"
DOCUMENT_ID = "aa0e8400-e29b-41d4-a716-446655440005"
USER_ID = "bb0e8400-e29b-41d4-a716-446655440006"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val


@pytest.mark.asyncio
async def test_insert_request_serializes_json_fields():
    phones = [{"type": "mobile", "number": "+15551234567"}]
    emails = [{"type": "personal", "address": "tenant@example.com"}]
    submitted_at = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    conn = _FakeConn(row={"id": REQUEST_ID})
    repo = TenantRequestsRepository(db_connection=conn)

    inserted = await repo.insert_request(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        submitted_by_contact_id=CONTACT_ID,
        tenant_first_name="Alice",
        tenant_last_name="Tenant",
        tenant_phones=phones,
        tenant_emails=emails,
        move_in_date=date(2026, 2, 1),
        portal_access=True,
        status=TenantRequestStatus.SUBMITTED.value,
        submitted_at=submitted_at,
    )

    assert inserted["id"] == REQUEST_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO tenant_requests" in query
    assert "::tenant_request_status" in query
    assert json.dumps(phones) in args
    assert json.dumps(emails) in args


@pytest.mark.asyncio
async def test_insert_document_and_event():
    conn = _FakeConn(row={"id": DOCUMENT_ID, "document_type": "lease_agreement"})
    repo = TenantRequestsRepository(db_connection=conn)

    doc = await repo.insert_document(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_type="lease_agreement",
        file_path="/files/lease.pdf",
        file_name="lease.pdf",
    )
    assert doc["document_type"] == "lease_agreement"
    doc_query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO tenant_request_documents" in doc_query
    assert "::tenant_request_document_type" in doc_query

    conn.row = {"id": "evt-1", "event_type": "submitted", "occurred_at": datetime.now(timezone.utc)}
    event = await repo.insert_event(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        event_type="submitted",
        actor_contact_id=CONTACT_ID,
        payload={"note": "initial"},
    )
    assert event["event_type"] == "submitted"
    event_query, event_args = conn.fetchrow_calls[1]
    assert "INSERT INTO tenant_request_events" in event_query
    assert json.dumps({"note": "initial"}) in event_args


@pytest.mark.asyncio
async def test_get_request_by_id_found_and_not_found():
    conn = _FakeConn(row={"id": REQUEST_ID, "tenant_first_name": "Alice", "unit_code": "A-101"})
    repo = TenantRequestsRepository(db_connection=conn)

    found = await repo.get_request_by_id(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert found["unit_code"] == "A-101"
    query, args = conn.fetchrow_calls[0]
    assert "FROM tenant_requests tr" in query
    assert "JOIN units u" in query
    assert args == (ORG_ID, REQUEST_ID)

    conn.row = None
    missing = await repo.get_request_by_id(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_documents_and_events():
    conn = _FakeConn(rows=[{"id": DOCUMENT_ID, "document_type": "id_proof"}])
    repo = TenantRequestsRepository(db_connection=conn)

    documents = await repo.list_documents(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert len(documents) == 1
    doc_query, _ = conn.fetch_calls[0]
    assert "FROM tenant_request_documents" in doc_query
    assert "ORDER BY document_type" in doc_query

    conn.rows = [{"id": "evt-1", "event_type": "submitted"}]
    events = await repo.list_events(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
    )
    assert events[0]["event_type"] == "submitted"
    event_query, _ = conn.fetch_calls[1]
    assert "FROM tenant_request_events" in event_query
    assert "ORDER BY occurred_at ASC" in event_query


@pytest.mark.asyncio
async def test_get_document_by_id():
    conn = _FakeConn(row={"id": DOCUMENT_ID, "status": "pending"})
    repo = TenantRequestsRepository(db_connection=conn)

    found = await repo.get_document_by_id(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOCUMENT_ID,
    )
    assert found["status"] == "pending"

    conn.row = None
    missing = await repo.get_document_by_id(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOCUMENT_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_update_document_reupload():
    conn = _FakeConn(row={"id": DOCUMENT_ID, "status": "pending"})
    repo = TenantRequestsRepository(db_connection=conn)

    updated = await repo.update_document_reupload(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOCUMENT_ID,
        file_path="/files/new.pdf",
        file_name="new.pdf",
    )
    assert updated["status"] == "pending"
    query, _ = conn.fetchrow_calls[0]
    assert "status = 'pending'::tenant_request_document_status" in query


@pytest.mark.asyncio
async def test_verify_and_reject_document():
    conn = _FakeConn(row={"id": DOCUMENT_ID, "status": "verified"})
    repo = TenantRequestsRepository(db_connection=conn)

    verified = await repo.verify_document(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOCUMENT_ID,
        verified_by_user_id=USER_ID,
    )
    assert verified["status"] == "verified"
    verify_query, _ = conn.fetchrow_calls[0]
    assert "status = 'verified'::tenant_request_document_status" in verify_query

    conn.row = {"id": DOCUMENT_ID, "status": "rejected"}
    rejected = await repo.reject_document(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        document_id=DOCUMENT_ID,
        verified_by_user_id=USER_ID,
        rejection_reason="blurry image",
    )
    assert rejected["status"] == "rejected"
    reject_query, reject_args = conn.fetchrow_calls[1]
    assert "status = 'rejected'::tenant_request_document_status" in reject_query
    assert "blurry image" in reject_args


@pytest.mark.asyncio
async def test_update_request_status_with_optional_fields():
    conn = _FakeConn(row={"id": REQUEST_ID, "status": TenantRequestStatus.APPROVED.value})
    repo = TenantRequestsRepository(db_connection=conn)

    updated = await repo.update_request_status(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        status=TenantRequestStatus.APPROVED.value,
        approved_by_user_id=USER_ID,
        approved_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
        admin_notes="approved after review",
    )
    assert updated["status"] == TenantRequestStatus.APPROVED.value
    query, args = conn.fetchrow_calls[0]
    assert "approved_by_user_id" in query
    assert "admin_notes" in query
    assert args[0] == ORG_ID
    assert args[1] == REQUEST_ID

    conn.row = None
    missing = await repo.update_request_status(
        organization_id=ORG_ID,
        tenant_request_id=REQUEST_ID,
        status=TenantRequestStatus.CANCELLED.value,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_for_owner_with_and_without_unit_filter():
    conn = _FakeConn(val=2, rows=[{"id": REQUEST_ID}, {"id": "req-2"}])
    repo = TenantRequestsRepository(db_connection=conn)

    rows, total = await repo.list_for_owner(
        organization_id=ORG_ID,
        owner_contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        limit=10,
        offset=0,
    )
    assert len(rows) == 2
    assert total == 2
    count_query, count_args = conn.fetchval_calls[0]
    assert "tr.submitted_by_contact_id = $2::uuid" in count_query
    assert "tr.unit_id = $3::uuid" in count_query
    assert count_args[:3] == (ORG_ID, CONTACT_ID, UNIT_ID)

    conn.fetchval_calls.clear()
    conn.fetch_calls.clear()
    conn.val = 1
    conn.rows = [{"id": REQUEST_ID}]
    rows, total = await repo.list_for_owner(
        organization_id=ORG_ID,
        owner_contact_id=CONTACT_ID,
        unit_id=None,
        limit=5,
        offset=0,
    )
    assert len(rows) == 1
    assert total == 1
    count_query, _ = conn.fetchval_calls[0]
    assert "tr.unit_id" not in count_query.split("AND tr.submitted_by_contact_id")[1]


@pytest.mark.asyncio
async def test_list_for_admin_applies_filters():
    conn = _FakeConn(val=1, rows=[{"id": REQUEST_ID, "documents_total_count": 2}])
    repo = TenantRequestsRepository(db_connection=conn)

    rows, total = await repo.list_for_admin(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        statuses=[TenantRequestStatus.PENDING_REVIEW.value],
        search="Alice",
        unit_id=UNIT_ID,
        limit=20,
        offset=0,
    )

    assert total == 1
    assert rows[0]["documents_total_count"] == 2
    count_query, count_args = conn.fetchval_calls[0]
    assert "tr.status = ANY" in count_query
    assert "ILIKE" in count_query
    assert "tr.unit_id" in count_query
    assert count_args[1] == PROJECT_ID

    list_query, _ = conn.fetch_calls[0]
    assert "documents_verified_count" in list_query


@pytest.mark.asyncio
async def test_get_summary_counts_with_and_without_row():
    conn = _FakeConn(
        row={
            "pending_review": 3,
            "awaiting_resubmission": 1,
            "ready_to_approve": 2,
            "approved_this_month": 4,
        }
    )
    repo = TenantRequestsRepository(db_connection=conn)

    summary = await repo.get_summary_counts(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )
    assert summary == {
        "pending_review": 3,
        "awaiting_resubmission": 1,
        "ready_to_approve": 2,
        "approved_this_month": 4,
    }
    query, args = conn.fetchrow_calls[0]
    assert "COUNT(*) FILTER" in query
    assert args[0] == ORG_ID
    assert args[1] == PROJECT_ID

    conn.row = None
    empty = await repo.get_summary_counts(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )
    assert empty == {
        "pending_review": 0,
        "awaiting_resubmission": 0,
        "ready_to_approve": 0,
        "approved_this_month": 0,
    }


@pytest.mark.asyncio
async def test_find_active_approved_for_unit():
    conn = _FakeConn(
        row={
            "id": REQUEST_ID,
            "tenant_contact_id": CONTACT_ID,
            "contact_unit_id": "cc0e8400-e29b-41d4-a716-446655440007",
        }
    )
    repo = TenantRequestsRepository(db_connection=conn)

    found = await repo.find_active_approved_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
    )
    assert found["id"] == REQUEST_ID
    query, args = conn.fetchrow_calls[0]
    assert "superseded_at IS NULL" in query
    assert args[2] == TenantRequestStatus.APPROVED.value

    conn.row = None
    missing = await repo.find_active_approved_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
    )
    assert missing is None
