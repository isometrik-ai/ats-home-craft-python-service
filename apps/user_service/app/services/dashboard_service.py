"""Assemble CRM dashboard payload from ``DashboardRepository``."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from apps.user_service.app.db.repositories.dashboard_repository import (
    DashboardRepository,
)
from apps.user_service.app.schemas.dashboard import (
    CrmOverviewSection,
    DashboardResponse,
    LeadPipelineSection,
    LeadPipelineStage,
    LeadsOverviewMetrics,
    MetricWithWeeklyDelta,
    ProjectsOverviewMetrics,
    WeeklyActivityDay,
    WeeklyActivitySection,
)
from apps.user_service.app.utils.dashboard_utils import serialize_my_project_row


def _json_list(val: Any) -> list[Any]:
    """Normalize asyncpg/jsonb payload to a list (handles str in edge codecs)."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    return []


class DashboardService:
    """Load dashboard sections for one organization."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        organization_id: str,
        user_id: str,
    ) -> None:
        self._db = db_connection
        self._organization_id = organization_id
        self._user_id = user_id
        self._repo = DashboardRepository(db_connection)

    async def get_dashboard(
        self,
        leads_start_date: date | None = None,
        leads_end_date: date | None = None,
    ) -> DashboardResponse:
        """Get dashboard data for the organization and user."""
        row = await self._repo.fetch_dashboard(
            self._organization_id,
            self._user_id,
            leads_start_date=leads_start_date,
            leads_end_date=leads_end_date,
        )

        timezone_name = str(row.get("user_timezone") or "UTC")
        timezone = ZoneInfo(timezone_name)
        today = datetime.now(timezone).date()

        weekly = _json_list(row.get("weekly_activity"))
        pipeline = _json_list(row.get("lead_pipeline"))
        raw_projects = _json_list(row.get("my_projects"))

        crm = CrmOverviewSection(
            contacts=MetricWithWeeklyDelta(
                total=int(row.get("total_contacts") or 0),
                new_this_week=int(row.get("contacts_new_this_week") or 0),
                new_previous_week=int(row.get("contacts_new_prev_week") or 0),
            ),
            companies=MetricWithWeeklyDelta(
                total=int(row.get("total_companies") or 0),
                new_this_week=int(row.get("companies_new_this_week") or 0),
                new_previous_week=int(row.get("companies_new_prev_week") or 0),
            ),
            leads=LeadsOverviewMetrics(
                open_total=int(row.get("open_leads") or 0),
                new_this_week=int(row.get("leads_new_this_week") or 0),
                new_previous_week=int(row.get("leads_new_prev_week") or 0),
            ),
            projects=ProjectsOverviewMetrics(
                active_total=int(row.get("active_projects") or 0),
                new_this_week=int(row.get("projects_new_this_week") or 0),
                new_previous_week=int(row.get("projects_new_prev_week") or 0),
                launching_soon=int(row.get("launching_soon") or 0),
            ),
            leads_without_stage=int(row.get("leads_without_stage") or 0),
        )

        lead_pipeline = LeadPipelineSection(
            stages=[
                LeadPipelineStage(
                    stage_key=str(p["stage_key"]),
                    stage_name=str(p["stage_name"]),
                    sort_order=int(p["sort_order"]),
                    count=int(p["count"] or 0),
                )
                for p in pipeline
            ],
            leads_without_stage_count=int(row.get("leads_without_stage") or 0),
        )

        my_projects = [serialize_my_project_row(dict(pr), today) for pr in raw_projects]

        return DashboardResponse(
            timezone=timezone_name,
            crm_overview=crm,
            weekly_activity=WeeklyActivitySection(
                days=[
                    WeeklyActivityDay(day=w["day"], leads_count=int(w["leads_count"] or 0))
                    for w in weekly
                ],
            ),
            lead_pipeline=lead_pipeline,
            my_projects=my_projects,
            operations={},
        )
