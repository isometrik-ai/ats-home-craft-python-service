"""Unit tests for ImportJobRowsRepository with AsyncMock db connection."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.import_job_rows_repository import (
    ImportJobRowsRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
JOB_ID = "660e8400-e29b-41d4-a716-446655440001"


def _mock_conn(*, row=None, rows=None, val=0, execute_result="UPDATE 0"):
    """Build asyncpg-like connection mock."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


@pytest.mark.asyncio
async def test_claim_rows_processing_empty():
    """Empty batch returns empty dict without DB calls."""
    conn = _mock_conn()
    repo = ImportJobRowsRepository(db_connection=conn)

    assert await repo.claim_rows_processing(organization_id=ORG_ID, job_id=JOB_ID, rows=[]) == {}
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_claim_rows_processing_batch():
    """Batch claim inserts then fetches statuses."""
    conn = _mock_conn(rows=[{"row_number": 1, "status": "processing"}])
    repo = ImportJobRowsRepository(db_connection=conn)

    result = await repo.claim_rows_processing(
        organization_id=ORG_ID,
        job_id=JOB_ID,
        rows=[(1, {"email": "a@b.com"})],
    )

    assert result == {1: "processing"}
    conn.execute.assert_awaited_once()
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_row_processing_inserted():
    """Single claim returns status from INSERT RETURNING."""
    conn = _mock_conn(row={"status": "processing"})
    repo = ImportJobRowsRepository(db_connection=conn)

    status = await repo.claim_row_processing(
        organization_id=ORG_ID, job_id=JOB_ID, row_number=2, raw_row={"name": "Jane"}
    )

    assert status == "processing"
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_row_processing_existing():
    """Single claim falls back to SELECT when insert conflicts."""
    conn = _mock_conn()
    conn.fetchrow = AsyncMock(side_effect=[None, {"status": "success"}])
    repo = ImportJobRowsRepository(db_connection=conn)

    status = await repo.claim_row_processing(organization_id=ORG_ID, job_id=JOB_ID, row_number=3)

    assert status == "success"
    assert conn.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_mark_success_bulk_noop():
    """Bulk success skips execute when row list empty."""
    conn = _mock_conn()
    repo = ImportJobRowsRepository(db_connection=conn)

    await repo.mark_success_bulk(organization_id=ORG_ID, job_id=JOB_ID, row_numbers=[])
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_mark_success():
    """Single success update executes status SQL."""
    conn = _mock_conn()
    repo = ImportJobRowsRepository(db_connection=conn)

    await repo.mark_success(organization_id=ORG_ID, job_id=JOB_ID, row_number=1)

    query, args = _sql_args(conn.execute)
    assert "status = 'success'" in query
    assert args[2] == 1


@pytest.mark.asyncio
async def test_mark_errors_bulk():
    """Bulk error update uses unnest parameters."""
    conn = _mock_conn()
    repo = ImportJobRowsRepository(db_connection=conn)

    await repo.mark_errors_bulk(
        organization_id=ORG_ID,
        job_id=JOB_ID,
        errors=[(1, "INVALID", "Bad row", {"email": "x"})],
    )

    query, _ = _sql_args(conn.execute)
    assert "status = 'error'" in query
    assert "unnest" in query


@pytest.mark.asyncio
async def test_mark_error():
    """Single error update sets code and message."""
    conn = _mock_conn()
    repo = ImportJobRowsRepository(db_connection=conn)

    await repo.mark_error(
        organization_id=ORG_ID,
        job_id=JOB_ID,
        row_number=5,
        error_code="DUPLICATE",
        error_message="Already exists",
    )

    query, args = _sql_args(conn.execute)
    assert "error_code = $4" in query
    assert args[3:5] == ("DUPLICATE", "Already exists")


@pytest.mark.asyncio
async def test_list_rows_pagination():
    """List rows returns formatted items and total count."""
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    conn = _mock_conn(
        val=2,
        rows=[
            {
                "row_number": 1,
                "status": "success",
                "error_message": None,
                "raw_row": {"a": 1},
                "created_at": created,
                "updated_at": created,
            }
        ],
    )
    repo = ImportJobRowsRepository(db_connection=conn)

    items, total = await repo.list_rows(organization_id=ORG_ID, job_id=JOB_ID, page=2, page_size=10)

    assert total == 2
    assert items[0]["row_number"] == 1
    assert items[0]["created_at"] == created.isoformat()
    assert conn.fetchval.await_count == 1
    assert conn.fetch.await_count == 1


@pytest.mark.asyncio
async def test_list_error_rows_filters_error_status():
    """Error list scopes to status = error and includes error_code."""
    created = datetime(2026, 2, 1, tzinfo=timezone.utc)
    conn = _mock_conn(
        val=1,
        rows=[
            {
                "row_number": 7,
                "status": "error",
                "error_code": "VALIDATION",
                "error_message": "Missing email",
                "raw_row": None,
                "created_at": created,
                "updated_at": None,
            }
        ],
    )
    repo = ImportJobRowsRepository(db_connection=conn)

    items, total = await repo.list_error_rows(
        organization_id=ORG_ID, job_id=JOB_ID, page=1, page_size=50
    )

    assert total == 1
    assert items[0]["error_code"] == "VALIDATION"
    count_query, _ = _sql_args(conn.fetchval)
    assert "status = 'error'" in count_query
    list_query, list_args = _sql_args(conn.fetch)
    assert "status = 'error'" in list_query
    assert list_args[-2:] == (50, 0)
