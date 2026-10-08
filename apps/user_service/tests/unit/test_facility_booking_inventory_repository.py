"""Unit tests for FacilityBookingInventoryRepository."""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

import pytest

from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"
ROW_ID = "55555555-5555-5555-5555-555555555555"

_WEEK_HOURS = [{"open": 360, "close": 1320, "closed": False} for _ in range(7)]


class _FakeConn:
    """Capture SQL issued by the repository."""

    def __init__(
        self,
        row: dict | None = None,
        *,
        fetch_rows: list[dict] | None = None,
        fetchrow: dict | None = None,
        fetchrow_missing: bool = False,
        fetchval: int | None = None,
        execute_result: str = "DELETE 1",
    ) -> None:
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self._row = row or {
            "id": ROW_ID,
            "facility_id": FACILITY_ID,
            "closed_on": date(2026, 12, 25),
            "reason": "Holiday",
            "created_at": None,
        }
        self._fetch_rows = fetch_rows
        self._fetchrow = fetchrow
        self._fetchrow_missing = fetchrow_missing
        self._fetchval = fetchval
        self._execute_result = execute_result

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if self._fetch_rows is not None:
            return self._fetch_rows
        return [self._row]

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if self._fetchrow_missing:
            return None
        return self._fetchrow if self._fetchrow is not None else self._row

    async def fetchval(self, query: str, *args):
        self.fetchval_calls.append((query, args))
        return self._fetchval

    async def execute(self, query: str, *args):
        self.execute_calls.append((query, args))
        return self._execute_result


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

    query, args = conn.fetch_calls[0]
    assert "INSERT INTO facility_schedule_periods" in query
    assert "::jsonb" in query
    assert "::date" in query
    assert "::uuid" in query
    assert date(2026, 11, 1) in args
    assert date(2026, 11, 30) in args


