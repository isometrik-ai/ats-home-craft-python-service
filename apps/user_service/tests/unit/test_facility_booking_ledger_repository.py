"""Unit tests for FacilityBookingLedgerRepository."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.facility_booking_ledger_repository import (
    FacilityBookingLedgerRepository,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"
RESERVATION_ID = "44444444-4444-4444-4444-444444444444"
INVOICE_ID = "55555555-5555-5555-5555-555555555555"
ENTRY_ID = "66666666-6666-6666-6666-666666666666"
USER_ID = "77777777-7777-7777-7777-777777777777"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        row: dict | None = None,
        execute_result: str = "UPDATE 1",
        fetchval_result: int = 0,
    ) -> None:
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
        self.fetchval_result = fetchval_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query: str, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_insert_ledger_entry() -> None:
    conn = _FakeConn(row={"id": ENTRY_ID, "amount": 500, "entry_type": "charge"})
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    inserted = await repo.insert(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "contact_id": CONTACT_ID,
            "reservation_id": RESERVATION_ID,
            "invoice_id": None,
            "entry_type": "charge",
            "description": "Court booking",
            "amount": 500,
            "method": None,
            "posted_at": None,
            "created_by_user_id": USER_ID,
        }
    )

    assert inserted["id"] == ENTRY_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facility_booking_ledger_entries" in query
    assert "::facility_booking_ledger_type" in query
    assert "COALESCE($10, now())" in query
    assert args[0:3] == (ORG_ID, PROJECT_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_list_for_reservation() -> None:
    conn = _FakeConn(rows=[{"id": ENTRY_ID, "facility_name": "Tennis Court"}])
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    rows = await repo.list_for_reservation(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
    )

    assert rows[0]["facility_name"] == "Tennis Court"
    query, args = conn.fetch_calls[0]
    assert "e.reservation_id = $3::uuid" in query
    assert args == (ORG_ID, PROJECT_ID, RESERVATION_ID)


@pytest.mark.asyncio
async def test_list_for_contact_paginated() -> None:
    conn = _FakeConn(rows=[{"id": ENTRY_ID}], fetchval_result=3)
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    rows, total = await repo.list_for_contact(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        page=2,
        page_size=10,
    )

    assert total == 3
    assert len(rows) == 1
    assert conn.fetchval_calls[0][0].startswith("SELECT COUNT(*)")
    list_query, list_args = conn.fetch_calls[0]
    assert "ORDER BY e.posted_at DESC" in list_query
    assert list_args[-2:] == (10, 10)


@pytest.mark.asyncio
async def test_list_project_with_optional_contact_filter() -> None:
    conn = _FakeConn(rows=[], fetchval_result=0)
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    _, total = await repo.list_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        page=1,
        page_size=20,
    )
    assert total == 0
    count_query = conn.fetchval_calls[0][0]
    assert "e.contact_id = $3::uuid" in count_query
    list_query, list_args = conn.fetch_calls[0]
    assert "OFFSET $4 LIMIT $5" in list_query
    assert list_args == (ORG_ID, PROJECT_ID, CONTACT_ID, 0, 20)


@pytest.mark.asyncio
async def test_contact_balance() -> None:
    conn = _FakeConn(fetchval_result=1500)
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    balance = await repo.contact_balance(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
    )

    assert balance == 1500
    query, args = conn.fetchval_calls[0]
    assert "COALESCE(SUM(amount), 0)" in query
    assert args == (ORG_ID, PROJECT_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_list_unbilled_and_invoiceable() -> None:
    conn = _FakeConn(rows=[{"id": ENTRY_ID, "invoice_id": None}])
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    unbilled = await repo.list_unbilled(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(unbilled) == 1
    unbilled_query, unbilled_args = conn.fetch_calls[0]
    assert "e.invoice_id IS NULL" in unbilled_query
    assert len(unbilled_args) == 3

    invoiceable = await repo.list_invoiceable(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_ids=[CONTACT_ID],
    )
    assert len(invoiceable) == 1
    inv_query, inv_args = conn.fetch_calls[1]
    assert "e.amount > 0" in inv_query
    assert inv_args[3] == [CONTACT_ID]


@pytest.mark.asyncio
async def test_attach_invoice_skips_empty_and_updates() -> None:
    conn = _FakeConn()
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    await repo.attach_invoice(entry_ids=[], invoice_id=INVOICE_ID)
    assert conn.execute_calls == []

    await repo.attach_invoice(entry_ids=[ENTRY_ID], invoice_id=INVOICE_ID)
    query, args = conn.execute_calls[0]
    assert "SET invoice_id = $2::uuid" in query
    assert args == ([ENTRY_ID], INVOICE_ID)


@pytest.mark.asyncio
async def test_list_payments_for_month() -> None:
    conn = _FakeConn(rows=[{"method": "cash", "amount": 200}])
    repo = FacilityBookingLedgerRepository(db_connection=conn)

    payments = await repo.list_payments_for_month(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        year=2026,
        month=6,
    )

    assert payments[0]["method"] == "cash"
    query, args = conn.fetch_calls[0]
    assert "entry_type = 'payment'" in query
    assert "EXTRACT(MONTH FROM posted_at)" in query
    assert args == (ORG_ID, PROJECT_ID, 2026, 6)
