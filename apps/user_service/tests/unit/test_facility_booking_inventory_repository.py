"""Unit tests for FacilityBookingInventoryRepository."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"


class _FakeConn:
    """Capture SQL issued by the repository."""

    def __init__(self, row: dict | None = None) -> None:
        self.fetch_calls: list[tuple[str, tuple]] = []
        self._row = row or {
            "id": "55555555-5555-5555-5555-555555555555",
            "facility_id": FACILITY_ID,
            "closed_on": date(2026, 12, 25),
            "reason": "Holiday",
            "created_at": None,
        }

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        return [self._row]


@pytest.mark.asyncio
async def test_insert_closure_uses_typed_placeholders() -> None:
    """Inventory inserts cast uuid/date/jsonb columns explicitly."""
    conn = _FakeConn()
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.insert(
        "facility_closures",
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        data={
            "closed_on": "2026-12-25",
            "reason": "Christmas Holiday Closure",
            "created_by_user_id": USER_ID,
        },
    )

    assert row["reason"] == "Holiday"
    query, _args = conn.fetch_calls[0]
    assert "INSERT INTO facility_closures" in query
    assert "::uuid" in query
    assert "::date" in query
    assert "::jsonb" not in query


@pytest.mark.asyncio
async def test_insert_schedule_uses_jsonb_cast() -> None:
    """Schedule periods serialize hours with an explicit jsonb cast."""
    conn = _FakeConn(
        row={
            "id": "66666666-6666-6666-6666-666666666666",
            "facility_id": FACILITY_ID,
            "name": "Winter",
            "starts_on": date(2026, 11, 1),
            "ends_on": date(2026, 11, 30),
            "hours": [{"open": 360, "close": 1320, "closed": False}] * 7,
            "created_at": None,
        }
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    await repo.insert(
        "facility_schedule_periods",
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        data={
            "name": "Winter Peak Schedule",
            "starts_on": "2026-11-01",
            "ends_on": "2026-11-30",
            "hours": [{"open": 360, "close": 1320, "closed": False}] * 7,
            "created_by_user_id": USER_ID,
        },
    )

    query, _args = conn.fetch_calls[0]
    assert "INSERT INTO facility_schedule_periods" in query
    assert "::jsonb" in query
    assert "::date" in query
    assert "::uuid" in query
