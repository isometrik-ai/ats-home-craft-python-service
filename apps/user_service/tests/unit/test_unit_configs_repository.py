"""Unit tests for UnitConfigsRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.unit_configs_repository import (
    UnitConfigsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
CONFIG_ID = "770e8400-e29b-41d4-a716-446655440002"
ITEM_ID = "880e8400-e29b-41d4-a716-446655440003"
MEDIA_ID = "990e8400-e29b-41d4-a716-446655440004"


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
async def test_config_crud():
    conn = _FakeConn(row={"id": CONFIG_ID, "name": "2BHK"})
    repo = UnitConfigsRepository(db_connection=conn)

    inserted = await repo.insert_config(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "config_kind": "residential",
            "name": "2BHK",
            "default_facing": "north",
        }
    )
    assert inserted["name"] == "2BHK"
    assert "::unit_config_kind" in conn.fetchrow_calls[0][0]

    found = await repo.get_config(
        organization_id=ORG_ID, project_id=PROJECT_ID, config_id=CONFIG_ID
    )
    assert found["id"] == CONFIG_ID

    conn.rows = [{"id": CONFIG_ID}]
    all_configs = await repo.list_configs(organization_id=ORG_ID, project_id=PROJECT_ID)
    assert len(all_configs) == 1

    conn.rows = [{"id": CONFIG_ID}]
    filtered = await repo.list_configs(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        config_kind="residential",
    )
    assert len(filtered) == 1
    assert "config_kind = $3::unit_config_kind" in conn.fetch_calls[1][0]

    conn.row = {"id": CONFIG_ID, "bedrooms": 2}
    updated = await repo.update_config(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        config_id=CONFIG_ID,
        update_data={"bedrooms": 2},
    )
    assert updated["bedrooms"] == 2

    conn.row = {"id": CONFIG_ID}
    unchanged = await repo.update_config(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        config_id=CONFIG_ID,
        update_data={},
    )
    assert unchanged["id"] == CONFIG_ID

    assert await repo.delete_config(
        organization_id=ORG_ID, project_id=PROJECT_ID, config_id=CONFIG_ID
    )


@pytest.mark.asyncio
async def test_plot_item_operations():
    conn = _FakeConn(row={"id": ITEM_ID}, rows=[{"id": ITEM_ID}])
    repo = UnitConfigsRepository(db_connection=conn)

    item = await repo.insert_plot_item(
        {
            "organization_id": ORG_ID,
            "config_id": CONFIG_ID,
            "plot_no": "P-12",
            "size_sqft": 1200,
        }
    )
    assert item["id"] == ITEM_ID
    assert "::plot_item_status" in conn.fetchrow_calls[0][0]

    items = await repo.list_plot_items(organization_id=ORG_ID, config_id=CONFIG_ID)
    assert len(items) == 1

    assert await repo.delete_plot_item(organization_id=ORG_ID, config_id=CONFIG_ID, item_id=ITEM_ID)


@pytest.mark.asyncio
async def test_config_media_operations():
    conn = _FakeConn(row={"id": MEDIA_ID}, rows=[{"id": MEDIA_ID}])
    repo = UnitConfigsRepository(db_connection=conn)

    media = await repo.insert_media(
        {
            "organization_id": ORG_ID,
            "config_id": CONFIG_ID,
            "kind": "floor_plan",
            "path": "/media/plan.pdf",
            "mime": "application/pdf",
            "size_bytes": 1024,
        }
    )
    assert media["id"] == MEDIA_ID
    assert "::config_media_kind" in conn.fetchrow_calls[0][0]

    media_rows = await repo.list_media(organization_id=ORG_ID, config_id=CONFIG_ID)
    assert len(media_rows) == 1

    assert await repo.delete_media(organization_id=ORG_ID, config_id=CONFIG_ID, media_id=MEDIA_ID)
