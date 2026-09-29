"""Unit tests for FacilityReservationsRepository."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
RESERVATION_ID = "44444444-4444-4444-4444-444444444444"
CONTACT_ID = "55555555-5555-5555-5555-555555555555"
USER_ID = "66666666-6666-6666-6666-666666666666"


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


def test_param_serializes_jsonb_dict() -> None:
    quote = {"total_cents": 500, "currency": "USD"}
    assert FacilityReservationsRepository._param("quote", quote) == json.dumps(quote)


def test_param_jsonb_string_and_none_unchanged() -> None:
    assert FacilityReservationsRepository._param("quote", '{"a": 1}') == '{"a": 1}'
    assert FacilityReservationsRepository._param("quote", None) is None


def test_param_non_jsonb_column() -> None:
    assert FacilityReservationsRepository._param("status", "confirmed") == "confirmed"


@pytest.mark.asyncio
async def test_lock_facility_uses_advisory_lock() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    await repo.lock_facility(FACILITY_ID)

    query, args = conn.execute_calls[0]
    assert "pg_advisory_xact_lock" in query
    assert args == (f"facility_booking:{FACILITY_ID}",)


@pytest.mark.asyncio
async def test_insert_reservation_dynamic_columns_and_casts() -> None:
    conn = _FakeConn(row={"id": RESERVATION_ID})
    repo = FacilityReservationsRepository(db_connection=conn)
    quote = {"line_items": [{"label": "Court", "amount": 100}]}

    result = await repo.insert_reservation(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "facility_id": FACILITY_ID,
            "status": "confirmed",
            "booked_by_actor": "resident",
            "quote": quote,
            "local_date": date(2026, 6, 1),
        }
    )

    assert result["id"] == RESERVATION_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facility_reservations" in query
    assert "::jsonb" in query
    assert "::facility_reservation_status" in query
    assert "::facility_reservation_actor_type" in query
    assert json.dumps(quote) in args


@pytest.mark.asyncio
async def test_update_reservation_success_and_no_row() -> None:
    conn = _FakeConn(execute_result="UPDATE 1")
    repo = FacilityReservationsRepository(db_connection=conn)

    updated = await repo.update_reservation(
        organization_id=ORG_ID,
        reservation_id=RESERVATION_ID,
        update_data={"status": "cancelled", "cancel_info": {"reason": "rain"}},
    )
    assert updated is True
    query, args = conn.execute_calls[0]
    assert "UPDATE facility_reservations" in query
    assert "::jsonb" in query
    assert "updated_at = NOW()" in query
    assert args[-2:] == (RESERVATION_ID, ORG_ID)

    conn.execute_result = "UPDATE 0"
    not_updated = await repo.update_reservation(
        organization_id=ORG_ID,
        reservation_id=RESERVATION_ID,
        update_data={"notes": "n/a"},
    )
    assert not_updated is False


@pytest.mark.asyncio
async def test_get_reservation_decodes_jsonb_and_for_update() -> None:
    conn = _FakeConn(
        row={
            "id": RESERVATION_ID,
            "quote": json.dumps({"total": 10}),
            "cancel_info": None,
        }
    )
    repo = FacilityReservationsRepository(db_connection=conn)

    found = await repo.get_reservation(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
    )
    assert found is not None
    assert found["quote"] == {"total": 10}
    query, args = conn.fetchrow_calls[0]
    assert "FOR UPDATE" not in query
    assert args == (RESERVATION_ID, ORG_ID, PROJECT_ID)

    await repo.get_reservation(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
        for_update=True,
    )
    assert "FOR UPDATE OF r" in conn.fetchrow_calls[1][0]

    conn.row = None
    missing = await repo.get_reservation(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_insert_participants_empty_skips() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    with patch.object(
        FacilityReservationsRepository,
        "bulk_insert_returning",
        new=AsyncMock(),
    ) as bulk:
        await repo.insert_participants(
            organization_id=ORG_ID,
            reservation_id=RESERVATION_ID,
            participants=[],
        )
    bulk.assert_not_called()


@pytest.mark.asyncio
async def test_insert_participants_bulk_insert() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    with patch.object(
        FacilityReservationsRepository,
        "bulk_insert_returning",
        new=AsyncMock(return_value=[{"id": "p1"}]),
    ) as bulk:
        await repo.insert_participants(
            organization_id=ORG_ID,
            reservation_id=RESERVATION_ID,
            participants=[
                {"kind": "host", "contact_id": CONTACT_ID, "name": "Alex Host"},
                {"kind": "guest", "name": "Guest One"},
            ],
        )

    bulk.assert_awaited_once()
    kwargs = bulk.call_args.kwargs
    assert kwargs["table"] == "facility_reservation_participants"
    assert len(kwargs["rows"]) == 2
    assert kwargs["rows"][0]["sort_order"] == 0
    assert kwargs["rows"][1]["sort_order"] == 1
    assert kwargs["rows"][1]["contact_id"] is None


@pytest.mark.asyncio
async def test_participants_by_reservation_empty_and_grouped() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    assert await repo.participants_by_reservation([]) == {}

    conn.rows = [
        {
            "reservation_id": RESERVATION_ID,
            "kind": "host",
            "contact_id": CONTACT_ID,
            "name": "Alex",
        },
        {
            "reservation_id": RESERVATION_ID,
            "kind": "guest",
            "contact_id": None,
            "name": "Sam",
        },
    ]
    grouped = await repo.participants_by_reservation([RESERVATION_ID])
    assert len(grouped[RESERVATION_ID]) == 2
    query, args = conn.fetch_calls[0]
    assert "facility_reservation_participants" in query
    assert args == ([RESERVATION_ID],)


@pytest.mark.asyncio
async def test_insert_event_uses_bulk_insert_with_jsonb() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    with patch.object(
        FacilityReservationsRepository,
        "bulk_insert_returning",
        new=AsyncMock(return_value=[{"id": "ev1"}]),
    ) as bulk:
        await repo.insert_event(
            organization_id=ORG_ID,
            reservation_id=RESERVATION_ID,
            event_type="created",
            actor_type="resident",
            message="Booked",
            actor_user_id=USER_ID,
            payload={"source": "app"},
        )

    kwargs = bulk.call_args.kwargs
    assert kwargs["table"] == "facility_reservation_events"
    assert kwargs["jsonb_columns"] == frozenset({"payload"})
    assert kwargs["rows"][0]["payload"] == {"source": "app"}


@pytest.mark.asyncio
async def test_list_events_empty_and_rows() -> None:
    conn = _FakeConn()
    repo = FacilityReservationsRepository(db_connection=conn)

    assert await repo.list_events([]) == []

    conn.rows = [{"id": "ev1", "message": "Created"}]
    events = await repo.list_events([RESERVATION_ID])
    assert events[0]["message"] == "Created"


@pytest.mark.asyncio
async def test_list_active_in_range_decodes_jsonb() -> None:
    conn = _FakeConn(rows=[{"id": RESERVATION_ID, "quote": json.dumps({"total": 1})}])
    repo = FacilityReservationsRepository(db_connection=conn)
    start = date(2026, 6, 1)
    end = date(2026, 6, 30)

    rows = await repo.list_active_in_range(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        start=start,
        end=end,
    )

    assert rows[0]["quote"] == {"total": 1}
    query, args = conn.fetch_calls[0]
    assert "r.local_date <= $5::date" in query
    assert args[0:2] == (ORG_ID, FACILITY_ID)
    assert args[3:5] == (start, end)


@pytest.mark.asyncio
async def test_list_weekly_pool() -> None:
    conn = _FakeConn(rows=[{"id": RESERVATION_ID, "quote": "{}"}])
    repo = FacilityReservationsRepository(db_connection=conn)
    week_start = date(2026, 6, 2)
    week_end = date(2026, 6, 8)

    rows = await repo.list_weekly_pool(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        contact_id=CONTACT_ID,
        week_start=week_start,
        week_end=week_end,
    )

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "facility_reservation_participants" in query
    assert args[3:6] == (CONTACT_ID, week_start, week_end)


@pytest.mark.asyncio
async def test_list_reservations_filters_pagination_and_search() -> None:
    conn = _FakeConn(
        rows=[{"id": RESERVATION_ID, "quote": json.dumps({})}],
        fetchval_result=42,
    )
    repo = FacilityReservationsRepository(db_connection=conn)

    items, total = await repo.list_reservations(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        statuses=["confirmed"],
        exclude_statuses=["cancelled"],
        contact_id=CONTACT_ID,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        search="  pool  ",
        descending=True,
        page=2,
        page_size=10,
    )

    assert total == 42
    assert len(items) == 1
    count_query, count_args = conn.fetchval_calls[0]
    assert "COUNT(*)" in count_query
    assert "ILIKE" in count_query
    assert "%pool%" in count_args
    list_query, list_args = conn.fetch_calls[0]
    assert "ORDER BY r.local_date DESC" in list_query
    assert list_args[-2:] == (10, 10)


@pytest.mark.asyncio
async def test_count_upcoming_active_with_optional_unit() -> None:
    conn = _FakeConn(fetchval_result=7)
    repo = FacilityReservationsRepository(db_connection=conn)

    count = await repo.count_upcoming_active(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        from_date=date(2026, 6, 1),
    )
    assert count == 7
    assert "unit_id" not in conn.fetchval_calls[0][0]

    await repo.count_upcoming_active(
        organization_id=ORG_ID,
        facility_id=FACILITY_ID,
        from_date=date(2026, 6, 1),
        unit_id="77777777-7777-7777-7777-777777777777",
    )
    assert "unit_id = $5::uuid" in conn.fetchval_calls[1][0]
