"""Unit tests for VisitorLogsRepository query building."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.visitor_logs_repository import (
    VisitorLogsRepository,
)
from apps.user_service.app.schemas.enums import PassEventType, PassType


class _FakeConn:
    """Minimal fake asyncpg connection for repository tests."""

    def __init__(self, *, rows=None, row=None, val=0):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval call and return configured scalar."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val


@pytest.mark.asyncio
async def test_list_logs_date_range_bounds():
    """List query scopes passes to the requested date range."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    start_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 15, tzinfo=timezone.utc)
    await repo.list_logs(
        organization_id="org-1",
        start_at=start_at,
        end_at=end_at,
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.valid_from >= $2" in count_query
    assert "p.valid_from < $3" in count_query
    assert count_args[0] == "org-1"
    assert count_args[1] == start_at
    assert count_args[2] == end_at


@pytest.mark.asyncio
async def test_list_logs_search_filter():
    """Search filter applies ilike predicates."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        search="Ravi",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "guest_name ILIKE" in count_query
    assert count_args[3] == "%Ravi%"


@pytest.mark.asyncio
async def test_list_logs_pass_type_filter():
    """Pass type filter casts to pass_type enum."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        pass_type=PassType.DELIVERY.value,
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.pass_type = $4::pass_type" in count_query
    assert PassType.DELIVERY.value in count_args


@pytest.mark.asyncio
async def test_get_overview_aggregates():
    """Overview query counts visitors, check-ins, deliveries, and daily help."""
    conn = _FakeConn(
        row={
            "total_visitors": 10,
            "in_count": 4,
            "deliveries": 2,
            "daily_help": 3,
        }
    )
    repo = VisitorLogsRepository(db_connection=conn)
    start_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 30, tzinfo=timezone.utc)
    result = await repo.get_overview(
        organization_id="org-1",
        start_at=start_at,
        end_at=end_at,
    )
    assert result["start_at"] == start_at
    assert result["end_at"] == end_at
    assert result["total_visitors"] == 10
    assert result["in_count"] == 4
    assert result["deliveries"] == 2
    assert result["daily_help"] == 3
    args = conn.fetchrow_calls[0][1]
    assert PassEventType.CHECKED_IN.value in args
    assert PassType.DELIVERY.value in args
    assert PassType.SERVICE.value in args
