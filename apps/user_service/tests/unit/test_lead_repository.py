"""Unit tests for LeadRepository (fake asyncpg connection)."""

import json
from typing import Any

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
        self.fetchrow_sequence: list[Any] | None = None
        self.fetch_result = []
        self.fetchval_result = None

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        if self.fetchrow_sequence:
            return self.fetchrow_sequence.pop(0)
        return self.fetchrow_result

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_delete_lead_returns_row():
    """delete_lead issues DELETE scoped by organization and returns the removed row."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "organization_id": "org-1", "client_company_id": None}
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
    """delete_leads_by_client_id removes lead_contacts and company-linked leads."""
    conn = _FakeConn()
    repo = LeadRepository(db_connection=conn)

    result = await repo.delete_leads_by_client_id("client-1")

    assert result is True
    assert len(conn.execute_calls) == 2
    assert "DELETE FROM lead_contacts" in conn.execute_calls[0][0]
    assert "DELETE FROM leads" in conn.execute_calls[1][0]
    assert conn.execute_calls[0][1] == ("client-1",)
    assert conn.execute_calls[1][1] == ("client-1",)


@pytest.mark.asyncio
async def test_fetch_lead_reference_validation_with_stage():
    """fetch_lead_reference_validation uses one fetchrow with stage + client types."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "stage_exists": True,
        "client_types": {"c1": "company", "c2": "person"},
    }
    repo = LeadRepository(db_connection=conn)

    ok, types_map = await repo.fetch_lead_reference_validation(
        "org-1",
        ["c1", "c2"],
        stage_id="stage-1",
    )

    assert ok is True
    assert types_map == {"c1": "company", "c2": "person"}
    assert len(conn.fetchrow_calls) == 1
    assert "lead_stages" in conn.fetchrow_calls[0][0]
    assert "jsonb_object_agg" in conn.fetchrow_calls[0][0]
    assert conn.fetchrow_calls[0][1] == ("org-1", "stage-1", ["c1", "c2"])


@pytest.mark.asyncio
async def test_fetch_create_reference_jsonb_as_string():
    """Production asyncpg decodes JSONB as a JSON string, not a dict."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "stage_exists": True,
        "client_types": '{"c1": "company", "c2": "person"}',
    }
    repo = LeadRepository(db_connection=conn)

    ok, types_map = await repo.fetch_lead_reference_validation(
        "org-1",
        ["c1", "c2"],
        stage_id="stage-1",
    )

    assert ok is True
    assert types_map == {"c1": "company", "c2": "person"}


@pytest.mark.asyncio
async def test_fetch_lead_validation_empty_client_ids():
    """Empty client id list still checks stage; client_types coalesce to empty dict."""
    conn = _FakeConn()
    conn.fetchrow_result = {"stage_exists": False, "client_types": None}
    repo = LeadRepository(db_connection=conn)

    ok, types_map = await repo.fetch_lead_reference_validation("o", [], stage_id="s")

    assert ok is False
    assert types_map == {}
    assert conn.fetchrow_calls[0][1][2] == []


@pytest.mark.asyncio
async def test_fetch_lead_reference_validation_no_db_when_empty_types_only():
    """When no stage and no client ids, skip the database round trip."""
    conn = _FakeConn()
    repo = LeadRepository(db_connection=conn)

    ok, types_map = await repo.fetch_lead_reference_validation("o", [], stage_id=None)

    assert ok is None
    assert types_map == {}
    assert not conn.fetchrow_calls


@pytest.mark.asyncio
async def test_create_lead_full_column_insert():
    """create_lead with no contacts: single INSERT (no empty uuid[] bind)."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "client_id": "c1"}
    repo = LeadRepository(db_connection=conn)

    row = {
        "organization_id": "org-1",
        "name": "L",
        "stage_id": "22222222-2222-2222-2222-222222222222",
        "client_company_id": None,
        "lead_source": None,
        "referral_source": None,
        "lead_score": None,
        "deal_type": "new_business",
        "priority": None,
        "close_date": None,
        "amount": None,
        "description": None,
        "notes": [],
        "custom_fields": [],
        "owner_id": None,
    }
    await repo.create_lead(row)

    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "WITH new_lead AS" not in query
    assert "INSERT INTO leads" in query
    assert "RETURNING *" in query
    assert "lead_contacts" not in query
    for col in CREATE_LEAD_COLUMNS:
        assert col in query
    assert len(args) == len(CREATE_LEAD_COLUMNS)


