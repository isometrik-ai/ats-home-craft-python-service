"""Unit tests for VisitorLogsService."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.services.visitor_logs_service import VisitorLogsService
from apps.user_service.app.utils.common_utils import UserContext


def _user_context() -> UserContext:
    """Build an admin user context for visitor log tests."""
    return UserContext(
        user_id="admin-1",
        email="admin@example.com",
        organization_id="org-1",
    )


class _FakeLogsRepo:
    """In-memory fake for VisitorLogsRepository."""

    def __init__(self):
        self.list_result: tuple[list[dict[str, Any]], int] = ([], 0)
        self.overview_result = {
            "start_at": "2026-06-01T00:00:00Z",
            "end_at": "2026-06-30T00:00:00Z",
            "total_visitors": 0,
            "in_count": 0,
            "deliveries": 0,
            "daily_help": 0,
        }

    async def list_logs(self, **_kwargs):
        """Return configured list result."""
        return self.list_result

    async def get_overview(self, **_kwargs):
        """Return configured overview result."""
        return self.overview_result


class _FakePassesRepo:
    """In-memory fake for org-scoped pass fetch."""

    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row

    async def get_by_id(self, **_kwargs):
        """Return configured pass row."""
        return self.row


class _FakeEventsRepo:
    """In-memory fake for pass events."""

    def __init__(self, events: list[dict[str, Any]] | None = None):
        self.events = events or []

    async def list_by_pass(self, **_kwargs):
        """Return configured events."""
        return self.events


def _service(
    *,
    logs_repo: _FakeLogsRepo | None = None,
    passes_repo: _FakePassesRepo | None = None,
    events_repo: _FakeEventsRepo | None = None,
) -> VisitorLogsService:
    """Build VisitorLogsService with fake repositories."""
    svc = VisitorLogsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.logs_repo = logs_repo or _FakeLogsRepo()
    svc.passes_repo = passes_repo or _FakePassesRepo()
    svc.events_repo = events_repo or _FakeEventsRepo()
    return svc


@pytest.mark.asyncio
async def test_list_logs_shapes_time_spent():
    """List logs derives time_spent_minutes from in/out timestamps."""
    in_time = datetime(2026, 6, 9, 9, 12, tzinfo=timezone.utc)
    out_time = datetime(2026, 6, 9, 9, 18, tzinfo=timezone.utc)
    logs_repo = _FakeLogsRepo()
    logs_repo.list_result = (
        [
            {
                "pass_id": "pass-1",
                "pass_type": "delivery",
                "unit_label": "B-1204",
                "tower_name": "Tower B",
                "created_by": "T. Nair",
                "scheduled_from": in_time,
                "scheduled_until": out_time,
                "entry_method": "qr",
                "guard_name": "Ramesh Kumar",
                "access_status": "approved",
                "in_time": in_time,
                "out_time": out_time,
            }
        ],
        1,
    )
    svc = _service(logs_repo=logs_repo)
    start_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 30, tzinfo=timezone.utc)
    items, total = await svc.list_logs(start_at=start_at, end_at=end_at)
    assert total == 1
    assert items[0]["time_spent_minutes"] == 6
    assert items[0]["created_by"] == "T. Nair"


@pytest.mark.asyncio
async def test_get_overview():
    """Overview returns repository aggregates unchanged."""
    logs_repo = _FakeLogsRepo()
    logs_repo.overview_result = {
        "start_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "end_at": datetime(2026, 6, 30, tzinfo=timezone.utc),
        "total_visitors": 28,
        "in_count": 7,
        "deliveries": 5,
        "daily_help": 11,
    }
    svc = _service(logs_repo=logs_repo)
    result = await svc.get_overview(
        start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert result["total_visitors"] == 28
    assert result["daily_help"] == 11
    assert result["start_at"].startswith("2026-06-01T00:00:00")
    assert result["end_at"].startswith("2026-06-30T00:00:00")


@pytest.mark.asyncio
async def test_get_log_detail_returns_pass_timeline():
    """Detail view should merge pass row with normalized events."""
    pass_row = {"id": "pass-1", "pass_type": "guest", "status": "approved"}
    events = [{"id": "evt-1", "event_type": "in"}]
    passes_repo = _FakePassesRepo(row=pass_row)
    events_repo = _FakeEventsRepo(events=events)
    svc = _service(passes_repo=passes_repo, events_repo=events_repo)
    svc._passes_service = type(
        "Passes",
        (),
        {
            "_normalize_event": staticmethod(lambda row: {**row, "normalized": True}),
            "_normalize_pass": staticmethod(
                lambda row, events=None, include_events=False: {
                    **row,
                    "events": events or [],
                    "include_events": include_events,
                }
            ),
        },
    )()
    detail = await svc.get_log_detail(pass_id="pass-1")
    assert detail["id"] == "pass-1"
    assert detail["include_events"] is True
    assert detail["events"][0]["normalized"] is True
