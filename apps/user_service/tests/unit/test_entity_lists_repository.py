"""Unit tests for EntityListsRepository with fake connection."""

import pytest

from apps.user_service.app.db.repositories.entity_lists_repository import (
    EntityListsRepository,
)
from apps.user_service.app.schemas.enums import EntityListStatus, EntityType


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.fetchrow_calls = []
        self.fetch_calls = []

    async def fetchrow(self, query, *args):
        """Record fetchrow call."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetch(self, query, *args):
        """Record fetch call."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows


@pytest.mark.asyncio
async def test_get_active_list_id_by_name():
    """Lookup scopes by org, name, entity_type, and status."""
    conn = _FakeConn(row={"id": "list-1"})
    repo = EntityListsRepository(db_connection=conn)

    list_id = await repo.get_active_list_id_by_name(
        organization_id="org-1",
        name="VIP",
        entity_type=EntityType.CONTACT,
    )

    assert list_id == "list-1"
    query, args = conn.fetchrow_calls[0]
    assert "FROM entity_lists" in query
    assert args == ("org-1", "VIP", EntityType.CONTACT.value)


@pytest.mark.asyncio
async def test_get_list():
    """get_list returns dict row scoped to organization."""
    conn = _FakeConn(row={"id": "list-1", "name": "VIP"})
    repo = EntityListsRepository(db_connection=conn)

    row = await repo.get_list(organization_id="org-1", list_id="list-1")

    assert row["name"] == "VIP"
    query, args = conn.fetchrow_calls[0]
    assert args == ("list-1", "org-1")


@pytest.mark.asyncio
async def test_list_member_ids():
    """list_member_ids paginates membership rows."""
    conn = _FakeConn(rows=[{"entity_id": "c1", "total_count": 2}])
    repo = EntityListsRepository(db_connection=conn)

    ids, total = await repo.list_member_ids(list_id="list-1", limit=10, offset=0)

    assert ids == ["c1"]
    assert total == 2
    query, args = conn.fetch_calls[0]
    assert "entity_list_members" in query
    assert args == ("list-1", 10, 0)


@pytest.mark.asyncio
async def test_list_lists_with_counts():
    """Contact lists join contacts table for enrichment counts."""
    conn = _FakeConn(
        rows=[
            {
                "id": "list-1",
                "total_count": 1,
                "total_items": 3,
                "enriched": 1,
                "pending": 1,
                "failed": 0,
            }
        ]
    )
    repo = EntityListsRepository(db_connection=conn)

    items, total = await repo.list_lists_with_counts_for_entity_type(
        organization_id="org-1",
        entity_type=EntityType.CONTACT,
        status=EntityListStatus.ACTIVE,
        search="vip",
        limit=20,
        offset=0,
    )

    assert total == 1
    assert items[0]["id"] == "list-1"
    query, _ = conn.fetch_calls[0]
    assert "FROM entity_lists el" in query
    assert "contacts" in query


@pytest.mark.asyncio
async def test_create_list_empty_entities():
    """create_list inserts list even when entity_ids empty."""
    conn = _FakeConn(row={"list": "{}", "members": "{}"})
    repo = EntityListsRepository(db_connection=conn)

    result = await repo.create_list(
        organization_id="org-1",
        name="New",
        entity_type=EntityType.LEAD,
        description=None,
        tags=[],
        entity_ids=[],
    )

    assert "list" in result
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO entity_lists" in query
    assert args[5] == EntityListStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_get_list_details_not_found():
    """get_list_details returns None when row missing."""
    conn = _FakeConn(row=None)
    repo = EntityListsRepository(db_connection=conn)

    assert await repo.get_list_details(organization_id="org-1", list_id="missing") is None


@pytest.mark.asyncio
async def test_list_lists_with_counts_for_company_type():
    """Company lists should use the companies join configuration."""
    conn = _FakeConn(rows=[{"id": "list-1", "total_count": 1, "total_items": 0}])
    repo = EntityListsRepository(db_connection=conn)

    items, total = await repo.list_lists_with_counts_for_entity_type(
        organization_id="org-1",
        entity_type=EntityType.COMPANY,
        status=EntityListStatus.ACTIVE,
        search=None,
        limit=10,
        offset=0,
    )

    assert total == 1
    query, _ = conn.fetch_calls[0]
    assert "companies" in query


@pytest.mark.asyncio
async def test_get_list_details_parses_items_json():
    """get_list_details should coerce items JSON into Python lists."""
    conn = _FakeConn(row={"id": "list-1", "items": '[{"entity_id": "c1"}]'})
    repo = EntityListsRepository(db_connection=conn)

    details = await repo.get_list_details(organization_id="org-1", list_id="list-1")

    assert details["items"] == [{"entity_id": "c1"}]


@pytest.mark.asyncio
async def test_update_list_coerces_membership_results():
    """update_list should parse add/remove membership JSON payloads."""
    conn = _FakeConn(
        row={
            "id": "list-1",
            "organization_id": "org-1",
            "entity_type": EntityType.CONTACT.value,
            "add_result": '{"requested": 1, "added": 1}',
            "remove_result": '{"requested": 0, "removed": 0}',
        }
    )
    repo = EntityListsRepository(db_connection=conn)

    result = await repo.update_list(
        organization_id="org-1",
        list_id="list-1",
        update_data={"name": "VIP"},
    )

    assert result["add_result"]["added"] == 1
    assert "UPDATE entity_lists" in conn.fetchrow_calls[0][0]
