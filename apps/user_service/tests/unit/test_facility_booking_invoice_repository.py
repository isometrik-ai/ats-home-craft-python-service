"""Unit tests for FacilityBookingInvoiceRepository."""

from __future__ import annotations

import json
from datetime import date

import pytest

from apps.user_service.app.db.repositories.facility_booking_invoice_repository import (
    FacilityBookingInvoiceRepository,
    _decode_lines,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"
INVOICE_ID = "44444444-4444-4444-4444-444444444444"
USER_ID = "55555555-5555-5555-5555-555555555555"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        row: dict | None = None,
        fetchval_result: int = 0,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.fetchval_result = fetchval_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result


def test_decode_lines_parses_json_string() -> None:
    row = {"lines": json.dumps([{"description": "Court fee", "amount": 500}])}
    decoded = _decode_lines(row)
    assert decoded["lines"][0]["amount"] == 500


def test_decode_lines_non_string_unchanged() -> None:
    row = {"lines": [{"amount": 1}]}
    assert _decode_lines(row)["lines"] == [{"amount": 1}]


@pytest.mark.asyncio
async def test_insert_invoice_serializes_lines_and_defaults_status() -> None:
    lines = [{"description": "Monthly dues", "amount": 1000}]
    conn = _FakeConn(
        row={
            "id": INVOICE_ID,
            "number": "INV-2026-001",
            "status": "issued",
            "lines": json.dumps(lines),
            "total": 1000,
        }
    )
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    inserted = await repo.insert(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-2026-001",
            "lines": lines,
            "total": 1000,
            "period_label": "June 2026",
            "due_date": date(2026, 6, 30),
            "created_by_user_id": USER_ID,
        }
    )

    assert inserted["lines"] == lines
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facility_booking_invoices" in query
    assert "::jsonb" in query
    assert "::facility_booking_invoice_status" in query
    assert args[4] == "issued"
    assert json.dumps(lines) in args


@pytest.mark.asyncio
async def test_count_for_year() -> None:
    conn = _FakeConn(fetchval_result=12)
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    count = await repo.count_for_year(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        year=2026,
    )

    assert count == 12
    query, args = conn.fetchval_calls[0]
    assert "EXTRACT(YEAR FROM created_at)" in query
    assert args == (ORG_ID, PROJECT_ID, 2026)


@pytest.mark.asyncio
async def test_get_invoice_found_and_missing() -> None:
    conn = _FakeConn(
        row={
            "id": INVOICE_ID,
            "contact_name": "Jane Doe",
            "lines": json.dumps([]),
        }
    )
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    found = await repo.get(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id=INVOICE_ID,
    )
    assert found is not None
    assert found["contact_name"] == "Jane Doe"
    assert "facility_booking_invoices" in conn.fetchrow_calls[0][0]

    conn.row = None
    missing = await repo.get(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id=INVOICE_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_project_with_filters_and_pagination() -> None:
    conn = _FakeConn(
        rows=[{"id": INVOICE_ID, "lines": json.dumps([])}],
        fetchval_result=1,
    )
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    items, total = await repo.list_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        status="issued",
        page=2,
        page_size=25,
    )

    assert total == 1
    assert len(items) == 1
    count_query, count_args = conn.fetchval_calls[0]
    assert "i.contact_id = $3::uuid" in count_query
    assert "i.status = $4::facility_booking_invoice_status" in count_query
    assert count_args == (ORG_ID, PROJECT_ID, CONTACT_ID, "issued")
    list_query, list_args = conn.fetch_calls[0]
    assert "ORDER BY i.created_at DESC" in list_query
    assert list_args[-2:] == (25, 25)


@pytest.mark.asyncio
async def test_list_project_minimal_filters() -> None:
    conn = _FakeConn(rows=[], fetchval_result=0)
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    items, total = await repo.list_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert items == []
    assert total == 0
    count_query, _ = conn.fetchval_calls[0]
    assert "contact_id" not in count_query
    list_query, list_args = conn.fetch_calls[0]
    assert "OFFSET $3 LIMIT $4" in list_query
    assert list_args[-2:] == (0, 50)


@pytest.mark.asyncio
async def test_mark_paid_returns_decoded_row() -> None:
    conn = _FakeConn(
        row={
            "id": INVOICE_ID,
            "status": "paid",
            "lines": json.dumps([{"amount": 100}]),
        }
    )
    repo = FacilityBookingInvoiceRepository(db_connection=conn)

    paid = await repo.mark_paid(
        invoice_id=INVOICE_ID,
        update={
            "paid_at": "2026-06-15T10:00:00Z",
            "paid_via": "cash",
            "payment_ref": "RCPT-1",
            "collected_by": USER_ID,
            "notes": "Paid in office",
        },
    )

    assert paid["status"] == "paid"
    assert paid["lines"][0]["amount"] == 100
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE facility_booking_invoices" in query
    assert "status = 'paid'" in query
    assert args[0] == INVOICE_ID
