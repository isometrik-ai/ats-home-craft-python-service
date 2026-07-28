"""Unit tests for ContactCompaniesRepository with fake connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contact_companies_repository import (
    ContactCompaniesRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
COMPANY_ID = "770e8400-e29b-41d4-a716-446655440002"
COMPANY_ID_2 = "880e8400-e29b-41d4-a716-446655440003"


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
        return None


@pytest.mark.asyncio
async def test_apply_companies_update_delta_returns_created_id():
    conn = _FakeConn(val=COMPANY_ID)
    repo = ContactCompaniesRepository(db_connection=conn)

    created_id = await repo.apply_companies_update_delta(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        remove_company_ids=[COMPANY_ID_2],
        add_company_ids=[COMPANY_ID],
        set_primary_company_ids=[COMPANY_ID],
        unset_primary_company_ids=[COMPANY_ID_2],
        create_company_name=" Acme Corp ",
        create_is_primary=True,
    )

    assert created_id == COMPANY_ID
    query, args = conn.fetchval_calls[0]
    assert "WITH unset_primary AS" in query
    assert "INSERT INTO companies" in query
    assert args[0] == ORG_ID
    assert args[1] == CONTACT_ID
    assert args[5] == "Acme Corp"


@pytest.mark.asyncio
async def test_apply_companies_update_delta_no_create_returns_none():
    conn = _FakeConn(val=None)
    repo = ContactCompaniesRepository(db_connection=conn)

    created_id = await repo.apply_companies_update_delta(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        remove_company_ids=[],
        add_company_ids=[COMPANY_ID],
        set_primary_company_ids=[],
        unset_primary_company_ids=[],
        create_company_name="   ",
        create_is_primary=False,
    )

    assert created_id is None
    _, args = conn.fetchval_calls[0]
    assert args[5] is None


@pytest.mark.asyncio
async def test_list_distinct_company_ids_for_contacts_empty_input():
    conn = _FakeConn()
    repo = ContactCompaniesRepository(db_connection=conn)

    result = await repo.list_distinct_company_ids_for_contacts(
        organization_id=ORG_ID,
        contact_ids=[],
    )

    assert result == []
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_list_distinct_company_ids_for_contacts_returns_sorted_ids():
    conn = _FakeConn(rows=[{"company_id": COMPANY_ID}, {"company_id": COMPANY_ID_2}])
    repo = ContactCompaniesRepository(db_connection=conn)

    result = await repo.list_distinct_company_ids_for_contacts(
        organization_id=ORG_ID,
        contact_ids=[CONTACT_ID],
    )

    assert result == [COMPANY_ID, COMPANY_ID_2]
    query, args = conn.fetch_calls[0]
    assert "SELECT DISTINCT cc.company_id::text" in query
    assert "co.status != 'deleted'" in query
    assert args == (ORG_ID, [CONTACT_ID])


@pytest.mark.asyncio
async def test_apply_contacts_update_delta_executes_batch_sql():
    conn = _FakeConn()
    repo = ContactCompaniesRepository(db_connection=conn)

    await repo.apply_contacts_update_delta(
        organization_id=ORG_ID,
        company_id=COMPANY_ID,
        remove_contact_ids=[CONTACT_ID],
        add_contact_ids=["990e8400-e29b-41d4-a716-446655440004"],
        set_primary_contact_id=CONTACT_ID,
        unset_primary_contact_ids=[CONTACT_ID],
    )

    query, args = conn.execute_calls[0]
    assert "WITH unset_primary AS" in query
    assert "INSERT INTO contact_companies" in query
    assert "primary_set AS" in query
    assert args[0] == ORG_ID
    assert args[1] == COMPANY_ID
    assert args[4] == CONTACT_ID


@pytest.mark.asyncio
async def test_get_contact_companies_snapshot():
    companies = [
        {"company_id": COMPANY_ID, "name": "Acme", "industry": "Tech", "is_primary": True},
    ]
    conn = _FakeConn(row={"companies": companies})
    repo = ContactCompaniesRepository(db_connection=conn)

    snapshot = await repo.get_contact_companies_snapshot(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )

    assert snapshot == companies
    query, args = conn.fetchrow_calls[0]
    assert "jsonb_agg" in query
    assert args == (ORG_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_get_contact_companies_snapshot_empty_row():
    conn = _FakeConn(row=None)
    repo = ContactCompaniesRepository(db_connection=conn)

    assert (
        await repo.get_contact_companies_snapshot(
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
        )
        == []
    )


@pytest.mark.asyncio
async def test_get_company_contacts_snapshot():
    contacts = [
        {
            "id": CONTACT_ID,
            "first_name": "Jane",
            "last_name": "Doe",
            "is_primary": True,
        },
    ]
    conn = _FakeConn(val=CONTACT_ID, row={"contacts": contacts})
    repo = ContactCompaniesRepository(db_connection=conn)

    snapshot = await repo.get_company_contacts_snapshot(
        organization_id=ORG_ID,
        company_id=COMPANY_ID,
    )

    assert snapshot == contacts
    primary_query, primary_args = conn.fetchval_calls[0]
    assert "SELECT primary_contact_id" in primary_query
    assert primary_args == (ORG_ID, COMPANY_ID)

    contacts_query, contacts_args = conn.fetchrow_calls[0]
    assert "jsonb_agg" in contacts_query
    assert contacts_args == (ORG_ID, COMPANY_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_get_company_contacts_snapshot_no_row():
    conn = _FakeConn(val=None, row=None)
    repo = ContactCompaniesRepository(db_connection=conn)

    assert (
        await repo.get_company_contacts_snapshot(
            organization_id=ORG_ID,
            company_id=COMPANY_ID,
        )
        == []
    )
