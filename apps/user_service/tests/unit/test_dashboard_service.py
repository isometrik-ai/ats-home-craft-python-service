"""Unit tests for DashboardService."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.dashboard_service import (
    DashboardService,
    _json_list,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"


def test_json_list_none_returns_empty() -> None:
    """_json_list returns [] for None."""
    assert _json_list(None) == []


def test_json_list_passthrough_list() -> None:
    """_json_list returns lists unchanged."""
    payload = [{"day": "Mon", "leads_count": 1}]
    assert _json_list(payload) == payload


def test_json_list_parses_json_string() -> None:
    """_json_list parses JSON string payloads."""
    payload = json.dumps([{"stage_key": "new", "stage_name": "New", "sort_order": 1, "count": 2}])
    result = _json_list(payload)
    assert len(result) == 1
    assert result[0]["stage_key"] == "new"


def test_json_list_non_list_json_returns_empty() -> None:
    """_json_list returns [] when parsed JSON is not a list."""
    assert _json_list('{"not": "a list"}') == []


def test_json_list_unknown_type_returns_empty() -> None:
    """_json_list returns [] for unsupported types."""
    assert _json_list(42) == []


@pytest.mark.asyncio
async def test_get_dashboard_assembles_sections() -> None:
    """get_dashboard maps repository row into DashboardResponse."""
    repo_row = {
        "user_timezone": "UTC",
        "weekly_activity": [{"day": "2026-07-21", "leads_count": 3}],
        "lead_pipeline": [
            {"stage_key": "qualified", "stage_name": "Qualified", "sort_order": 1, "count": 5}
        ],
        "my_projects": [
            {
                "id": "m1",
                "project_id": "p1",
                "project_title": "Tower A",
                "status": "active",
                "priority": "medium",
                "target_end_date": "2026-08-01",
                "start_date": "2026-01-01",
            }
        ],
        "total_contacts": 10,
        "contacts_new_this_week": 2,
        "contacts_new_prev_week": 1,
        "total_companies": 4,
        "companies_new_this_week": 1,
        "companies_new_prev_week": 0,
        "open_leads": 7,
        "leads_new_this_week": 3,
        "leads_new_prev_week": 2,
        "active_projects": 2,
        "projects_new_this_week": 1,
        "projects_new_prev_week": 0,
        "launching_soon": 1,
        "leads_without_stage": 2,
    }

    mock_repo = MagicMock()
    mock_repo.fetch_dashboard = AsyncMock(return_value=repo_row)

    with (
        patch(
            "apps.user_service.app.services.dashboard_service.DashboardRepository",
            return_value=mock_repo,
        ),
        patch(
            "apps.user_service.app.services.dashboard_service.ZoneInfo",
            side_effect=lambda _name: timezone.utc,
        ),
        patch(
            "apps.user_service.app.services.dashboard_service.datetime",
        ) as mock_datetime,
    ):
        mock_datetime.now.return_value = datetime(2026, 7, 23, tzinfo=timezone.utc)
        service = DashboardService(
            db_connection=MagicMock(),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        result = await service.get_dashboard(
            leads_start_date=date(2026, 1, 1),
            leads_end_date=date(2026, 1, 31),
        )

    mock_repo.fetch_dashboard.assert_awaited_once_with(
        ORG_ID,
        USER_ID,
        leads_start_date=date(2026, 1, 1),
        leads_end_date=date(2026, 1, 31),
    )
    assert result.timezone == "UTC"
    assert result.crm_overview.contacts.total == 10
    assert result.crm_overview.leads.open_total == 7
    assert result.lead_pipeline.stages[0].count == 5
    assert len(result.weekly_activity.days) == 1
    assert len(result.my_projects) == 1
    assert result.my_projects[0].project_title == "Tower A"


@pytest.mark.asyncio
async def test_get_dashboard_defaults_missing_counts() -> None:
    """get_dashboard treats missing numeric fields as zero."""
    mock_repo = MagicMock()
    mock_repo.fetch_dashboard = AsyncMock(
        return_value={
            "user_timezone": None,
            "weekly_activity": None,
            "lead_pipeline": None,
            "my_projects": None,
        }
    )

    with (
        patch(
            "apps.user_service.app.services.dashboard_service.DashboardRepository",
            return_value=mock_repo,
        ),
        patch(
            "apps.user_service.app.services.dashboard_service.ZoneInfo",
            side_effect=lambda _name: timezone.utc,
        ),
        patch(
            "apps.user_service.app.services.dashboard_service.datetime",
        ) as mock_datetime,
    ):
        mock_datetime.now.return_value = datetime(2026, 7, 23, tzinfo=timezone.utc)
        service = DashboardService(
            db_connection=MagicMock(),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        result = await service.get_dashboard()

    assert result.timezone == "UTC"
    assert result.crm_overview.contacts.total == 0
    assert result.lead_pipeline.stages == []
    assert result.my_projects == []
