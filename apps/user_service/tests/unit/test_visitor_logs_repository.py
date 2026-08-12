"""Unit tests for VisitorLogsRepository query building."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.visitor_logs_repository import (
    WALK_IN_LOG_TYPE,
    VisitorLogsRepository,
)
from apps.user_service.app.schemas.enums import (
    PassEventType,
    PassType,
    VisitorLogBucket,
    VisitorLogVisitStatus,
    VisitorType,
)


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
    assert "ci.occurred_at >= $2" in count_query
    assert "ci.occurred_at < $3" in count_query
    assert "w.entered_at >= $2" in count_query
    assert "w.requested_at >= $2" in count_query
    assert "p.valid_from < $3" in count_query
    assert "UNION ALL" in count_query
    assert count_args[0] == "org-1"
    assert count_args[1] == start_at
    assert count_args[2] == end_at


@pytest.mark.asyncio
async def test_list_logs_includes_passes_without_check_in():
    """List query includes passes without gate check-in via LEFT JOIN LATERAL."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "LEFT JOIN LATERAL" in count_query
    assert PassEventType.CHECKED_IN.value in count_query
    assert "visit_status" in count_query


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
    assert "visitor_first_name ILIKE" in count_query
    assert "p.code" in count_query
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
    assert "walk_in_entries" not in count_query
    assert PassType.DELIVERY.value in count_args


@pytest.mark.asyncio
async def test_list_logs_walk_in_type_filter():
    """Walk-in type filter excludes pass branch."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        pass_type=WALK_IN_LOG_TYPE,
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "walk_in_entries" in count_query
    assert "FROM passes p" not in count_query


@pytest.mark.asyncio
async def test_list_logs_walk_in_search_uses_consecutive_params():
    """Walk-in search must not leave unused pass-branch bind parameters."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        pass_type=WALK_IN_LOG_TYPE,
        search="babita",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "walk_in_entries" in count_query
    assert "FROM passes p" not in count_query
    assert "w.visitor_first_name ILIKE $4" in count_query
    assert "$5" not in count_query.split("w.visitor_first_name ILIKE $4")[1].split("ORDER BY")[0]
    assert count_args[3] == "%babita%"


