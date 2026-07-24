"""Unit tests for CompaniesRepository SQL-building with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.companies_repository import (
    CompaniesRepository,
)
from apps.user_service.app.schemas.enums import ClientStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
COMPANY_ID = "660e8400-e29b-41d4-a716-446655440001"


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        """Record fetch and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval and return configured value."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute call."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_get_company_ids_by_names_lookup():
    """Name lookup normalizes lowercase trimmed names."""
    conn = _FakeConn(rows=[{"name_norm": "acme", "id": COMPANY_ID}])
    repo = CompaniesRepository(db_connection=conn)

    mapping = await repo.get_company_ids_by_names(
        organization_id=ORG_ID,
        names=[" Acme "],
    )

    assert mapping == {"acme": COMPANY_ID}
    query, args = conn.fetch_calls[0]
    assert "LOWER(TRIM(name)) = ANY($3::text[])" in query
    assert ClientStatus.DELETED.value in args


@pytest.mark.asyncio
async def test_list_companies_search_filter():
    """List adds name ILIKE when search is provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = CompaniesRepository(db_connection=conn)

    await repo.list_companies(
        organization_id=ORG_ID,
        search="acme",
        status=None,
        page=1,
        page_size=20,
    )

    count_query, count_args = conn.fetchval_calls[0]
    assert "ILIKE" in count_query
    assert "%acme%" in count_args
    list_query, _ = conn.fetch_calls[0]
    assert "member_contacts" in list_query


@pytest.mark.asyncio
async def test_list_companies_status_filter():
    """List adds status predicate when provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = CompaniesRepository(db_connection=conn)

    await repo.list_companies(
        organization_id=ORG_ID,
        search=None,
        status=ClientStatus.ACTIVE.value,
        page=2,
        page_size=10,
    )

    count_query, count_args = conn.fetchval_calls[0]
    assert "co.status = $3" in count_query
    assert ClientStatus.ACTIVE.value in count_args
    _, list_args = conn.fetch_calls[0]
    assert list_args[-2] == 10
    assert list_args[-1] == 10


@pytest.mark.asyncio
async def test_get_company_details_joins():
    """Details query joins contacts, leads, and addresses."""
    conn = _FakeConn(
        row={
            "id": COMPANY_ID,
            "name": "Acme",
            "contacts": [],
            "leads": [],
            "addresses": [],
        }
    )
    repo = CompaniesRepository(db_connection=conn)

    details = await repo.get_company_details(
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
    )

    assert details["name"] == "Acme"
    query, args = conn.fetchrow_calls[0]
    assert "FROM companies co" in query
    assert "contact_companies cc" in query
    assert "company_addresses addr" in query
    assert args[0] == COMPANY_ID
    assert args[1] == ORG_ID


@pytest.mark.asyncio
async def test_update_company_dynamic_set():
    """Update builds dynamic SET for scalar/jsonb columns."""
    conn = _FakeConn(row={"id": COMPANY_ID, "name": "Renamed"})
    repo = CompaniesRepository(db_connection=conn)

    updated = await repo.update_company(
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
        update_data={"name": "Renamed", "phones": []},
    )

    assert updated["name"] == "Renamed"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE companies" in query
    assert "name = $1" in query
    assert "phones = $2::jsonb" in query
    assert args[-3] == COMPANY_ID
    assert args[-2] == ORG_ID
    assert args[-1] == ClientStatus.DELETED.value


@pytest.mark.asyncio
async def test_delete_company_addresses_execute():
    """Delete addresses issues scoped DELETE statement."""
    addr_id = "770e8400-e29b-41d4-a716-446655440002"
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)

    await repo.delete_company_addresses(
        company_id=COMPANY_ID,
        address_ids=[addr_id],
    )

    query, args = conn.execute_calls[0]
    assert "DELETE FROM company_addresses" in query
    assert args[0] == COMPANY_ID
    assert addr_id in args[1]


