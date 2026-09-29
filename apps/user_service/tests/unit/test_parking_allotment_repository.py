"""Unit tests for ParkingAllotmentRepository with fake connection."""

from __future__ import annotations

import json
from datetime import date

import pytest

from apps.user_service.app.db.repositories.parking_allotment_repository import (
    ParkingAllotmentRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
TOWER_ID = "770e8400-e29b-41d4-a716-446655440002"
FACILITY_ID = "880e8400-e29b-41d4-a716-446655440003"
SLOT_ID = "990e8400-e29b-41d4-a716-446655440004"
UNIT_ID = "aa0e8400-e29b-41d4-a716-446655440005"
ALLOTMENT_ID = "bb0e8400-e29b-41d4-a716-446655440006"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        total: int = 0,
        rows=None,
        row=None,
        fetchval_results=None,
        fetchrow_results=None,
    ):
        self.total = total
        self.rows = rows or []
        self.row = row
        self.fetchval_results = list(fetchval_results) if fetchval_results is not None else None
        self.fetchrow_results = list(fetchrow_results) if fetchrow_results is not None else None
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        if self.fetchval_results is not None:
            return self.fetchval_results.pop(0)
        return self.total

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        if self.fetchrow_results is not None:
            return self.fetchrow_results.pop(0)
        return self.row

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_list_slots_slot_type_filter_uses_normalized_subtype_sql():
    """slot_type filter must match normalized facility_subtype (same as API response)."""
    conn = _FakeConn(total=2)
    repo = ParkingAllotmentRepository(db_connection=conn)

    rows, total = await repo.list_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_type="basement",
        page=1,
        page_size=50,
    )

    assert total == 2
    assert rows == []
    count_query, count_args = conn.fetchval_calls[0]
    list_query, list_args = conn.fetch_calls[0]
    assert "WHEN TRIM(COALESCE(f.facility_subtype, '')) = '' THEN 'open'" in count_query
    assert "basement" in count_args
    assert count_args[2] == "basement"
    assert list_args[2] == "basement"


@pytest.mark.asyncio
async def test_list_slots_search_uses_slot_code_label_sql():
    """Search must match the same slot_code_label shown in the by-slot UI."""
    conn = _FakeConn(total=1)
    repo = ParkingAllotmentRepository(db_connection=conn)

    await repo.list_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        search="LUX-A-B1-2",
        page=1,
        page_size=20,
    )

    count_query, count_args = conn.fetchval_calls[0]
    list_query, list_args = conn.fetch_calls[0]
    assert "CONCAT_WS(" in count_query
    assert "NULLIF(TRIM(COALESCE(t.code, '')), '')" in count_query
    assert "NULLIF(TRIM(COALESCE(fps.slot_code, '')), '')" in count_query
    assert "LPAD(fps.slot_number::text, 3, '0')" in count_query
    assert "CONCAT(" not in count_query
    assert count_args[2] == "%LUX-A-B1-2%"
    assert list_args[2] == "%LUX-A-B1-2%"


@pytest.mark.asyncio
async def test_scope_filter_sql_static_helper():
    sql, args, idx = ParkingAllotmentRepository._scope_filter_sql(
        tower_id=TOWER_ID,
        facility_id=FACILITY_ID,
        start_idx=3,
    )
    assert "f.tower_id = $3::uuid" in sql
    assert "fps.facility_id = $4::uuid" in sql
    assert args == [TOWER_ID, FACILITY_ID]
    assert idx == 5

    empty_sql, empty_args, empty_idx = ParkingAllotmentRepository._scope_filter_sql(
        tower_id=None,
        facility_id=None,
        start_idx=3,
    )
    assert empty_sql == ""
    assert empty_args == []
    assert empty_idx == 3


