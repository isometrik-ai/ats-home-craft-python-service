"""Unit tests for UnitsRepository query building with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.units_repository import UnitsRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
ZONE_ID = "880e8400-e29b-41d4-a716-446655440003"
TOWER_ID = "990e8400-e29b-41d4-a716-446655440004"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None, execute_result="DELETE 1"):
        self.rows = rows or []
        self.row = row
        self.val = val
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

    async def fetchval(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        if self.val is not None:
            return self.val
        return len(self.rows)

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
    units, total = await repo.list_units(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(units) == 1
    assert total == 1

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
async def test_list_units_applies_registry_filters():
    """List units passes filter params into registry WHERE builder."""
    conn = _FakeConn(row={"total": 0, "sold_count": 0, "unsold_count": 0})
    conn.rows = []
    repo = UnitsRepository(db_connection=conn)

    units, total = await repo.list_units(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        search="A-101",
        property_type="residential",
        tower_id=TOWER_ID,
        config_id="cfg-1",
        status="vacant",
    )

    assert units == []
    assert total == 0
    list_query = conn.fetch_calls[0][0]
    assert "ILIKE" in list_query
    assert "tower_id" in list_query
    assert "config_id" in list_query
    assert "::unit_status" in list_query
    assert "parking_entitlement" in list_query


@pytest.mark.asyncio
async def test_get_units_registry_summary():
    """Summary query returns sold and unsold counts."""
    conn = _FakeConn(row={"total": 2, "sold_count": 1, "unsold_count": 1})
    repo = UnitsRepository(db_connection=conn)

    summary = await repo.get_units_registry_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert summary == {"total": 2, "sold_count": 1, "unsold_count": 1}


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


@pytest.mark.asyncio
async def test_count_units_by_property_type_for_projects():
    """Batch unit counts are grouped by project and resolved property type."""
    conn = _FakeConn(
        rows=[
            {"project_id": PROJECT_ID, "property_type": "residential", "unit_count": 12},
            {"project_id": PROJECT_ID, "property_type": "commercial", "unit_count": 3},
        ]
    )
    repo = UnitsRepository(db_connection=conn)

    counts = await repo.count_units_by_property_type_for_projects(
        organization_id=ORG_ID,
        project_ids=[PROJECT_ID],
    )

    assert counts == {
        PROJECT_ID: {"residential": 12, "commercial": 3, "plots": 0},
    }
    query, args = conn.fetch_calls[0]
    assert "GROUP BY u.project_id" in query
    assert "NOT u.is_parking" in query
    assert args == (ORG_ID, [PROJECT_ID])


@pytest.mark.asyncio
async def test_count_units_by_property_type_for_projects_empty():
    """Empty project id list skips the database query."""
    conn = _FakeConn()
    repo = UnitsRepository(db_connection=conn)

    counts = await repo.count_units_by_property_type_for_projects(
        organization_id=ORG_ID,
        project_ids=[],
    )

    assert counts == {}
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_reconcile_unit_inventory_status_marks_vacant_without_owner():
    """Reconcile clears occupied inventory when no active Owner exists."""
    conn = _FakeConn(rows=[])
    repo = UnitsRepository(db_connection=conn)

    status = await repo.reconcile_unit_inventory_status(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
    )

    assert status == "vacant"
    assert len(conn.execute_calls) == 1
    assert "vacant" in conn.execute_calls[0][0]


@pytest.mark.asyncio
async def test_reconcile_unit_inventory_status_marks_occupied_with_owner():
    """Reconcile keeps inventory occupied when an active Owner exists."""
    conn = _FakeConn(rows=[{"exists": True}])
    repo = UnitsRepository(db_connection=conn)

    status = await repo.reconcile_unit_inventory_status(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
    )

    assert status == "occupied"
    assert len(conn.execute_calls) == 1
    assert "occupied" in conn.execute_calls[0][0]


@pytest.mark.asyncio
async def test_bulk_insert_units_empty_and_multi():
    conn = _FakeConn(rows=[{"id": UNIT_ID, "code": "A-101"}])
    repo = UnitsRepository(db_connection=conn)

    assert await repo.bulk_insert_units([]) == []

    inserted = await repo.bulk_insert_units(
        [
            {
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "code": "A-101",
                "status": "available",
            }
        ]
    )
    assert inserted[0]["code"] == "A-101"
    assert "INSERT INTO units" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_has_active_owner_and_mark_unit_status():
    conn = _FakeConn(val=True)
    repo = UnitsRepository(db_connection=conn)

    assert await repo.has_active_owner(organization_id=ORG_ID, unit_id=UNIT_ID) is True

    conn.execute_result = "UPDATE 1"
    await repo.mark_unit_occupied(organization_id=ORG_ID, project_id=PROJECT_ID, unit_id=UNIT_ID)
    assert "occupied" in conn.execute_calls[-1][0]

    await repo.mark_unit_vacant(organization_id=ORG_ID, project_id=PROJECT_ID, unit_id=UNIT_ID)
    assert "vacant" in conn.execute_calls[-1][0]


@pytest.mark.asyncio
async def test_get_by_plot_item_id_and_parking_entitlement():
    conn = _FakeConn(row={"id": UNIT_ID}, val=2)
    repo = UnitsRepository(db_connection=conn)

    unit = await repo.get_by_plot_item_id(
        organization_id=ORG_ID,
        plot_item_id="plot-1",
    )
    assert unit["id"] == UNIT_ID

    entitlement = await repo.get_parking_entitlement_by_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
    )
    assert entitlement == 2


@pytest.mark.asyncio
async def test_get_unit_owner_and_residents_batch():
    conn = _FakeConn(
        row={"contact_id": "c1", "first_name": "Jane"},
        rows=[{"contact_id": "c1", "unit_id": UNIT_ID, "person_name": "Jane Doe"}],
    )
    repo = UnitsRepository(db_connection=conn)

    owner = await repo.get_unit_owner_contact(organization_id=ORG_ID, unit_id=UNIT_ID)
    assert owner["first_name"] == "Jane"

    residents = await repo.get_contact_residents_batch(
        organization_id=ORG_ID,
        contact_unit_pairs=[],
    )
    assert residents == {}

    residents = await repo.get_contact_residents_batch(
        organization_id=ORG_ID,
        contact_unit_pairs=[("c1", UNIT_ID)],
    )
    assert residents[f"c1:{UNIT_ID}"]["person_name"] == "Jane Doe"


@pytest.mark.asyncio
async def test_get_unit_role_occupants_and_summary_none():
    conn = _FakeConn(
        rows=[
            {
                "unit_id": UNIT_ID,
                "role": "Owner",
                "contact_id": "c1",
                "person_name": "Jane Doe",
            }
        ]
    )
    repo = UnitsRepository(db_connection=conn)

    occupants = await repo.get_unit_role_occupants(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
    )
    assert "owner" in occupants

    batch = await repo.get_unit_role_occupants_batch(
        organization_id=ORG_ID,
        unit_ids=[UNIT_ID],
    )
    assert UNIT_ID in batch

    conn.row = None
    summary = await repo.get_units_registry_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )
    assert summary == {"total": 0, "sold_count": 0, "unsold_count": 0}
