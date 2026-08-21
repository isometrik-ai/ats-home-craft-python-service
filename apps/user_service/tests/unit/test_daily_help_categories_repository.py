"""Unit tests for DailyHelpCategoriesRepository."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.daily_help_categories_repository import (
    DailyHelpCategoriesRepository,
)

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
CATEGORY = "33333333-3333-3333-3333-333333333333"


class _FakeConn:
    def __init__(self, *, rows=None, row=None, val=0):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val


def _category_row(**overrides) -> dict:
    base = {
        "id": CATEGORY,
        "organization_id": ORG,
        "project_id": PROJECT,
        "name": "Maid",
        "sort_order": 1,
        "status": "active",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_by_project_with_and_without_status():
    conn = _FakeConn(rows=[_category_row()])
    repo = DailyHelpCategoriesRepository(db_connection=conn)

    items = await repo.list_by_project(organization_id=ORG, project_id=PROJECT)
    assert items[0]["name"] == "Maid"
    assert "ORDER BY c.sort_order" in conn.fetch_calls[0][0]

    conn.fetch_calls.clear()
    await repo.list_by_project(
        organization_id=ORG,
        project_id=PROJECT,
        status="active",
    )
    assert "c.status = $3::daily_help_category_status" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_get_by_id():
    conn = _FakeConn(row=_category_row())
    repo = DailyHelpCategoriesRepository(db_connection=conn)
    found = await repo.get_by_id(
        organization_id=ORG,
        project_id=PROJECT,
        category_id=CATEGORY,
    )
    assert found["id"] == CATEGORY

    conn.row = None
    assert (
        await repo.get_by_id(
            organization_id=ORG,
            project_id=PROJECT,
            category_id=CATEGORY,
        )
        is None
    )


@pytest.mark.asyncio
async def test_insert_category():
    conn = _FakeConn(row={"id": CATEGORY, **_category_row()})
    repo = DailyHelpCategoriesRepository(db_connection=conn)
    created = await repo.insert(
        organization_id=ORG,
        project_id=PROJECT,
        name="  Maid  ",
        sort_order=1,
        status="active",
        created_by_user_id="user-1",
    )
    assert created["name"] == "Maid"
    _, args = conn.fetchrow_calls[0]
    assert args[2] == "Maid"


@pytest.mark.asyncio
async def test_update_category_paths():
    conn = _FakeConn(row=_category_row())
    repo = DailyHelpCategoriesRepository(db_connection=conn)

    unchanged = await repo.update(
        organization_id=ORG,
        project_id=PROJECT,
        category_id=CATEGORY,
        fields={},
    )
    assert unchanged["id"] == CATEGORY

    conn.fetchrow_calls.clear()
    patched = await repo.update(
        organization_id=ORG,
        project_id=PROJECT,
        category_id=CATEGORY,
        fields={"name": "Cook", "status": "inactive"},
        updated_by_user_id="user-2",
    )
    assert patched["status"] == "active"
    update_query, _ = conn.fetchrow_calls[0]
    assert "status = $5::daily_help_category_status" in update_query
    assert "updated_by_user_id = $6::uuid" in update_query

    conn.row = None
    assert (
        await repo.update(
            organization_id=ORG,
            project_id=PROJECT,
            category_id=CATEGORY,
            fields={"name": "Missing"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_count_profiles_using_category():
    conn = _FakeConn(val=7)
    repo = DailyHelpCategoriesRepository(db_connection=conn)
    count = await repo.count_profiles_using_category(
        organization_id=ORG,
        project_id=PROJECT,
        category_id=CATEGORY,
    )
    assert count == 7
    assert "p.status <> 'deleted'" in conn.fetchval_calls[0][0]
