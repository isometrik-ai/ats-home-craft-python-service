"""Unit tests for LeadStageRepository with fake asyncpg connection."""

import pytest

from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        self.fetchval_calls = []
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.execute_calls = []
        self.fetchval_result = None
        self.fetch_result = []
        self.fetchrow_result = None

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_count_stages_returns_count():
    """count_stages returns total active stages for org."""
    conn = _FakeConn()
    conn.fetchval_result = 3
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.count_stages("org-1")

    assert result == 3
    query, args = conn.fetchval_calls[0]
    assert "SELECT COUNT(*)::int" in query
    assert "FROM lead_stages" in query
    assert "organization_id = $1" in query
    assert args == ("org-1",)


@pytest.mark.asyncio
async def test_get_max_sort_order_returns_zero_when_empty():
    """get_max_sort_order returns COALESCE(max, 0)."""
    conn = _FakeConn()
    conn.fetchval_result = 0
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.get_max_sort_order("org-1")

    assert result == 0
    query, args = conn.fetchval_calls[0]
    assert "COALESCE(MAX(sort_order), 0)::int" in query
    assert "organization_id = $1" in query
    assert args == ("org-1",)


@pytest.mark.asyncio
async def test_check_stage_key_exists_casts_to_bool():
    """check_stage_key_exists returns bool from EXISTS query."""
    conn = _FakeConn()
    conn.fetchval_result = 1
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.check_stage_key_exists("org-1", "qualified")

    assert result is True
    query, args = conn.fetchval_calls[0]
    assert "SELECT EXISTS" in query
    assert "stage_key = $2" in query
    assert args == ("org-1", "qualified")


@pytest.mark.asyncio
async def test_shift_sort_orders_for_insert_executes_update():
    """shift_sort_orders_for_insert bumps orders at target position."""
    conn = _FakeConn()
    repo = LeadStageRepository(db_connection=conn)

    await repo.shift_sort_orders_for_insert("org-1", 2)

    query, args = conn.execute_calls[0]
    assert "UPDATE lead_stages" in query
    assert "SET sort_order = sort_order + 1" in query
    assert "sort_order >= $2" in query
    assert args == ("org-1", 2)


@pytest.mark.asyncio
async def test_create_stage_inserts_and_returns_dict():
    """create_stage inserts row and returns dict payload."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "stage-1",
        "stage_name": "Qualified",
        "stage_key": "qualified",
        "description": "Warm lead",
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    repo = LeadStageRepository(db_connection=conn)
    stage_data = {
        "organization_id": "org-1",
        "stage_name": "Qualified",
        "stage_key": "qualified",
        "description": "Warm lead",
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
    }

    result = await repo.create_stage(stage_data)

    assert result["id"] == "stage-1"
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO lead_stages" in query
    assert "RETURNING" in query
    assert args == (
        "org-1",
        "Qualified",
        "qualified",
        "Warm lead",
        "blue",
        2,
        False,
        False,
    )


@pytest.mark.asyncio
async def test_get_stages_by_organization_returns_rows():
    """get_stages_by_organization returns ordered stage list."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "stage-1",
            "stage_name": "New",
            "stage_key": "new",
            "description": None,
            "color": "blue",
            "sort_order": 1,
            "is_initial": True,
            "is_final": False,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.get_stages_by_organization("org-1")

    assert len(result) == 1
    assert result[0]["stage_key"] == "new"
    query, args = conn.fetch_calls[0]
    assert "FROM lead_stages" in query
    assert "ORDER BY sort_order ASC" in query
    assert args == ("org-1",)


@pytest.mark.asyncio
async def test_get_stage_by_id_returns_row_or_none():
    """get_stage_by_id returns stage dict when found and None otherwise."""
    conn = _FakeConn()
    repo = LeadStageRepository(db_connection=conn)

    conn.fetchrow_result = {
        "id": "stage-1",
        "stage_name": "Qualified",
        "stage_key": "qualified",
        "description": "Warm lead",
        "color": "green",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    found = await repo.get_stage_by_id("org-1", "stage-id")
    assert found is not None
    assert found["stage_name"] == "Qualified"
    query, args = conn.fetchrow_calls[0]
    assert "AND id = $2::uuid" in query
    assert "LIMIT 1" in query
    assert args == ("org-1", "stage-id")

    conn.fetchrow_result = None
    missing = await repo.get_stage_by_id("org-1", "missing-id")
    assert missing is None