@pytest.mark.asyncio
async def test_create_companies_bulk_insert():
    """Bulk create inserts required organization_id and name."""
    conn = _FakeConn(rows=[{"id": COMPANY_ID, "name": "Acme"}])
    repo = CompaniesRepository(db_connection=conn)

    created = await repo.create_companies(
        [
            {
                "organization_id": ORG_ID,
                "name": "Acme",
                "status": ClientStatus.ACTIVE.value,
                "phones": [],
            }
        ]
    )

    assert created[0]["name"] == "Acme"
    query, _ = conn.fetch_calls[0]
    assert "INSERT INTO companies" in query
    assert "organization_id" in query
    assert "name" in query


@pytest.mark.asyncio
async def test_get_company_ids_by_names_empty_input():
    """Empty or blank names short-circuit without DB call."""
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)

    assert await repo.get_company_ids_by_names(organization_id=ORG_ID, names=[]) == {}
    assert await repo.get_company_ids_by_names(organization_id=ORG_ID, names=["  "]) == {}
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_create_companies_empty_rows():
    """Bulk create returns empty list for empty input."""
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)
    assert await repo.create_companies([]) == []


@pytest.mark.asyncio
async def test_create_company_with_optional_contact_link_validation():
    """Cannot pass both contact_id and contact_data."""
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)

    with pytest.raises(ValueError, match="only one"):
        await repo.create_company_with_optional_contact_link(
            organization_id=ORG_ID,
            company_data={"name": "Acme"},
            addresses=[],
            contact_id=COMPANY_ID,
            contact_data={"first_name": "Jane"},
            set_primary=True,
        )


@pytest.mark.asyncio
async def test_create_company_with_optional_contact_link_success():
    """Complex CTE insert returns company and contact metadata."""
    conn = _FakeConn(
        row={
            "company_id": COMPANY_ID,
            "company": {"id": COMPANY_ID, "name": "Acme"},
            "contact_id": None,
            "contact": None,
            "contact_found": False,
        }
    )
    repo = CompaniesRepository(db_connection=conn)

    result = await repo.create_company_with_optional_contact_link(
        organization_id=ORG_ID,
        company_data={"name": "Acme", "status": ClientStatus.ACTIVE.value},
        addresses=[{"address_line1": "Line 1", "is_primary": True}],
        contact_id=None,
        contact_data=None,
        set_primary=False,
    )

    assert result["company_id"] == COMPANY_ID
    assert result["company"]["name"] == "Acme"
    assert "WITH contact_exists AS" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_get_company_for_update_parses_json_strings():
    """JSON string aggregates are parsed into Python lists."""
    conn = _FakeConn(
        row={
            "id": COMPANY_ID,
            "name": "Acme",
            "contacts": "[]",
            "leads": "[]",
            "addresses": "not-json",
        }
    )
    repo = CompaniesRepository(db_connection=conn)

    details = await repo.get_company_for_update(
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
    )

    assert details["contacts"] == []
    assert details["addresses"] == []
    assert "FOR UPDATE OF co" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_get_company_for_update_by_enrichment_request_id():
    """Enrichment lookup returns None for blank id."""
    conn = _FakeConn(row={"id": COMPANY_ID, "enrichment_request_id": "req-1"})
    repo = CompaniesRepository(db_connection=conn)

    assert (
        await repo.get_company_for_update_by_enrichment_request_id(enrichment_request_id="") is None
    )

    found = await repo.get_company_for_update_by_enrichment_request_id(
        enrichment_request_id="req-1"
    )
    assert found["id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_update_company_address_empty_returns_none():
    """Empty update_data short-circuits."""
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)
    assert (
        await repo.update_company_address(
            company_id=COMPANY_ID,
            address_id="addr-1",
            update_data={},
        )
        is None
    )


@pytest.mark.asyncio
async def test_delete_company_addresses_noop_on_empty_ids():
    """Delete addresses skips execute when ids empty."""
    conn = _FakeConn()
    repo = CompaniesRepository(db_connection=conn)
    await repo.delete_company_addresses(company_id=COMPANY_ID, address_ids=[])
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_list_companies_dropdown_filters():
    """List adds custom_fields dropdown predicate when provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = CompaniesRepository(db_connection=conn)

    await repo.list_companies(
        organization_id=ORG_ID,
        search=None,
        status=None,
        dropdown_filters={"tier": ["gold", "silver"]},
        page=1,
        page_size=20,
    )

    count_query, _ = conn.fetchval_calls[0]
    assert "co.custom_fields" in count_query