@pytest.mark.asyncio
async def test_insert_unit_coerces_uuid_fields_to_strings() -> None:
    """asyncpg UUID columns are serialized before response-model validation."""
    unit_id = UUID("77777777-7777-7777-7777-777777777777")
    conn = _FakeConn(
        row={
            "id": unit_id,
            "facility_id": UUID(FACILITY_ID),
            "name": "Main space",
            "tower_id": None,
            "floor_id": None,
            "room_type": None,
            "features": [],
            "sort_order": 0,
            "active": True,
            "created_at": None,
            "updated_at": None,
        }
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.insert(
        "facility_booking_units",
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        data={"name": "Main space"},
    )

    assert row["id"] == str(unit_id)
    assert row["facility_id"] == FACILITY_ID
    assert isinstance(row["id"], str)


@pytest.mark.asyncio
async def test_list_rows_orders_and_scopes_by_facility() -> None:
    """list_rows selects facility-scoped rows with table-specific ordering."""
    conn = _FakeConn(fetch_rows=[{"id": ROW_ID, "facility_id": FACILITY_ID, "name": "Court A"}])
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    rows = await repo.list_rows(
        "facility_booking_units",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
    )

    assert rows[0]["name"] == "Court A"
    query, args = conn.fetch_calls[0]
    assert "FROM facility_booking_units" in query
    assert "ORDER BY sort_order, created_at" in query
    assert args == (ORG_ID, FACILITY_ID)


@pytest.mark.asyncio
async def test_list_rows_applies_active_from_filter_for_closures() -> None:
    """Historical inventory queries append active_from when supported."""
    active_from = date(2026, 1, 1)
    conn = _FakeConn()
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    await repo.list_rows(
        "facility_closures",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        active_from=active_from,
    )

    query, args = conn.fetch_calls[0]
    assert "closed_on >= $3::date" in query
    assert args == (ORG_ID, FACILITY_ID, active_from)


@pytest.mark.asyncio
async def test_list_rows_decodes_schedule_hours_json() -> None:
    """Schedule period rows decode jsonb hours from text."""
    conn = _FakeConn(
        fetch_rows=[
            {
                "id": ROW_ID,
                "facility_id": FACILITY_ID,
                "name": "Winter",
                "starts_on": date(2026, 11, 1),
                "ends_on": date(2026, 11, 30),
                "hours": json.dumps(_WEEK_HOURS),
            }
        ]
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    rows = await repo.list_rows(
        "facility_schedule_periods",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
    )

    assert rows[0]["hours"][0]["open"] == 360


@pytest.mark.asyncio
async def test_get_returns_serialized_row() -> None:
    """get fetches one row and normalizes through the response model."""
    conn = _FakeConn(
        fetchrow={
            "id": ROW_ID,
            "facility_id": FACILITY_ID,
            "name": "Lane 1",
            "tower_id": None,
            "floor_id": None,
            "room_type": None,
            "features": [],
            "sort_order": 0,
            "active": True,
            "created_at": None,
            "updated_at": None,
        }
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.get(
        "facility_booking_units",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        row_id=ROW_ID,
    )

    assert row is not None
    assert row["name"] == "Lane 1"
    assert conn.fetchrow_calls


@pytest.mark.asyncio
async def test_get_returns_none_when_missing() -> None:
    """get returns None when no row matches."""
    conn = _FakeConn(fetchrow_missing=True)
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.get(
        "facility_closures",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        row_id=ROW_ID,
    )

    assert row is None


@pytest.mark.asyncio
async def test_update_returns_serialized_row() -> None:
    """update applies changes and serializes the returned row."""
    conn = _FakeConn(
        fetchrow={
            "id": ROW_ID,
            "facility_id": FACILITY_ID,
            "closed_on": date(2026, 12, 26),
            "reason": "Updated",
            "created_at": None,
            "updated_at": None,
        }
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.update(
        "facility_closures",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        row_id=ROW_ID,
        update_data={"reason": "Updated"},
    )

    assert row is not None
    assert row["reason"] == "Updated"
    query, _args = conn.fetchrow_calls[0]
    assert "UPDATE facility_closures" in query


@pytest.mark.asyncio
async def test_update_schedule_coerces_iso_date_strings() -> None:
    """PATCH schedule payloads use JSON date strings that must bind as date objects."""
    conn = _FakeConn(
        fetchrow={
            "id": ROW_ID,
            "facility_id": FACILITY_ID,
            "name": "Summer season",
            "starts_on": date(2027, 4, 1),
            "ends_on": date(2027, 7, 1),
            "hours": _WEEK_HOURS,
            "created_at": None,
        }
    )
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    row = await repo.update(
        "facility_schedule_periods",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        row_id=ROW_ID,
        update_data={
            "name": "Summer season",
            "starts_on": "2027-04-01",
            "ends_on": "2027-07-01",
            "hours": _WEEK_HOURS,
        },
    )

    assert row is not None
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE facility_schedule_periods" in query
    assert "::date" in query
    assert date(2027, 4, 1) in args
    assert date(2027, 7, 1) in args


@pytest.mark.asyncio
async def test_delete_reports_affected_rows() -> None:
    """delete returns True when one row is removed."""
    conn = _FakeConn(execute_result="DELETE 1")
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    deleted = await repo.delete(
        "facility_closures",
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        row_id=ROW_ID,
    )

    assert deleted is True
    query, args = conn.execute_calls[0]
    assert "DELETE FROM facility_closures" in query
    assert args == (ROW_ID, ORG_ID, FACILITY_ID)


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing() -> None:
    """delete returns False when no row matched."""
    conn = _FakeConn(execute_result="DELETE 0")
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    assert (
        await repo.delete(
            "facility_closures",
            organization_id=ORG_ID,
            facility_id=FACILITY_ID,
            row_id=ROW_ID,
        )
        is False
    )


@pytest.mark.asyncio
async def test_count_units_returns_integer() -> None:
    """count_units coerces fetchval to int."""
    conn = _FakeConn(fetchval=3)
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    assert await repo.count_units(organization_id=ORG_ID, facility_id=FACILITY_ID) == 3


@pytest.mark.asyncio
async def test_load_all_fetches_every_inventory_table() -> None:
    """load_all loads each inventory table for snapshot assembly."""
    conn = _FakeConn(fetch_rows=[])
    repo = FacilityBookingInventoryRepository(db_connection=conn)

    loaded = await repo.load_all(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        active_from=date(2026, 6, 1),
    )

    assert set(loaded) == {
        "facility_booking_units",
        "facility_schedule_periods",
        "facility_slot_blocks",
        "facility_closures",
        "facility_maintenance_windows",
    }
    assert len(conn.fetch_calls) == 5
    assert all(
        "ends_on >= $3::date" in q
        or "closed_on >= $3::date" in q
        or "on_date >= $3::date" in q
        or "COALESCE" in q
        or "facility_booking_units" in q
        for q, _ in conn.fetch_calls
    )