@pytest.mark.asyncio
async def test_create_lead_with_contacts_one_statement():
    """create_lead with contacts: CTE lead + lead_contacts via unnest."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1"}
    repo = LeadRepository(db_connection=conn)

    row = {
        "organization_id": "org-1",
        "name": "L",
        "stage_id": "22222222-2222-2222-2222-222222222222",
        "client_company_id": None,
        "lead_source": None,
        "referral_source": None,
        "lead_score": None,
        "deal_type": "new_business",
        "priority": None,
        "close_date": None,
        "amount": None,
        "description": None,
        "notes": [],
        "custom_fields": [],
        "owner_id": None,
    }
    await repo.create_lead(
        row, contacts=[("33333333-3333-3333-3333-333333333333", "decision_maker")]
    )

    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "WITH new_lead AS" in query
    assert "INSERT INTO lead_contacts" in query
    assert "unnest" in query
    assert args[-2:] == (
        ["33333333-3333-3333-3333-333333333333"],
        ["decision_maker"],
    )


@pytest.mark.asyncio
async def test_update_lead_calls_fetchrow():
    """update_lead builds UPDATE scoped by organization and returns the row."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "organization_id": "org-1"}
    repo = LeadRepository(db_connection=conn)

    result = await repo.update_lead(
        "org-1",
        "lead-1",
        {"deal_type": "existing_business", "notes": [{"title": "T", "content": "U"}]},
    )

    assert result is not None
    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE leads" in query
    assert "WHERE organization_id = $1" in query
    assert "id = $2::uuid" in query


@pytest.mark.asyncio
async def test_count_leads_filtered_returns_int():
    """count_leads_filtered uses fetchrow and casts COUNT(*) to int."""
    conn = _FakeConn()
    conn.fetchrow_result = {"n": "3"}
    repo = LeadRepository(db_connection=conn)

    result = await repo.count_leads_filtered(
        "org-1",
        stage_id="22222222-2222-2222-2222-222222222222",
        search="lead",
    )

    assert result == 3
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "COUNT(*)::int" in query
    assert args == (
        "org-1",
        "22222222-2222-2222-2222-222222222222",
        "%lead%",
    )


@pytest.mark.asyncio
async def test_list_leads_page_uses_limit_offset():
    """list_leads_page uses LIMIT/OFFSET and passes optional filters."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "lead-1",
            "client_company_id": None,
            "name": "Lead A",
        }
    ]
    repo = LeadRepository(db_connection=conn)

    rows = await repo.list_leads_page(
        "org-1",
        stage_id=None,
        search="lead",
        limit=10,
        offset=20,
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "lead-1"
    assert len(conn.fetch_calls) == 1
    query, args = conn.fetch_calls[0]
    assert "FROM leads" in query
    assert "LIMIT $4::int" in query
    assert "OFFSET $5::int" in query
    assert args == ("org-1", None, "%lead%", 10, 20)


@pytest.mark.asyncio
async def test_list_leads_kanban_fetch():
    """list_leads_for_kanban returns all matching rows and does not apply LIMIT/OFFSET."""
    conn = _FakeConn()
    conn.fetch_result = [{"id": "lead-1", "client_company_id": None, "name": "Lead A"}]
    repo = LeadRepository(db_connection=conn)

    rows = await repo.list_leads_for_kanban(
        "org-1",
        stage_id=None,
        search="lead",
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "lead-1"
    assert len(conn.fetch_calls) == 1
    query, args = conn.fetch_calls[0]
    assert "FROM leads" in query
    assert "LIMIT $4::int" not in query
    assert args == ("org-1", None, "%lead%")


@pytest.mark.asyncio
async def test_get_lead_detail_by_id_returns_row_or_none():
    """get_lead_detail_by_id returns a dict when found and None when missing."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "client_company_id": None}
    repo = LeadRepository(db_connection=conn)

    found = await repo.get_lead_detail_by_id("org-1", "lead-1")
    assert found["client_company_id"] is None
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "LIMIT 1" in query
    assert args == ("org-1", "lead-1")

    conn.fetchrow_result = None
    missing = await repo.get_lead_detail_by_id("org-1", "missing")
    assert missing is None