@pytest.mark.asyncio
async def test_get_summary_merges_slot_counts_and_units_short():
    conn = _FakeConn(
        fetchrow_results=[
            {
                "total_slots": 10,
                "allotted": 4,
                "free_to_allot": 5,
                "visitor_pool": 1,
                "blocked": 0,
            }
        ],
        fetchval_results=[3],
    )
    repo = ParkingAllotmentRepository(db_connection=conn)

    summary = await repo.get_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        facility_id=FACILITY_ID,
    )

    assert summary["total_slots"] == 10
    assert summary["units_short_of_entitlement"] == 3
    summary_query, summary_args = conn.fetchrow_calls[0]
    assert "f.tower_id = $3::uuid" in summary_query
    assert "fps.facility_id = $4::uuid" in summary_query
    assert summary_args[:2] == (ORG_ID, PROJECT_ID)
    units_query, units_args = conn.fetchval_calls[0]
    assert "units_short" not in units_query
    assert "unit_counts" in units_query
    assert units_args == (ORG_ID, PROJECT_ID, TOWER_ID)


@pytest.mark.asyncio
async def test_get_summary_empty_row_defaults_units_short_to_zero():
    conn = _FakeConn(fetchrow_results=[None], fetchval_results=[None])
    repo = ParkingAllotmentRepository(db_connection=conn)

    summary = await repo.get_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert summary == {"units_short_of_entitlement": 0}


@pytest.mark.asyncio
async def test_list_slots_applies_tower_facility_floor_and_status_filters():
    conn = _FakeConn(
        total=1,
        rows=[{"id": SLOT_ID, "slot_number": 1, "display_status": "free"}],
    )
    repo = ParkingAllotmentRepository(db_connection=conn)

    rows, total = await repo.list_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        facility_id=FACILITY_ID,
        floor_level=" B1 ",
        status="free",
        page=2,
        page_size=10,
    )

    assert total == 1
    assert len(rows) == 1
    count_query, count_args = conn.fetchval_calls[0]
    assert "f.tower_id = $3::uuid" in count_query
    assert "fps.facility_id = $4::uuid" in count_query
    assert "LOWER(COALESCE(f.floor_level, '')) = LOWER($5)" in count_query
    assert count_args[4] == "B1"
    assert count_args[5] == "free"
    list_query, list_args = conn.fetch_calls[0]
    assert "OFFSET $7 LIMIT $8" in list_query
    assert list_args[-2:] == (10, 10)


