"""Unit tests for ParkingSlotsRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
FACILITY_ID = "770e8400-e29b-41d4-a716-446655440002"
SLOT_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="DELETE 3"):
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_bulk_insert_slots():
    """Bulk insert creates numbered slots via unnest."""
    conn = _FakeConn(
        rows=[
            {"id": "slot-1", "slot_number": 1, "slot_code": "SLT-A-1"},
            {"id": "slot-2", "slot_number": 2, "slot_code": "SLT-A-2"},
        ]
    )
    repo = ParkingSlotsRepository(db_connection=conn)

    slots = await repo.bulk_insert_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        slots=[(1, "SLT-A-1"), (2, "SLT-A-2")],
    )

    assert len(slots) == 2
    query, args = conn.fetch_calls[0]
    assert "INSERT INTO facility_parking_slots" in query
    assert "unnest" in query
    assert "slot_code" in query
    assert args == (ORG_ID, PROJECT_ID, FACILITY_ID, [1, 2], ["SLT-A-1", "SLT-A-2"])


@pytest.mark.asyncio
async def test_bulk_insert_slots_empty_list():
    """Empty slot list is a no-op."""
    conn = _FakeConn()
    repo = ParkingSlotsRepository(db_connection=conn)

    slots = await repo.bulk_insert_slots(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        slots=[],
    )

    assert slots == []
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_list_by_facility():
    """List slots for facility with optional status filter."""
    conn = _FakeConn(rows=[{"id": SLOT_ID, "slot_number": 1, "status": "available"}])
    repo = ParkingSlotsRepository(db_connection=conn)

    slots = await repo.list_by_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        status="available",
    )

    assert slots[0]["status"] == "available"
    query, args = conn.fetch_calls[0]
    assert "::parking_slot_status" in query
    assert args[3] == "available"


@pytest.mark.asyncio
async def test_list_by_facility_no_status_filter():
    """List without status passes NULL filter."""
    conn = _FakeConn(rows=[])
    repo = ParkingSlotsRepository(db_connection=conn)

    slots = await repo.list_by_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )

    assert slots == []
    assert conn.fetch_calls[0][1][3] is None


@pytest.mark.asyncio
async def test_get_slot():
    """Get slot scoped to org and project."""
    conn = _FakeConn(row={"id": SLOT_ID, "slot_number": 5})
    repo = ParkingSlotsRepository(db_connection=conn)

    slot = await repo.get_slot(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )

    assert slot["slot_number"] == 5


@pytest.mark.asyncio
async def test_get_slot_not_found():
    """Missing slot returns None."""
    conn = _FakeConn(row=None)
    repo = ParkingSlotsRepository(db_connection=conn)

    slot = await repo.get_slot(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )

    assert slot is None


@pytest.mark.asyncio
async def test_assign_slot():
    """Assign marks available slot as assigned."""
    conn = _FakeConn(row={"id": SLOT_ID, "status": "assigned"})
    repo = ParkingSlotsRepository(db_connection=conn)

    slot = await repo.assign_slot(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )

    assert slot["status"] == "assigned"
    query, _ = conn.fetchrow_calls[0]
    assert "status = 'assigned'::parking_slot_status" in query
    assert "status = 'available'::parking_slot_status" in query


@pytest.mark.asyncio
async def test_assign_slot_not_available():
    """Assign on non-available slot returns None."""
    conn = _FakeConn(row=None)
    repo = ParkingSlotsRepository(db_connection=conn)

    assert (
        await repo.assign_slot(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
        )
        is None
    )


@pytest.mark.asyncio
async def test_release_slot():
    """Release marks slot available."""
    conn = _FakeConn(row={"id": SLOT_ID, "status": "available"})
    repo = ParkingSlotsRepository(db_connection=conn)

    slot = await repo.release_slot(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
    )

    assert slot["status"] == "available"
    assert "status = 'available'::parking_slot_status" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_release_slot_not_found():
    """Release on missing slot returns None."""
    conn = _FakeConn(row=None)
    repo = ParkingSlotsRepository(db_connection=conn)

    assert (
        await repo.release_slot(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
        )
        is None
    )


@pytest.mark.asyncio
async def test_delete_by_facility():
    """Delete all slots for a facility."""
    conn = _FakeConn()
    repo = ParkingSlotsRepository(db_connection=conn)

    await repo.delete_by_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )

    query, args = conn.execute_calls[0]
    assert "DELETE FROM facility_parking_slots" in query
    assert args == (ORG_ID, PROJECT_ID, FACILITY_ID)