@pytest.mark.asyncio
async def test_get_lead_detail_with_contacts_by_id():
    """get_lead_detail_with_contacts_by_id returns contacts + lead data, or None when missing."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "lead-1",
            "client_company_id": None,
            "contact_client_id": "c1",
            "label": "decision_maker",
            "contact_name": "P",
        },
    ]
    repo = LeadRepository(db_connection=conn)

    found = await repo.get_lead_detail_with_contacts_by_id("org-1", "lead-1")
    assert found is not None
    assert found["id"] == "lead-1"
    assert isinstance(found["contacts"], list)
    assert found["contacts"][0]["contact_client_id"] == "c1"
    assert found["contacts"][0]["label"] == "decision_maker"
    assert len(conn.fetchrow_calls) == 0
    assert len(conn.fetch_calls) == 1
    assert "FROM leads l" in conn.fetch_calls[0][0]
    assert conn.fetch_calls[0][1] == ("org-1", "lead-1")

    conn.fetch_result = []
    missing = await repo.get_lead_detail_with_contacts_by_id("org-1", "missing")
    assert missing is None


@pytest.mark.asyncio
async def test_update_lead_empty_uses_select():
    """update_lead returns existing row via get_lead_detail_by_id when filtered is empty."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "lead-1", "client_company_id": None}
    repo = LeadRepository(db_connection=conn)

    result = await repo.update_lead("org-1", "lead-1", {})

    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE leads" not in query
    assert "FROM leads l" in query


@pytest.mark.asyncio
async def test_update_lead_filters_allowed_fields():
    """update_lead only updates allowed columns and uses $3.. placeholders."""
    conn = _FakeConn()
    conn.fetchrow_sequence = [
        {"id": "lead-1", "name": "New Lead"},
    ]
    repo = LeadRepository(db_connection=conn)

    result = await repo.update_lead(
        "org-1",
        "lead-1",
        {"foo": "bar", "name": "New Lead"},
    )

    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    update_query, update_args = conn.fetchrow_calls[0]
    assert "UPDATE leads" in update_query
    assert "foo" not in update_query
    assert "name = $3" in update_query
    assert update_args == ("org-1", "lead-1", "New Lead")
    assert "LIMIT 1" in update_query


@pytest.mark.asyncio
async def test_update_lead_serializes_custom_fields_as_jsonb():
    """update_lead serializes custom_fields dicts to JSON strings."""
    conn = _FakeConn()
    conn.fetchrow_sequence = [
        {"id": "lead-1", "name": "New Lead"},
    ]
    repo = LeadRepository(db_connection=conn)

    result = await repo.update_lead(
        "org-1",
        "lead-1",
        {"name": "New Lead", "custom_fields": {"x": "y"}},
    )

    assert result["id"] == "lead-1"
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "custom_fields = $4" in query
    assert args[0] == "org-1"
    assert args[1] == "lead-1"
    assert args[2] == "New Lead"
    # JSON serialization is deterministic here; validate structure rather than exact spacing.
    serialized_cf = args[3]
    assert isinstance(serialized_cf, str)
    assert json.loads(serialized_cf) == {"x": "y"}
