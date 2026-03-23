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
async def test_organization_for_new_stage_returns_row_dict():
    """summarize_organization_for_new_stage returns counts and key collision flag."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "total_stages": 3,
        "max_sort_order": 5,
        "stage_key_exists": False,
    }
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.summarize_organization_for_new_stage("org-1", "qualified")

    assert result["total_stages"] == 3
    assert result["max_sort_order"] == 5
    assert result["stage_key_exists"] is False
    query, args = conn.fetchrow_calls[0]
    assert "FROM lead_stages" in query or "lead_stages" in query
    assert "total_stages" in query
    assert "max_sort_order" in query
    assert "stage_key_exists" in query
    assert args == ("org-1", "qualified")


@pytest.mark.asyncio
async def test_adjust_sort_orders_unbounded_upper():
    """adjust_sort_orders with max_sort_order=None shifts every row from min upward."""
    conn = _FakeConn()
    repo = LeadStageRepository(db_connection=conn)

    await repo.adjust_sort_orders("org-1", min_sort_order=2, max_sort_order=None, delta=1)

    query, args = conn.execute_calls[0]
    assert "UPDATE lead_stages" in query
    assert "SET sort_order = sort_order + $4::int" in query
    assert "sort_order >= $2::int" in query
    assert "$3::int IS NULL OR sort_order <= $3::int" in query
    assert args == ("org-1", 2, None, 1)


@pytest.mark.asyncio
async def test_adjust_sort_orders_bounded_range():
    """adjust_sort_orders with max_sort_order set only touches the closed range."""
    conn = _FakeConn()
    repo = LeadStageRepository(db_connection=conn)

    await repo.adjust_sort_orders("org-1", min_sort_order=2, max_sort_order=5, delta=-1)

    query, args = conn.execute_calls[0]
    assert "UPDATE lead_stages" in query
    assert args == ("org-1", 2, 5, -1)


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
    row = {
        "organization_id": "org-1",
        "stage_name": "Qualified",
        "stage_key": "qualified",
        "description": "Warm lead",
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
    }

    result = await repo.create_stage(row)

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
async def test_list_stages_by_organization_returns_rows():
    """list_stages_by_organization returns ordered stage list."""
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

    result = await repo.list_stages_by_organization("org-1")

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


@pytest.mark.asyncio
async def test_get_stage_with_metrics_returns_row():
    """get_stage_by_id_with_organization_metrics returns merged stage and org stats."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "total_stages": 4,
        "key_conflict_count": 0,
        "other_initial_count": 1,
        "other_final_count": 1,
    }
    repo = LeadStageRepository(db_connection=conn)

    row = await repo.get_stage_by_id_with_organization_metrics(
        "org-1",
        "550e8400-e29b-41d4-a716-446655440000",
        "proposed_key",
    )

    assert row is not None
    assert row["total_stages"] == 4
    assert row["key_conflict_count"] == 0
    assert row["stage_key"] == "mid"
    query, args = conn.fetchrow_calls[0]
    assert "WITH org_stats AS" in query
    assert "key_conflict_count" in query
    assert "FROM lead_stages s" in query
    assert args == ("org-1", "550e8400-e29b-41d4-a716-446655440000", "proposed_key")


@pytest.mark.asyncio
async def test_get_stage_with_metrics_none_when_missing():
    """get_stage_by_id_with_organization_metrics returns None when stage is absent."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = LeadStageRepository(db_connection=conn)

    row = await repo.get_stage_by_id_with_organization_metrics("org-1", "missing-id", None)

    assert row is None
    _, args = conn.fetchrow_calls[0]
    assert args[2] == ""


@pytest.mark.asyncio
async def test_update_stage_builds_dynamic_update():
    """update_stage updates only provided columns."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "stage-1",
        "stage_name": "Qualified",
        "stage_key": "new",
        "description": "Warm lead",
        "color": "green",
        "sort_order": 2,
        "is_initial": True,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.update_stage(
        "org-1",
        "stage-id",
        {"stage_name": "Qualified", "color": "green"},
    )

    assert result is not None
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE lead_stages" in query
    assert "stage_name = $3" in query
    assert "color = $4" in query
    assert args == ("org-1", "stage-id", "Qualified", "green")


@pytest.mark.asyncio
async def test_update_empty_payload_selects_existing_row():
    """update_stage with no allowed columns issues SELECT via get_stage_by_id."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.update_stage("org-1", "stage-id", {})

    assert result is not None
    assert result["stage_key"] == "mid"
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE lead_stages" not in query
    assert "FROM lead_stages" in query
    assert args == ("org-1", "stage-id")


@pytest.mark.asyncio
async def test_delete_stage_removes_row_and_returns_dict():
    """delete_stage issues DELETE with RETURNING stage columns."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    repo = LeadStageRepository(db_connection=conn)

    result = await repo.delete_stage("org-1", "550e8400-e29b-41d4-a716-446655440000")

    assert result is not None
    assert result["stage_key"] == "mid"
    query, args = conn.fetchrow_calls[0]
    assert "DELETE FROM lead_stages" in query
    assert "RETURNING" in query
    assert args == ("org-1", "550e8400-e29b-41d4-a716-446655440000")

    conn.fetchrow_result = None
    missing = await repo.delete_stage("org-1", "550e8400-e29b-41d4-a716-446655440001")
    assert missing is None