@pytest.mark.asyncio
async def test_get_overview_aggregates():
    """Overview query counts UI card metrics from the expanded union."""
    conn = _FakeConn(
        row={
            "total_entries": 10,
            "inside_now": 3,
            "awaiting_approval": 2,
            "walk_ins": 4,
            "exited": 3,
            "denied_expired": 1,
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
    assert result["total_entries"] == 10
    assert result["inside_now"] == 3
    assert result["awaiting_approval"] == 2
    assert result["walk_ins"] == 4
    assert result["exited"] == 3
    assert result["denied_expired"] == 1
    overview_query = conn.fetchrow_calls[0][0]
    assert "UNION ALL" in overview_query
    assert VisitorLogVisitStatus.INSIDE.value in overview_query
    assert VisitorLogVisitStatus.AWAITING_APPROVAL.value in overview_query
    assert VisitorLogVisitStatus.EXITED.value in overview_query


@pytest.mark.asyncio
async def test_list_logs_unit_and_project_filters():
    """Unit and project filters scope passes to one flat."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.project_id = $4::uuid" in count_query
    assert "p.unit_id = $5::uuid" in count_query
    assert "w.project_id = $6::uuid" in count_query
    assert count_args.count("project-1") == 2
    assert count_args.count("unit-1") == 2


@pytest.mark.asyncio
async def test_get_overview_unit_scope():
    """Overview applies optional unit/project scope via union filters."""
    conn = _FakeConn(
        row={
            "total_entries": 2,
            "inside_now": 1,
            "awaiting_approval": 0,
            "walk_ins": 1,
            "exited": 0,
            "denied_expired": 0,
        }
    )
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.get_overview(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    overview_query = conn.fetchrow_calls[0][0]
    assert "p.project_id = $4::uuid" in overview_query
    assert "p.unit_id = $5::uuid" in overview_query
    assert "walk_in_entries w" in overview_query
    assert "walk_in_visit_units vu" in overview_query


def test_resolve_range_defaults_to_current_month():
    """Omitted bounds default to the current UTC calendar month."""
    start, end = VisitorLogsRepository._resolve_range(start_at=None, end_at=None)
    assert start.tzinfo is not None
    assert end > start
    assert start.day == 1


def test_resolve_range_rejects_partial_bounds():
    """start_at and end_at must be supplied together."""
    with pytest.raises(ValueError, match="must be provided together"):
        VisitorLogsRepository._resolve_range(
            start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end_at=None,
        )


def test_resolve_range_rejects_end_before_start():
    """end_at must be strictly after start_at."""
    start = datetime(2026, 6, 30, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="must be after"):
        VisitorLogsRepository._resolve_range(start_at=start, end_at=end)


def test_resolve_range_adds_utc_to_naive_datetimes():
    """Naive datetimes are treated as UTC."""
    start = datetime(2026, 6, 1)
    end = datetime(2026, 6, 30)
    resolved_start, resolved_end = VisitorLogsRepository._resolve_range(
        start_at=start,
        end_at=end,
    )
    assert resolved_start.tzinfo == timezone.utc
    assert resolved_end.tzinfo == timezone.utc


def test_current_month_bounds_december():
    """December rolls end bound into January of the next year."""
    from unittest.mock import patch

    dec_now = datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc)
    with patch("apps.user_service.app.db.repositories.visitor_logs_repository.datetime") as mock_dt:
        mock_dt.now.return_value = dec_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        start, end = VisitorLogsRepository._current_month_bounds()
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_list_logs_entry_method_and_access_status_filters():
    """Entry method and access status filters are applied."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        entry_method="qr_scan",
        access_status="granted",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "ci.entry_method = $4::pass_entry_method" in count_query
    assert "ci.access_status = $5::pass_access_status" in count_query
    assert "walk_in_entries" not in count_query


@pytest.mark.asyncio
async def test_list_logs_tower_filter():
    """Tower filter scopes to unit tower_id."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        tower_id="tower-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "u.tower_id = $4::uuid" in count_query
    assert "walk_in_visit_units vu" in count_query


@pytest.mark.asyncio
async def test_get_overview_empty_row():
    """Overview returns zero metrics when fetchrow is empty."""
    conn = _FakeConn(row=None)
    repo = VisitorLogsRepository(db_connection=conn)
    result = await repo.get_overview(
        organization_id="org-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert result["total_entries"] == 0
    assert result["inside_now"] == 0
    assert result["exited"] == 0


@pytest.mark.asyncio
async def test_list_logs_bucket_filter():
    """Bucket filter applies visit_status predicates on the combined subquery."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        bucket=VisitorLogBucket.INSIDE_NOW.value,
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "combined.visit_status = $" in count_query
    assert VisitorLogVisitStatus.INSIDE.value in count_args


@pytest.mark.asyncio
async def test_list_logs_visitor_type_and_guard_filters():
    """Visitor type and guard filters apply on the combined subquery."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        visitor_type=VisitorType.GUEST.value,
        guard_user_id="guard-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "combined.source = 'pass'" in count_query
    assert "combined.pass_type = $" in count_query
    assert "combined.guard_user_id = $" in count_query
    assert PassType.GUEST.value in count_args
    assert "guard-1" in count_args


@pytest.mark.asyncio
async def test_list_logs_pass_resident_uses_creator_name():
    """Pass list always maps resident to the pass creator, with optional role."""
    conn = _FakeConn(rows=[], val=0)
    repo = VisitorLogsRepository(db_connection=conn)
    await repo.list_logs(
        organization_id="org-1",
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.created_by_contact_id::text AS resident_contact_id" in count_query
    assert "WHEN 'Owner' THEN 1" not in count_query
