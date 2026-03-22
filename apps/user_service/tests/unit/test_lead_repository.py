"""Unit tests for LeadRepository (fake asyncpg connection)."""

import pytest

from apps.user_service.app.db.repositories.lead_repository import (
    CREATE_LEAD_COLUMNS,
    LeadRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self.fetchrow_result = None
        self.fetch_result = []
        self.fetchval_result = None

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_delete_lead_returns_row():
    """delete_lead issues DELETE scoped by organization and returns the removed row."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "organization_id": "org-1", "client_id": "c1"}
    repo = LeadRepository(db_connection=conn)

    result = await repo.delete_lead("org-1", "lead-1")

    assert result is not None
    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "DELETE FROM leads" in query
    assert "WHERE organization_id = $1" in query
    assert "id = $2::uuid" in query
    assert conn.fetchrow_calls[0][1] == ("org-1", "lead-1")


@pytest.mark.asyncio
async def test_delete_leads_by_client_id():
    """delete_leads_by_client_id deletes all leads for a client id."""
    conn = _FakeConn()
    repo = LeadRepository(db_connection=conn)

    result = await repo.delete_leads_by_client_id("client-1")

    assert result is True
    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "DELETE FROM leads" in query
    assert "client_id = $1" in query
    assert "client-1" in conn.execute_calls[0][1]


@pytest.mark.asyncio
async def test_create_lead_full_column_insert():
    """create_lead issues a single INSERT with the canonical column set."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "client_id": "c1"}
    repo = LeadRepository(db_connection=conn)

    await repo.create_lead(
        {
            "client_id": "c1",
            "organization_id": "org-1",
            "lead_status": "prospect",
        }
    )

    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "INSERT INTO leads" in query
    for col in CREATE_LEAD_COLUMNS:
        assert col in query


@pytest.mark.asyncio
async def test_update_lead_calls_fetchrow():
    """update_lead builds UPDATE scoped by organization and returns the row."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "organization_id": "org-1"}
    repo = LeadRepository(db_connection=conn)

    result = await repo.update_lead(
        "org-1",
        "lead-1",
        {"lead_status": "qualified", "notes": "Updated"},
    )

    assert result is not None
    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE leads" in query
    assert "WHERE organization_id = $1" in query
    assert "id = $2::uuid" in query
