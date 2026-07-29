"""Unit tests for InventoryRepository query building with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.inventory_repository import (
    InventoryRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
TOWER_ID = "770e8400-e29b-41d4-a716-446655440002"
FLOOR_ID = "880e8400-e29b-41d4-a716-446655440003"
CONFIG_ID = "990e8400-e29b-41d4-a716-446655440004"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, fetchval_results=None):
        self.rows = rows or []
        self.row = row
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return True


@pytest.mark.asyncio
async def test_references_valid_all_ok():
    """All tower/floor/config references must validate."""
    conn = _FakeConn(fetchval_results=[True, True, True])
    repo = InventoryRepository(db_connection=conn)

    ok = await repo.references_valid(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_ids=[TOWER_ID],
        floor_ids=[FLOOR_ID],
        config_ids=[CONFIG_ID],
    )

    assert ok is True
    assert len(conn.fetchval_calls) == 3
    assert "FROM towers" in conn.fetchval_calls[0][0]
    assert "FROM floors f" in conn.fetchval_calls[1][0]
    assert "FROM unit_configs" in conn.fetchval_calls[2][0]


@pytest.mark.asyncio
async def test_references_valid_towers_fail():
    """Invalid towers fail validation even when floors/configs pass."""
    conn = _FakeConn(fetchval_results=[False, True, True])
    repo = InventoryRepository(db_connection=conn)

    ok = await repo.references_valid(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_ids=[TOWER_ID],
        floor_ids=[FLOOR_ID],
        config_ids=[CONFIG_ID],
    )

    assert ok is False


@pytest.mark.asyncio
async def test_references_valid_deduplicates_ids():
    """Duplicate ids are deduplicated before validation."""
    conn = _FakeConn(fetchval_results=[True, True, True])
    repo = InventoryRepository(db_connection=conn)

    await repo.references_valid(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_ids=[TOWER_ID, TOWER_ID],
        floor_ids=[FLOOR_ID, FLOOR_ID],
        config_ids=[CONFIG_ID, CONFIG_ID],
    )

    for _, args in conn.fetchval_calls:
        assert len(set(args[2])) == 1


@pytest.mark.asyncio
async def test_upsert_items():
    """Upsert returns affected inventory rows."""
    conn = _FakeConn(rows=[{"tower_id": TOWER_ID, "floor_id": FLOOR_ID, "quantity": 3}])
    repo = InventoryRepository(db_connection=conn)

    rows = await repo.upsert_items(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_ids=[TOWER_ID],
        floor_ids=[FLOOR_ID],
        config_ids=[CONFIG_ID],
        quantities=[3],
    )

    assert len(rows) == 1
    assert rows[0]["quantity"] == 3
    query, _ = conn.fetch_calls[0]
    assert "INSERT INTO floor_inventory" in query
    assert "ON CONFLICT" in query


@pytest.mark.asyncio
async def test_list_inventory():
    """List inventory cells for a project."""
    conn = _FakeConn(rows=[{"id": "inv-1"}, {"id": "inv-2"}])
    repo = InventoryRepository(db_connection=conn)

    items = await repo.list_inventory(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert len(items) == 2
    query, args = conn.fetch_calls[0]
    assert "FROM floor_inventory" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_list_inventory_empty():
    """Empty inventory returns no rows."""
    conn = _FakeConn(rows=[])
    repo = InventoryRepository(db_connection=conn)

    items = await repo.list_inventory(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert items == []


@pytest.mark.asyncio
async def test_list_summary_towers():
    """Summary towers query scopes to org and project."""
    conn = _FakeConn(rows=[{"id": TOWER_ID, "name": "Tower A"}])
    repo = InventoryRepository(db_connection=conn)

    towers = await repo.list_summary_towers(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert towers[0]["name"] == "Tower A"
    assert "FROM towers" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_list_summary_units_with_filters():
    """Summary units supports tower and status filters."""
    conn = _FakeConn(rows=[{"id": "unit-1", "code": "A-101", "status": "vacant"}])
    repo = InventoryRepository(db_connection=conn)

    units = await repo.list_summary_units(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
        status="vacant",
    )

    assert units[0]["code"] == "A-101"
    query, args = conn.fetch_calls[0]
    assert "::unit_status" in query
    assert args == (ORG_ID, PROJECT_ID, TOWER_ID, "vacant")


@pytest.mark.asyncio
async def test_list_summary_units_no_filters():
    """Summary units without filters passes NULL placeholders."""
    conn = _FakeConn(rows=[])
    repo = InventoryRepository(db_connection=conn)

    units = await repo.list_summary_units(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert units == []
    assert conn.fetch_calls[0][1][2:] == (None, None)


@pytest.mark.asyncio
async def test_list_summary_floors():
    """Summary floors joins towers and optional tower filter."""
    conn = _FakeConn(rows=[{"id": FLOOR_ID, "level_number": 1}])
    repo = InventoryRepository(db_connection=conn)

    floors = await repo.list_summary_floors(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        tower_id=TOWER_ID,
    )

    assert floors[0]["level_number"] == 1
    query, _ = conn.fetch_calls[0]
    assert "JOIN towers t" in query


@pytest.mark.asyncio
async def test_list_summary_plot_configs():
    """Plot configs filter by config_kind plot."""
    conn = _FakeConn(rows=[{"id": CONFIG_ID, "name": "Plot A"}])
    repo = InventoryRepository(db_connection=conn)

    configs = await repo.list_summary_plot_configs(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert configs[0]["name"] == "Plot A"
    assert "config_kind = 'plot'" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_list_summary_plot_items():
    """Plot items join configs and optional unit status."""
    conn = _FakeConn(rows=[{"id": "plot-1", "plot_no": "P-01", "unit_status": "sold"}])
    repo = InventoryRepository(db_connection=conn)

    items = await repo.list_summary_plot_items(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert items[0]["plot_no"] == "P-01"
    query, _ = conn.fetch_calls[0]
    assert "FROM plot_config_items pci" in query
    assert "owner_row.owner_contact_id" in query
    assert "contact_units cu" in query
