"""Unit tests for ImportJobsRepository with fake connection."""

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.import_jobs_repository import (
    ImportJobsRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetch(self, query, *args):
        """Record fetch call."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


def test_normalize_job_row():
    """Normalization exposes job_id alias."""
    row = ImportJobsRepository._normalize_job_row(  # pylint: disable=protected-access
        {"job_key": "imp_1", "status": "pending"}
    )
    assert row["job_id"] == "imp_1"


@pytest.mark.asyncio
async def test_create_job():
    """create_job bulk inserts import_jobs row."""
    conn = _FakeConn(
        rows=[
            {
                "job_key": "imp_1",
                "organization_id": "org-1",
                "status": "pending",
            }
        ]
    )
    repo = ImportJobsRepository(db_connection=conn)

    job = await repo.create_job(
        job_id="imp_1",
        organization_id="org-1",
        status="pending",
        file_url="https://example.com/f.csv",
        file_type="csv",
        schema_version=1,
        mapping={"email": "Email"},
        options={"dry_run": False},
    )

    assert job["job_id"] == "imp_1"
    query, _ = conn.fetch_calls[0]
    assert "INSERT INTO import_jobs" in query


@pytest.mark.asyncio
async def test_get_job():
    """get_job scopes by job_key and organization."""
    conn = _FakeConn(row={"job_key": "imp_1", "organization_id": "org-1"})
    repo = ImportJobsRepository(db_connection=conn)

    job = await repo.get_job(job_id="imp_1", organization_id="org-1")

    assert job is not None
    assert job["job_id"] == "imp_1"
    query, args = conn.fetchrow_calls[0]
    assert "FROM import_jobs" in query
    assert args == ("imp_1", "org-1")


@pytest.mark.asyncio
async def test_set_status():
    """set_status updates status and returns row."""
    conn = _FakeConn(row={"job_key": "imp_1", "status": "running", "organization_id": "org-1"})
    repo = ImportJobsRepository(db_connection=conn)

    job = await repo.set_status(job_id="imp_1", organization_id="org-1", status="running")

    assert job["status"] == "running"
    query, _ = conn.fetchrow_calls[0]
    assert "UPDATE import_jobs" in query


@pytest.mark.asyncio
async def test_set_status_and_timestamps():
    """Timestamp update casts timestamptz explicitly."""
    conn = _FakeConn(row={"job_key": "imp_1", "status": "done"})
    repo = ImportJobsRepository(db_connection=conn)
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)

    job = await repo.set_status_and_timestamps(
        job_id="imp_1",
        organization_id="org-1",
        status="running",
        started_at=started,
    )

    assert job is not None
    query, _ = conn.fetchrow_calls[0]
    assert "started_at = $2::timestamptz" in query


@pytest.mark.asyncio
async def test_increment_counters():
    """increment_counters atomically adds progress deltas."""
    conn = _FakeConn(row={"job_key": "imp_1", "processed_rows": 10})
    repo = ImportJobsRepository(db_connection=conn)

    job = await repo.increment_counters(
        job_id="imp_1",
        organization_id="org-1",
        total_rows_delta=5,
        processed_rows_delta=3,
        success_rows_delta=2,
        error_rows_delta=1,
    )

    assert job is not None
    query, args = conn.fetchrow_calls[0]
    assert "total_rows = total_rows + $3" in query
    assert args[2:] == (5, 3, 2, 1)
