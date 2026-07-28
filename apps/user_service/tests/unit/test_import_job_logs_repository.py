"""Unit tests for ImportJobLogsRepository with fake connection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.import_job_logs_repository import (
    ImportJobLogsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
JOB_ID = "660e8400-e29b-41d4-a716-446655440001"
CREATED = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_result="INSERT 0 1"):
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
async def test_list_logs_returns_parsed_items():
    """List logs joins import_jobs and normalizes payload/timestamps."""
    conn = _FakeConn(
        rows=[
            {
                "total": 1,
                "job_id": "imp_abc",
                "job_status": "completed",
                "payload": {"imported": 10},
                "created_at": CREATED,
                "updated_at": UPDATED,
            }
        ]
    )
    repo = ImportJobLogsRepository(db_connection=conn)

    items, total = await repo.list_logs(organization_id=ORG_ID, page=1, page_size=50)

    assert total == 1
    assert len(items) == 1
    assert items[0]["job_id"] == "imp_abc"
    assert items[0]["job_status"] == "completed"
    assert items[0]["payload"] == {"imported": 10}
    assert items[0]["created_at"] == CREATED.isoformat()
    assert items[0]["updated_at"] == UPDATED.isoformat()
    query, args = conn.fetch_calls[0]
    assert "FROM import_job_logs l" in query
    assert "JOIN import_jobs j" in query
    assert args == (ORG_ID, 50, 0)


@pytest.mark.asyncio
async def test_list_logs_empty():
    """Empty log list returns zero total."""
    conn = _FakeConn(rows=[])
    repo = ImportJobLogsRepository(db_connection=conn)

    items, total = await repo.list_logs(organization_id=ORG_ID)

    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_logs_pagination_defaults():
    """Pagination clamps invalid page/page_size to minimum 1."""
    conn = _FakeConn(rows=[])
    repo = ImportJobLogsRepository(db_connection=conn)

    await repo.list_logs(organization_id=ORG_ID, page=0, page_size=0)

    assert conn.fetch_calls[0][1] == (ORG_ID, 1, 0)


@pytest.mark.asyncio
async def test_list_logs_page_two_offset():
    """Page 2 applies correct offset."""
    conn = _FakeConn(rows=[])
    repo = ImportJobLogsRepository(db_connection=conn)

    await repo.list_logs(organization_id=ORG_ID, page=2, page_size=25)

    assert conn.fetch_calls[0][1] == (ORG_ID, 25, 25)


@pytest.mark.asyncio
async def test_list_logs_null_payload_and_timestamps():
    """Missing payload and timestamps normalize to safe defaults."""
    conn = _FakeConn(
        rows=[
            {
                "total": 1,
                "job_id": None,
                "job_status": None,
                "payload": None,
                "created_at": None,
                "updated_at": None,
            }
        ]
    )
    repo = ImportJobLogsRepository(db_connection=conn)

    items, _ = await repo.list_logs(organization_id=ORG_ID)

    assert items[0]["job_id"] == ""
    assert items[0]["job_status"] == ""
    assert items[0]["payload"] == {}
    assert items[0]["created_at"] is None
    assert items[0]["updated_at"] is None


@pytest.mark.asyncio
async def test_upsert_payload():
    """Upsert payload inserts or updates log row."""
    conn = _FakeConn()
    repo = ImportJobLogsRepository(db_connection=conn)

    await repo.upsert_payload(
        organization_id=ORG_ID,
        job_id=JOB_ID,
        payload={"rows_processed": 5},
    )

    query, args = conn.execute_calls[0]
    assert "INSERT INTO import_job_logs" in query
    assert "ON CONFLICT (job_id)" in query
    assert args[0] == ORG_ID
    assert args[1] == JOB_ID
