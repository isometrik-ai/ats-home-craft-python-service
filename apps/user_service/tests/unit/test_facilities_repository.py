"""Unit tests for FacilitiesRepository query building with fake connection."""

from __future__ import annotations

import json

import pytest

from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
FACILITY_ID = "770e8400-e29b-41d4-a716-446655440002"
TOWER_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, execute_result="DELETE 1", fetchval_result=0):
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
        self.fetchval_result = fetchval_result
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
        return self.fetchval_result

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_insert_facility_with_extra_attributes_and_casts():
    """Insert serializes extra_attributes as jsonb and casts enum columns."""
    extra = {"amenity_tags": ["pool", "gym"], "notes": "near lobby"}
    conn = _FakeConn(row={"id": FACILITY_ID, "name": "Clubhouse", "extra_attributes": extra})
    repo = FacilitiesRepository(db_connection=conn)

    inserted = await repo.insert_facility(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "name": "Clubhouse",
            "status": "active",
            "location_type": "tower",
            "parking_user_type": "resident",
            "extra_attributes": extra,
        }
    )

    assert inserted["name"] == "Clubhouse"
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facilities" in query
    assert "::jsonb" in query
    assert "::facility_status" in query
    assert "::facility_location_type" in query
    assert "::parking_user_type" in query
    assert json.dumps(extra) in args


@pytest.mark.asyncio
async def test_insert_facility_extra_attributes_none_serializes_empty_object():
    """None extra_attributes is stored as empty JSON object."""
    conn = _FakeConn(row={"id": FACILITY_ID})
    repo = FacilitiesRepository(db_connection=conn)

    await repo.insert_facility(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "name": "Parking",
            "extra_attributes": None,
        }
    )

    _, args = conn.fetchrow_calls[0]
    assert "{}" in args


@pytest.mark.asyncio
async def test_get_facility_found_and_not_found():
    conn = _FakeConn(row={"id": FACILITY_ID, "name": "Gym"})
    repo = FacilitiesRepository(db_connection=conn)

    found = await repo.get_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert found["name"] == "Gym"
    query, args = conn.fetchrow_calls[0]
    assert "FROM facilities" in query
    assert args == (FACILITY_ID, PROJECT_ID, ORG_ID)

    conn.row = None
    missing = await repo.get_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert missing is None


@pytest.mark.asyncio
async def test_list_facilities():
    conn = _FakeConn(rows=[{"id": FACILITY_ID, "sort_order": 1}], fetchval_result=15)
    repo = FacilitiesRepository(db_connection=conn)

    facilities, total = await repo.list_facilities(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        page=2,
        page_size=10,
    )

    assert len(facilities) == 1
    assert total == 15
    count_query, count_args = conn.fetchval_calls[0]
    assert "SELECT COUNT(*)::int FROM facilities" in count_query
    assert count_args == (ORG_ID, PROJECT_ID)
    query, args = conn.fetch_calls[0]
    assert "ORDER BY sort_order, created_at" in query
    assert "OFFSET $3 LIMIT $4" in query
    assert args == (ORG_ID, PROJECT_ID, 10, 10)


@pytest.mark.asyncio
async def test_update_facility_with_data_and_empty_dict():
    conn = _FakeConn(row={"id": FACILITY_ID, "name": "Updated Gym"})
    repo = FacilitiesRepository(db_connection=conn)

    updated = await repo.update_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        update_data={"name": "Updated Gym", "status": "inactive"},
    )
    assert updated["name"] == "Updated Gym"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE facilities SET" in query
    assert "updated_at = now()" in query
    assert "::facility_status" in query
    assert args[-3:] == (FACILITY_ID, PROJECT_ID, ORG_ID)

    conn.row = {"id": FACILITY_ID}
    conn.fetchrow_calls.clear()
    unchanged = await repo.update_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        update_data={},
    )
    assert unchanged["id"] == FACILITY_ID
    assert len(conn.fetchrow_calls) == 1
    assert "SELECT * FROM facilities" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_update_facility_extra_attributes_and_not_found():
    conn = _FakeConn(row={"id": FACILITY_ID})
    repo = FacilitiesRepository(db_connection=conn)

    await repo.update_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        update_data={"extra_attributes": {"key": "value"}},
    )
    query, args = conn.fetchrow_calls[0]
    assert "extra_attributes = $1::jsonb" in query
    assert json.dumps({"key": "value"}) in args

    conn.row = None
    missing = await repo.update_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        update_data={"name": "Missing"},
    )
    assert missing is None


@pytest.mark.asyncio
async def test_delete_facility_success_and_failure():
    conn = _FakeConn(execute_result="DELETE 1")
    repo = FacilitiesRepository(db_connection=conn)

    deleted = await repo.delete_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert deleted is True
    query, args = conn.execute_calls[0]
    assert "DELETE FROM facilities" in query
    assert args == (FACILITY_ID, PROJECT_ID, ORG_ID)

    conn.execute_result = "DELETE 0"
    not_deleted = await repo.delete_facility(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )
    assert not_deleted is False
