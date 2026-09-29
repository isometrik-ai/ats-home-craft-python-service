"""Unit tests for DashboardRepository (SQL shape + parameter binding)."""

from collections import deque
from datetime import date
from types import SimpleNamespace

import pytest

from apps.user_service.app.db.repositories.dashboard_repository import (
    CLOSED_LEAD_STAGE_KEYS,
    DashboardRepository,
)
from apps.user_service.app.schemas.enums import ProjectStatus
from apps.user_service.app.utils.dashboard_utils import serialize_my_project_row


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Initialize fake call stores."""
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetch_result: list = []
        self.fetchrow_queue: deque = deque()

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        if self.fetchrow_queue:
            return self.fetchrow_queue.popleft()
        return None


@pytest.mark.asyncio
async def test_fetch_dashboard_timezone_then_aggregate(monkeypatch):
    """Test fetch_dashboard with timezone validation and aggregate query."""
    from datetime import date as date_cls

    class _FakeTZ:
        """Stand-in timezone for ZoneInfo when tzdata is unavailable."""

    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.validate_iana_timezone",
        lambda tz: tz,
    )
    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.ZoneInfo",
        lambda _name: _FakeTZ(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.datetime",
        SimpleNamespace(now=lambda tz=None: SimpleNamespace(date=lambda: date_cls(2026, 1, 15))),
    )
    conn = _FakeConn()
    conn.fetchrow_queue.append({"tz": "America/Chicago"})
    conn.fetchrow_queue.append(
        {
            "total_contacts": 1,
            "total_companies": 2,
            "open_leads": 3,
            "leads_without_stage": 0,
            "active_projects": 4,
            "launching_soon": 0,
            "contacts_new_this_week": 0,
            "contacts_new_prev_week": 0,
            "companies_new_this_week": 0,
            "companies_new_prev_week": 0,
            "leads_new_this_week": 0,
            "leads_new_prev_week": 0,
            "projects_new_this_week": 0,
            "projects_new_prev_week": 0,
            "weekly_activity": [],
            "lead_pipeline": [],
            "my_projects": [],
        }
    )
    repo = DashboardRepository(conn)
    result = await repo.fetch_dashboard("org-uuid", "user-uuid", my_projects_limit=25)

    assert len(conn.fetchrow_calls) == 2
    assert len(conn.fetch_calls) == 0
    assert result.get("user_timezone") == "America/Chicago"

    tz_query, tz_args = conn.fetchrow_calls[0]
    assert "organization_members" in tz_query
    assert tz_args == ("org-uuid", "user-uuid")

    agg_query, agg_args = conn.fetchrow_calls[1]
    assert agg_query.startswith("WITH\n")
    assert "bounds AS (" in agg_query
    assert "weekly_series AS" in agg_query
    assert "pipeline_rows AS" in agg_query
    assert "my_project_rows AS" in agg_query
    assert "NOT EXISTS" in agg_query and "lead_stages" in agg_query
    assert "json_agg" in agg_query and "weekly_activity" in agg_query
    assert "p.status <> $5::text" in agg_query
    assert "LIMIT $7::int" in agg_query
    assert agg_args[0] == "org-uuid"
    assert agg_args[1] == "America/Chicago"
    assert agg_args[2] == sorted(CLOSED_LEAD_STAGE_KEYS)
    assert agg_args[3] == [
        ProjectStatus.DISCOVERY.value,
        ProjectStatus.ACTIVE.value,
        ProjectStatus.ON_HOLD.value,
    ]
    assert agg_args[4] == ProjectStatus.ARCHIVED.value
    assert agg_args[5] == "user-uuid"
    assert agg_args[6] == 25
    assert len(agg_args) == 13


def test_dashboard_utils_serialize_my_project_health():
    """Test serialize_my_project_row with health calculation."""
    row = {
        "id": "p1",
        "project_id": "PRJ-1",
        "project_title": "T",
        "status": "active",
        "priority": "urgent",
        "target_end_date": date(2030, 1, 1),
        "start_date": None,
        "created_at": None,
    }
    item = serialize_my_project_row(row, date(2026, 1, 1))
    assert item.health == "at_risk"
    assert item.due_summary.kind == "in_days"
    assert item.due_summary.days == (date(2030, 1, 1) - date(2026, 1, 1)).days


def test_due_summary_overdue_uses_positive_days():
    """Test serialize_my_project_row with overdue due summary."""
    row = {
        "id": "p1",
        "project_id": "PRJ-1",
        "project_title": "Late",
        "status": "discovery",
        "priority": "medium",
        "target_end_date": date(2026, 4, 29),
        "start_date": None,
        "created_at": None,
    }
    item = serialize_my_project_row(row, date(2026, 5, 12))
    assert item.due_summary.kind == "overdue"
    assert item.due_summary.days == 13


def test_due_summary_today_tomorrow_and_none():
    """Test serialize_my_project_row with today, tomorrow, and none due summaries."""
    today = date(2026, 5, 12)
    row_base = {
        "id": "p1",
        "project_id": "PRJ-1",
        "project_title": "T",
        "status": "active",
        "priority": "medium",
        "start_date": None,
        "created_at": None,
    }
    today_row = {**row_base, "target_end_date": today}
    assert serialize_my_project_row(today_row, today).due_summary.kind == "today"
    assert serialize_my_project_row(today_row, today).due_summary.days is None

    tomorrow_row = {**row_base, "target_end_date": date(2026, 5, 13)}
    serialized = serialize_my_project_row(tomorrow_row, today)
    assert serialized.due_summary.kind == "tomorrow"
    assert serialized.due_summary.days is None

    none_row = {**row_base, "target_end_date": None}
    assert serialize_my_project_row(none_row, today).due_summary.kind == "none"


def test_serialize_my_project_parses_iso_date_strings():
    """Test serialize_my_project_row with ISO date string parsing."""
    row = {
        "id": "p1",
        "project_id": "PRJ-1",
        "project_title": "T",
        "status": "active",
        "priority": "medium",
        "target_end_date": "2030-01-15",
        "start_date": None,
        "created_at": None,
    }
    item = serialize_my_project_row(row, date(2026, 1, 1))
    assert item.target_end_date == date(2030, 1, 15)


def test_inclusive_range_defaults_to_none_when_unbounded():
    """Unbounded lead ranges should pass through as SQL defaults."""
    from apps.user_service.app.db.repositories.dashboard_repository import (
        _inclusive_range,
    )

    assert _inclusive_range(None, None, date(2026, 1, 15)) == (None, None, None, None)


def test_inclusive_range_builds_previous_window():
    """Inclusive lead ranges should include a previous period of equal span."""
    from apps.user_service.app.db.repositories.dashboard_repository import (
        _inclusive_range,
    )

    start, end, prev_start, prev_end = _inclusive_range(
        date(2026, 1, 10),
        date(2026, 1, 12),
        date(2026, 1, 15),
    )
    assert start == date(2026, 1, 10)
    assert end == date(2026, 1, 12)
    assert prev_end == date(2026, 1, 9)
    assert prev_start == date(2026, 1, 7)


@pytest.mark.asyncio
async def test_resolve_dashboard_timezone_falls_back_to_utc(monkeypatch):
    """Invalid member timezones should fall back to UTC."""
    from apps.user_service.app.db.repositories.dashboard_repository import (
        DashboardRepository,
    )

    def _invalid_timezone(_tz: str) -> str:
        raise ValueError("bad tz")

    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.validate_iana_timezone",
        _invalid_timezone,
    )
    conn = _FakeConn()
    conn.fetchrow_queue.append({"tz": "Not/AZone"})
    repo = DashboardRepository(conn)

    tz = await repo._resolve_dashboard_timezone("org-uuid", "user-uuid")

    assert tz == "UTC"


@pytest.mark.asyncio
async def test_fetch_dashboard_with_lead_date_filters(monkeypatch):
    """Explicit lead date filters should bound chart windows."""
    from datetime import date as date_cls

    class _FakeTZ:
        """Stand-in timezone for ZoneInfo when tzdata is unavailable."""

    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.validate_iana_timezone",
        lambda tz: tz,
    )
    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.ZoneInfo",
        lambda _name: _FakeTZ(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.dashboard_repository.datetime",
        SimpleNamespace(now=lambda tz=None: SimpleNamespace(date=lambda: date_cls(2026, 1, 15))),
    )
    conn = _FakeConn()
    conn.fetchrow_queue.append({"tz": "UTC"})
    conn.fetchrow_queue.append({"total_contacts": 0})
    repo = DashboardRepository(conn)

    await repo.fetch_dashboard(
        "org-uuid",
        "user-uuid",
        leads_start_date=date_cls(2026, 1, 1),
        leads_end_date=date_cls(2026, 1, 7),
    )

    agg_args = conn.fetchrow_calls[1][1]
    assert agg_args[7:11] == (
        date_cls(2026, 1, 1),
        date_cls(2026, 1, 7),
        date_cls(2025, 12, 25),
        date_cls(2025, 12, 31),
    )
    assert agg_args[11] == date_cls(2026, 1, 1)
    assert agg_args[12] == date_cls(2026, 1, 7)
