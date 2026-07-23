"""Unit tests for UnitsRepository query building with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.units_repository import UnitsRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
ZONE_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="DELETE 1"):
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
async def test_insert_get_list_update_delete_unit():
    conn = _FakeConn(row={"id": UNIT_ID, "code": "A-101"})
    repo = UnitsRepository(db_connection=conn)

    inserted = await repo.insert_unit(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "code": "A-101",
            "status": "available",
        }
    )
    assert inserted["code"] == "A-101"
    assert "::unit_status" in conn.fetchrow_calls[0][0]

    found = await repo.get_unit(organization_id=ORG_ID, project_id=PROJECT_ID, unit_id=UNIT_ID)
    assert found["code"] == "A-101"

    conn.rows = [{"id": UNIT_ID}]
    units = await repo.list_units(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(units) == 1

    conn.row = {"id": UNIT_ID, "unit_label": "101A"}
    updated = await repo.update_unit(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        update_data={"unit_label": "101A"},
    )
    assert updated["unit_label"] == "101A"

    conn.row = {"id": UNIT_ID}
    unchanged = await repo.update_unit(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        update_data={},
    )
    assert unchanged["id"] == UNIT_ID

    assert await repo.delete_unit(organization_id=ORG_ID, project_id=PROJECT_ID, unit_id=UNIT_ID)


@pytest.mark.asyncio
async def test_get_unit_detail_base_and_residents():
    conn = _FakeConn(
        row={"id": UNIT_ID, "tower_name": "Tower A"},
        rows=[{"contact_id": "c1", "first_name": "Jane"}],
    )
    repo = UnitsRepository(db_connection=conn)

    detail = await repo.get_unit_detail_base(
        organization_id=ORG_ID, project_id=PROJECT_ID, unit_id=UNIT_ID
    )
    assert detail["tower_name"] == "Tower A"
    assert "LEFT JOIN towers t" in conn.fetchrow_calls[0][0]

    residents = await repo.list_unit_residents(organization_id=ORG_ID, unit_id=UNIT_ID)
    assert residents[0]["first_name"] == "Jane"
    assert "contact_units cu" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_count_unit_vehicles():
    conn = _FakeConn(row={"vehicles_count": 2, "parking_slots_assigned": 1})
    repo = UnitsRepository(db_connection=conn)

    vehicles, slots = await repo.count_unit_vehicles(organization_id=ORG_ID, unit_id=UNIT_ID)
    assert vehicles == 2
    assert slots == 1

    conn.row = None
    assert await repo.count_unit_vehicles(organization_id=ORG_ID, unit_id=UNIT_ID) == (0, 0)


@pytest.mark.asyncio
async def test_parking_zone_operations():
    conn = _FakeConn(row={"id": ZONE_ID}, rows=[{"id": ZONE_ID}])
    repo = UnitsRepository(db_connection=conn)

    zone = await repo.insert_parking_zone(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "tower_id": "t1",
            "floor_id": "f1",
            "name": "Basement P1",
        }
    )
    assert zone["id"] == ZONE_ID

    zones = await repo.list_parking_zones(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(zones) == 1

    assert await repo.delete_parking_zone(
        organization_id=ORG_ID, project_id=PROJECT_ID, zone_id=ZONE_ID
    )