@pytest.mark.asyncio
async def test_get_slot_row_returns_dict_or_none():
    conn = _FakeConn(row={"id": SLOT_ID, "slot_number": 7})
    repo = ParkingAllotmentRepository(db_connection=conn)

    found = await repo.get_slot_row(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )
    assert found == {"id": SLOT_ID, "slot_number": 7}
    query, args = conn.fetchrow_calls[0]
    assert "fps.id = $3::uuid" in query
    assert args == (ORG_ID, PROJECT_ID, SLOT_ID)

    conn.row = None
    missing = await repo.get_slot_row(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_slot_history():
    conn = _FakeConn(rows=[{"id": "event-1", "event_type": "allotted", "unit_code": "A-101"}])
    repo = ParkingAllotmentRepository(db_connection=conn)

    events = await repo.list_slot_history(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )

    assert events[0]["event_type"] == "allotted"
    query, args = conn.fetch_calls[0]
    assert "FROM parking_slot_events e" in query
    assert args == (ORG_ID, PROJECT_ID, SLOT_ID)


@pytest.mark.asyncio
async def test_list_units_with_search_tower_and_short_entitlement_filter():
    conn = _FakeConn(total=2, rows=[{"id": UNIT_ID, "code": "A-101"}])
    repo = ParkingAllotmentRepository(db_connection=conn)

    rows, total = await repo.list_units(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        entitlement_status="short",
        search="  A-10 ",
        page=1,
        page_size=25,
    )

    assert total == 2
    assert rows[0]["code"] == "A-101"
    count_query, count_args = conn.fetchval_calls[0]
    assert "u.tower_id = $3::uuid" in count_query
    assert count_args[3] == "%A-10%"
    assert "HAVING (" in count_query
    list_query, list_args = conn.fetch_calls[0]
    assert "json_agg(" in list_query
    assert list_args[-2:] == (0, 25)


@pytest.mark.asyncio
async def test_list_units_met_entitlement_filter():
    conn = _FakeConn(total=0, rows=[])
    repo = ParkingAllotmentRepository(db_connection=conn)

    await repo.list_units(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        entitlement_status="met",
        page=1,
        page_size=10,
    )

    count_query, _ = conn.fetchval_calls[0]
    assert "HAVING NOT (" in count_query


@pytest.mark.asyncio
async def test_get_unit_for_allotment_view_and_context():
    conn = _FakeConn(row={"id": UNIT_ID, "code": "A-101", "slots_assigned": 1})
    repo = ParkingAllotmentRepository(db_connection=conn)

    unit_view = await repo.get_unit_for_allotment_view(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
    )
    assert unit_view["code"] == "A-101"
    view_query, _ = conn.fetchrow_calls[0]
    assert "u.is_parking = false" in view_query

    conn.row = {"id": UNIT_ID, "is_parking": False, "slots_assigned": 0}
    context = await repo.get_unit_allotment_context(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
    )
    assert context["is_parking"] is False
    context_query, _ = conn.fetchrow_calls[1]
    assert "included_slots_assigned" in context_query


@pytest.mark.asyncio
async def test_get_active_allotment_by_slot():
    conn = _FakeConn(row={"id": ALLOTMENT_ID, "unit_id": UNIT_ID, "status": "active"})
    repo = ParkingAllotmentRepository(db_connection=conn)

    allotment = await repo.get_active_allotment_by_slot(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )
    assert allotment["id"] == ALLOTMENT_ID
    query, args = conn.fetchrow_calls[0]
    assert "unit_parking_allotments" in query
    assert args == (ORG_ID, PROJECT_ID, SLOT_ID)


@pytest.mark.asyncio
async def test_insert_allotment_release_allotment_and_insert_event():
    conn = _FakeConn(
        fetchrow_results=[
            {"id": ALLOTMENT_ID, "unit_id": UNIT_ID, "parking_slot_id": SLOT_ID},
            {"id": ALLOTMENT_ID, "status": "released"},
            {"id": "event-1", "event_type": "allotted"},
        ]
    )
    repo = ParkingAllotmentRepository(db_connection=conn)

    created = await repo.insert_allotment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        parking_slot_id=SLOT_ID,
        allotment_basis="included_with_unit",
        effective_from=date(2026, 3, 1),
        created_by_user_id="user-1",
    )
    assert created["id"] == ALLOTMENT_ID
    insert_query, insert_args = conn.fetchrow_calls[0]
    assert "INSERT INTO unit_parking_allotments" in insert_query
    assert insert_args[4] == "included_with_unit"

    released = await repo.release_allotment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        allotment_id=ALLOTMENT_ID,
        release_reason="move",
        updated_by_user_id="user-1",
    )
    assert released["status"] == "released"

    event = await repo.insert_event(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        parking_slot_id=SLOT_ID,
        event_type="allotted",
        unit_id=UNIT_ID,
        allotment_id=ALLOTMENT_ID,
        actor_user_id="user-1",
        payload={"reason": "test"},
    )
    assert event["event_type"] == "allotted"
    event_query, event_args = conn.fetchrow_calls[2]
    assert "INSERT INTO parking_slot_events" in event_query
    assert json.loads(event_args[7]) == {"reason": "test"}


@pytest.mark.asyncio
async def test_clear_vehicle_slot_references():
    conn = _FakeConn()
    repo = ParkingAllotmentRepository(db_connection=conn)

    await repo.clear_vehicle_slot_references(
        organization_id=ORG_ID,
        parking_slot_id=SLOT_ID,
    )

    query, args = conn.execute_calls[0]
    assert "UPDATE vehicles" in query
    assert "parking_slot_id = NULL" in query
    assert args == (ORG_ID, SLOT_ID)
