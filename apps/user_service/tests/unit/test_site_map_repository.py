"""Unit tests for SiteMapRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.site_map_repository import SiteMapRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
OVERLAY_ID = "770e8400-e29b-41d4-a716-446655440002"
ENTITY_ID = "880e8400-e29b-41d4-a716-446655440003"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="DELETE 1"):
        self.rows = rows or []
        self.row = row
        self.execute_result = execute_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        return self.row

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_insert_overlays():
    """Insert overlays bulk inserts required columns."""
    conn = _FakeConn(
        rows=[
            {
                "id": OVERLAY_ID,
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "entity_type": "tower",
                "entity_id": ENTITY_ID,
                "latitude": 12.97,
                "longitude": 77.59,
                "label": "Tower A",
            }
        ]
    )
    repo = SiteMapRepository(db_connection=conn)

    overlays = await repo.insert_overlays(
        [
            {
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "entity_type": "tower",
                "entity_id": ENTITY_ID,
                "latitude": 12.97,
                "longitude": 77.59,
                "label": "Tower A",
            }
        ]
    )

    assert overlays[0]["label"] == "Tower A"
    query, _ = conn.fetch_calls[0]
    assert "INSERT INTO site_map_overlays" in query
    assert "RETURNING *" in query


@pytest.mark.asyncio
async def test_insert_overlays_empty():
    """Empty overlay list returns without querying."""
    conn = _FakeConn()
    repo = SiteMapRepository(db_connection=conn)

    overlays = await repo.insert_overlays([])

    assert overlays == []
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_list_overlays():
    """List overlays scoped to org and project."""
    conn = _FakeConn(rows=[{"id": OVERLAY_ID, "label": "Gate"}])
    repo = SiteMapRepository(db_connection=conn)

    overlays = await repo.list_overlays(organization_id=ORG_ID, project_id=PROJECT_ID)

    assert overlays[0]["label"] == "Gate"
    query, args = conn.fetch_calls[0]
    assert "FROM site_map_overlays" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_list_overlays_empty():
    """No overlays returns empty list."""
    conn = _FakeConn(rows=[])
    repo = SiteMapRepository(db_connection=conn)

    assert await repo.list_overlays(organization_id=ORG_ID, project_id=PROJECT_ID) == []


@pytest.mark.asyncio
async def test_delete_overlay_success():
    """Delete overlay returns True when one row removed."""
    conn = _FakeConn(execute_result="DELETE 1")
    repo = SiteMapRepository(db_connection=conn)

    deleted = await repo.delete_overlay(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        overlay_id=OVERLAY_ID,
    )

    assert deleted is True
    query, args = conn.execute_calls[0]
    assert "DELETE FROM site_map_overlays" in query
    assert args == (OVERLAY_ID, PROJECT_ID, ORG_ID)


@pytest.mark.asyncio
async def test_delete_overlay_not_found():
    """Delete overlay returns False when no row removed."""
    conn = _FakeConn(execute_result="DELETE 0")
    repo = SiteMapRepository(db_connection=conn)

    deleted = await repo.delete_overlay(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        overlay_id=OVERLAY_ID,
    )

    assert deleted is False
