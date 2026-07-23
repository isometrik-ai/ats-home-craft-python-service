"""Unit tests for TowersRepository query building with fake connection."""

from __future__ import annotations

import json

import pytest

from apps.user_service.app.db.repositories.towers_repository import TowersRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
TOWER_ID = "770e8400-e29b-41d4-a716-446655440002"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, val=None, execute_result="DELETE 1"):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_insert_tower_casts_enums():
    conn = _FakeConn(row={"id": TOWER_ID, "name": "Tower A"})
    repo = TowersRepository(db_connection=conn)

    row = await repo.insert_tower(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "name": "Tower A",
            "tower_type": "residential",
            "numbering_pattern": "sequential",
        }
    )

    assert row["name"] == "Tower A"
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO towers" in query
    assert "::tower_type" in query
    assert "::unit_numbering_pattern" in query


@pytest.mark.asyncio
async def test_get_tower_and_list_towers():
    conn = _FakeConn(row={"id": TOWER_ID}, rows=[{"id": TOWER_ID}])
    repo = TowersRepository(db_connection=conn)

    found = await repo.get_tower(organization_id=ORG_ID, project_id=PROJECT_ID, tower_id=TOWER_ID)
    assert found["id"] == TOWER_ID

    conn.row = None
    missing = await repo.get_tower(organization_id=ORG_ID, project_id=PROJECT_ID, tower_id=TOWER_ID)
    assert missing is None

    towers = await repo.list_towers(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(towers) == 1
    assert "ORDER BY sort_order" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_update_tower_empty_and_patch():
    conn = _FakeConn(row={"id": TOWER_ID, "name": "Renamed"})
    repo = TowersRepository(db_connection=conn)

    unchanged = await repo.update_tower(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        update_data={},
    )
    assert unchanged["id"] == TOWER_ID
    assert "SELECT * FROM towers" in conn.fetchrow_calls[0][0]

    conn.fetchrow_calls.clear()
    updated = await repo.update_tower(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        update_data={"name": "Renamed", "tower_type": "commercial"},
    )
    assert updated["name"] == "Renamed"
    query, _ = conn.fetchrow_calls[0]
    assert "UPDATE towers" in query
    assert "tower_type = $2::tower_type" in query


@pytest.mark.asyncio
async def test_delete_tower():
    conn = _FakeConn(execute_result="DELETE 1")
    repo = TowersRepository(db_connection=conn)
    assert await repo.delete_tower(organization_id=ORG_ID, project_id=PROJECT_ID, tower_id=TOWER_ID)

    conn.execute_result = "DELETE 0"
    assert not await repo.delete_tower(
        organization_id=ORG_ID, project_id=PROJECT_ID, tower_id=TOWER_ID
    )


@pytest.mark.asyncio
async def test_wing_operations():
    wing_id = "880e8400-e29b-41d4-a716-446655440003"
    conn = _FakeConn(row={"id": wing_id}, rows=[{"id": wing_id}], val=1)
    repo = TowersRepository(db_connection=conn)

    wing = await repo.insert_wing(
        {
            "organization_id": ORG_ID,
            "tower_id": TOWER_ID,
            "name": "East Wing",
            "code": "E",
        }
    )
    assert wing["id"] == wing_id

    wings = await repo.list_wings(organization_id=ORG_ID, tower_id=TOWER_ID)
    assert len(wings) == 1

    assert await repo.wing_belongs_to_tower(
        organization_id=ORG_ID, tower_id=TOWER_ID, wing_id=wing_id
    )

    conn.val = None
    assert not await repo.wing_belongs_to_tower(
        organization_id=ORG_ID, tower_id=TOWER_ID, wing_id=wing_id
    )

    assert await repo.delete_wing(organization_id=ORG_ID, tower_id=TOWER_ID, wing_id=wing_id)


@pytest.mark.asyncio
async def test_gate_operations_with_operating_hours():
    gate_id = "990e8400-e29b-41d4-a716-446655440004"
    conn = _FakeConn(row={"id": gate_id}, rows=[{"id": gate_id}])
    repo = TowersRepository(db_connection=conn)

    gate = await repo.insert_gate(
        {
            "organization_id": ORG_ID,
            "tower_id": TOWER_ID,
            "name": "Main Gate",
            "operating_hours": {"mon": "09:00-18:00"},
        }
    )
    assert gate["id"] == gate_id
    _, args = conn.fetchrow_calls[0]
    assert json.loads(args[7]) == {"mon": "09:00-18:00"}

    gates = await repo.list_gates(organization_id=ORG_ID, tower_id=TOWER_ID)
    assert len(gates) == 1

    by_id = await repo.get_gate_by_id(organization_id=ORG_ID, gate_id=gate_id)
    assert by_id["id"] == gate_id

    assert await repo.delete_gate(organization_id=ORG_ID, tower_id=TOWER_ID, gate_id=gate_id)


@pytest.mark.asyncio
async def test_lift_and_floor_operations():
    lift_id = "aa0e8400-e29b-41d4-a716-446655440005"
    floor_id = "bb0e8400-e29b-41d4-a716-446655440006"
    conn = _FakeConn(
        row={"id": lift_id},
        rows=[{"id": lift_id}],
    )
    repo = TowersRepository(db_connection=conn)

    lift = await repo.insert_lift(
        {
            "organization_id": ORG_ID,
            "tower_id": TOWER_ID,
            "name": "Lift 1",
            "serves_floors": [1, 2, 3],
        }
    )
    assert lift["id"] == lift_id
    assert "::lift_type" in conn.fetchrow_calls[0][0]

    lifts = await repo.list_lifts(organization_id=ORG_ID, tower_id=TOWER_ID)
    assert len(lifts) == 1
    assert lifts[0]["id"] == lift_id
    assert await repo.delete_lift(organization_id=ORG_ID, tower_id=TOWER_ID, lift_id=lift_id)

    conn.row = {"id": floor_id}
    floor = await repo.insert_floor(
        {
            "organization_id": ORG_ID,
            "tower_id": TOWER_ID,
            "level_number": 1,
            "display_name": "Ground",
        }
    )
    assert floor["id"] == floor_id

    conn.rows = [{"id": floor_id}]
    floors = await repo.list_floors(organization_id=ORG_ID, tower_id=TOWER_ID)
    assert len(floors) == 1
    assert await repo.delete_floor(organization_id=ORG_ID, tower_id=TOWER_ID, floor_id=floor_id)
