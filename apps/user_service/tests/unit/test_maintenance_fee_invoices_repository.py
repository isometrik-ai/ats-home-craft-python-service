"""Unit tests for MaintenanceFeeInvoicesRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.maintenance_fee_invoices_repository import (
    MaintenanceFeeInvoicesRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
INVOICE_ID = "770e8400-e29b-41d4-a716-446655440002"
UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
CU_ID = "990e8400-e29b-41d4-a716-446655440004"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_insert_invoice():
    conn = _FakeConn(row={"id": INVOICE_ID, "status": "issued"})
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    row = await repo.insert(
        data={
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "unit_id": UNIT_ID,
            "unit_config_kind": "residential",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "due_date": "2026-02-01",
            "amount_minor": 500000,
            "currency": "INR",
            "status": "issued",
        }
    )

    assert row["id"] == INVOICE_ID
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO maintenance_fee_invoices" in query
    assert "::unit_config_kind" in query


@pytest.mark.asyncio
async def test_get_by_id():
    conn = _FakeConn(row={"id": INVOICE_ID})
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    found = await repo.get_by_id(organization_id=ORG_ID, invoice_id=INVOICE_ID)
    assert found["id"] == INVOICE_ID

    conn.row = None
    missing = await repo.get_by_id(organization_id=ORG_ID, invoice_id=INVOICE_ID)
    assert missing is None


@pytest.mark.asyncio
async def test_list_by_project_with_status():
    conn = _FakeConn(row={"total": 2}, rows=[{"id": INVOICE_ID}])
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    rows, total = await repo.list_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        status="issued",
        limit=10,
        offset=0,
    )

    assert total == 2
    assert len(rows) == 1
    count_query, _ = conn.fetchrow_calls[0]
    list_query, _ = conn.fetch_calls[0]
    assert "i.status = $3::maintenance_fee_invoice_status" in count_query
    assert "LIMIT $4" in list_query


@pytest.mark.asyncio
async def test_list_by_project_without_status():
    conn = _FakeConn(row={"total": 0}, rows=[])
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    rows, total = await repo.list_by_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        status=None,
    )

    assert total == 0
    assert rows == []


@pytest.mark.asyncio
async def test_list_by_contact_units_empty_and_populated():
    conn = _FakeConn()
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    rows, total = await repo.list_by_contact_units(
        organization_id=ORG_ID,
        contact_unit_ids=[],
    )
    assert rows == [] and total == 0

    conn.row = {"total": 1}
    conn.rows = [{"id": INVOICE_ID}]
    rows, total = await repo.list_by_contact_units(
        organization_id=ORG_ID,
        contact_unit_ids=[CU_ID],
        limit=5,
        offset=0,
    )
    assert total == 1
    assert "contact_unit_id = ANY($2::uuid[])" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_list_due_for_reminders_and_retries():
    conn = _FakeConn(rows=[{"id": INVOICE_ID}])
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    reminders = await repo.list_due_for_reminders(organization_id=ORG_ID, limit=50)
    assert len(reminders) == 1
    assert "reminder_count" in conn.fetch_calls[0][0]

    retries = await repo.list_due_for_retries(organization_id=ORG_ID, limit=50)
    assert len(retries) == 1
    assert "retry_count" in conn.fetch_calls[1][0]


@pytest.mark.asyncio
async def test_update_invoice_paths():
    conn = _FakeConn(row={"id": INVOICE_ID, "status": "paid"})
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    updated = await repo.update_invoice(
        organization_id=ORG_ID,
        invoice_id=INVOICE_ID,
        patch={
            "status": "paid",
            "amount_paid_minor": 500000,
            "metadata": {"note": "paid"},
            "ignored_field": "x",
        },
    )
    assert updated["status"] == "paid"
    query, _ = conn.fetchrow_calls[0]
    assert "status = $3::maintenance_fee_invoice_status" in query
    assert "metadata = $5::jsonb" in query

    conn.fetchrow_calls.clear()
    conn.row = {"id": INVOICE_ID}
    noop = await repo.update_invoice(
        organization_id=ORG_ID,
        invoice_id=INVOICE_ID,
        patch={},
    )
    assert noop["id"] == INVOICE_ID
    assert "maintenance_fee_invoices i" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_sum_outstanding_and_latest_fee():
    conn = _FakeConn(row={"outstanding": 150000})
    repo = MaintenanceFeeInvoicesRepository(db_connection=conn)

    outstanding = await repo.sum_outstanding_by_unit(organization_id=ORG_ID, unit_id=UNIT_ID)
    assert outstanding == 150000

    conn.row = {"amount_minor": 500000}
    fee = await repo.latest_monthly_fee_by_unit(organization_id=ORG_ID, unit_id=UNIT_ID)
    assert fee == 500000

    conn.row = None
    assert await repo.latest_monthly_fee_by_unit(organization_id=ORG_ID, unit_id=UNIT_ID) is None
