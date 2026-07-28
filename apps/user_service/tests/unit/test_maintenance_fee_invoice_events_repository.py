"""Unit tests for MaintenanceFeeInvoiceEventsRepository with fake connection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.maintenance_fee_invoice_events_repository import (
    MaintenanceFeeInvoiceEventsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
INVOICE_ID = "660e8400-e29b-41d4-a716-446655440001"
EVENT_ID = "770e8400-e29b-41d4-a716-446655440002"
USER_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, row=None):
        self.row = row
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_insert_invoice_event():
    occurred_at = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    conn = _FakeConn(
        row={
            "id": EVENT_ID,
            "organization_id": ORG_ID,
            "invoice_id": INVOICE_ID,
            "event_type": "issued",
            "occurred_at": occurred_at,
            "actor_user_id": USER_ID,
            "notes": "Invoice issued",
            "metadata": {"source": "system"},
        }
    )
    repo = MaintenanceFeeInvoiceEventsRepository(db_connection=conn)

    row = await repo.insert(
        data={
            "organization_id": ORG_ID,
            "invoice_id": INVOICE_ID,
            "event_type": "issued",
            "occurred_at": occurred_at,
            "actor_user_id": USER_ID,
            "notes": "Invoice issued",
            "metadata": {"source": "system"},
        }
    )

    assert row["id"] == EVENT_ID
    assert row["event_type"] == "issued"
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO maintenance_fee_invoice_events" in query
    assert "::maintenance_fee_invoice_event_type" in query
    assert "::jsonb" in query
    assert args[0] == ORG_ID
    assert args[1] == INVOICE_ID
    assert args[2] == "issued"
    assert args[3] == occurred_at
    assert args[4] == USER_ID
    assert args[5] == "Invoice issued"
    assert args[6] == {"source": "system"}


@pytest.mark.asyncio
async def test_insert_invoice_event_defaults_metadata():
    conn = _FakeConn(row={"id": EVENT_ID, "event_type": "paid", "metadata": {}})
    repo = MaintenanceFeeInvoiceEventsRepository(db_connection=conn)

    row = await repo.insert(
        data={
            "organization_id": ORG_ID,
            "invoice_id": INVOICE_ID,
            "event_type": "paid",
        }
    )

    assert row["event_type"] == "paid"
    _, args = conn.fetchrow_calls[0]
    assert args[3] is None
    assert args[4] is None
    assert args[5] is None
    assert args[6] == {}
