"""Integration tests for CRM dashboard endpoints."""

from datetime import date

import pytest

from apps.user_service.app.schemas.dashboard import (
    CrmOverviewSection,
    DashboardResponse,
    LeadPipelineSection,
    LeadsOverviewMetrics,
    MetricWithWeeklyDelta,
    ProjectsOverviewMetrics,
    WeeklyActivityDay,
    WeeklyActivitySection,
)
from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_success


def _fake_dashboard(**overrides) -> DashboardResponse:
    """Build a dashboard response for service fakes."""
    base = DashboardResponse(
        timezone="UTC",
        crm_overview=CrmOverviewSection(
            contacts=MetricWithWeeklyDelta(total=10, new_this_week=2, new_previous_week=1),
            companies=MetricWithWeeklyDelta(total=5, new_this_week=1, new_previous_week=0),
            leads=LeadsOverviewMetrics(open_total=8, new_this_week=3, new_previous_week=2),
            projects=ProjectsOverviewMetrics(
                active_total=4,
                new_this_week=1,
                new_previous_week=1,
                launching_soon=2,
            ),
            leads_without_stage=1,
        ),
        weekly_activity=WeeklyActivitySection(
            days=[WeeklyActivityDay(day=date(2026, 7, 1), leads_count=2)]
        ),
        lead_pipeline=LeadPipelineSection(stages=[], leads_without_stage_count=1),
        my_projects=[],
    )
    if overrides:
        return base.model_copy(update=overrides)
    return base


def _patch_user_context(monkeypatch) -> None:
    """Patch extract_user_context for dashboard routes."""

    async def fake_extract_user_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123")

    monkeypatch.setattr(
        "apps.user_service.app.api.dashboard.extract_user_context",
        fake_extract_user_context,
    )


@pytest.mark.asyncio
async def test_get_dashboard(monkeypatch, client):
    """GET dashboard returns CRM overview and activity sections."""

    _patch_user_context(monkeypatch)

    async def fake_get_dashboard(
        _self,
        *,
        leads_start_date=None,
        leads_end_date=None,
    ):
        del _self, leads_start_date, leads_end_date
        return _fake_dashboard()

    monkeypatch.setattr(
        "apps.user_service.app.services.dashboard_service.DashboardService.get_dashboard",
        fake_get_dashboard,
    )

    res = await client.get("/v1/dashboard")
    body = assert_success(res, 200)
    assert body["data"]["timezone"] == "UTC"
    assert body["data"]["crm_overview"]["contacts"]["total"] == 10
    assert body["data"]["weekly_activity"]["days"][0]["leads_count"] == 2


@pytest.mark.asyncio
async def test_get_dashboard_with_leads_range(monkeypatch, client):
    """GET dashboard forwards leads date filters to the service."""

    _patch_user_context(monkeypatch)

    async def fake_get_dashboard(
        _self,
        *,
        leads_start_date=None,
        leads_end_date=None,
    ):
        del _self
        assert leads_start_date == date(2026, 7, 1)
        assert leads_end_date == date(2026, 7, 31)
        return _fake_dashboard()

    monkeypatch.setattr(
        "apps.user_service.app.services.dashboard_service.DashboardService.get_dashboard",
        fake_get_dashboard,
    )

    res = await client.get(
        "/v1/dashboard",
        params={
            "leads_start_date": "2026-07-01",
            "leads_end_date": "2026-07-31",
        },
    )
    body = assert_success(res, 200)
    assert body["data"]["crm_overview"]["leads"]["open_total"] == 8


@pytest.mark.asyncio
async def test_get_dashboard_pipeline_metrics(monkeypatch, client):
    """GET dashboard returns pipeline and project overview metrics."""

    _patch_user_context(monkeypatch)

    async def fake_get_dashboard(
        _self,
        *,
        leads_start_date=None,
        leads_end_date=None,
    ):
        del _self, leads_start_date, leads_end_date
        return _fake_dashboard()

    monkeypatch.setattr(
        "apps.user_service.app.services.dashboard_service.DashboardService.get_dashboard",
        fake_get_dashboard,
    )

    res = await client.get("/v1/dashboard")
    body = assert_success(res, 200)
    assert body["data"]["schema_version"] == 1
    assert body["data"]["lead_pipeline"]["leads_without_stage_count"] == 1
    assert body["data"]["crm_overview"]["projects"]["launching_soon"] == 2
