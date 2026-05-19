"""Pydantic models for CRM dashboard API responses."""

from __future__ import annotations

from datetime import date as date_type
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import ProjectStatus
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.status_codes import CustomStatusCode

_DASHBOARD_SCHEMA_VERSION = 1


class DashboardQueryParams(BaseModel):
    """Query parameters for GET /dashboard."""

    start_date: date_type | None = Field(None, description="Inclusive overall range start.")
    end_date: date_type | None = Field(None, description="Inclusive overall range end.")
    leads_start_date: date_type | None = Field(None, description="Inclusive leads range start.")
    leads_end_date: date_type | None = Field(None, description="Inclusive leads range end.")

    @model_validator(mode="after")
    def validate_date_ranges(self) -> Self:
        """Reject inverted overall or leads date ranges."""
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise BadRequestException(
                message_key="dashboard.errors.end_before_start",
                custom_code=CustomStatusCode.BAD_REQUEST,
                errors=[
                    {
                        "field": "query.end_date",
                        "type": "bad_request",
                        "msg": "end_date must be on or after start_date",
                    }
                ],
            )
        if (
            self.leads_start_date is not None
            and self.leads_end_date is not None
            and self.leads_end_date < self.leads_start_date
        ):
            raise BadRequestException(
                message_key="dashboard.errors.leads_end_before_start",
                custom_code=CustomStatusCode.BAD_REQUEST,
                errors=[
                    {
                        "field": "query.leads_end_date",
                        "type": "bad_request",
                        "msg": "leads_end_date must be on or after leads_start_date",
                    }
                ],
            )
        return self


class MetricWithWeeklyDelta(BaseModel):
    """Total plus new records in current vs previous calendar week."""

    total: int = Field(..., ge=0)
    new_this_week: int = Field(..., ge=0)
    new_previous_week: int = Field(..., ge=0)


class LeadsOverviewMetrics(BaseModel):
    """Open lead count (pipeline) plus weekly new lead creations."""

    open_total: int = Field(..., ge=0)
    new_this_week: int = Field(..., ge=0)
    new_previous_week: int = Field(..., ge=0)


class ProjectsOverviewMetrics(BaseModel):
    """Active projects, weekly new projects, and upcoming starts."""

    active_total: int = Field(..., ge=0)
    new_this_week: int = Field(..., ge=0)
    new_previous_week: int = Field(..., ge=0)
    launching_soon: int = Field(
        ...,
        ge=0,
        description="Active pipeline projects with start_date in the next 14 local days.",
    )


class CrmOverviewSection(BaseModel):
    """CRM Overview card data."""

    contacts: MetricWithWeeklyDelta
    companies: MetricWithWeeklyDelta
    leads: LeadsOverviewMetrics
    projects: ProjectsOverviewMetrics
    leads_without_stage: int = Field(
        ...,
        ge=0,
        description="Leads with no stage_id (excluded from pipeline donut slices).",
    )


class WeeklyActivityDay(BaseModel):
    """One point on the leads activity chart."""

    day: date_type
    leads_count: int = Field(..., ge=0)


class WeeklyActivitySection(BaseModel):
    """Seven-day leads series (local calendar days in requested TZ)."""

    days: list[WeeklyActivityDay]


class LeadPipelineStage(BaseModel):
    """One slice of the pipeline donut."""

    stage_key: str
    stage_name: str
    sort_order: int
    count: int = Field(..., ge=0)


class LeadPipelineSection(BaseModel):
    """Pipeline distribution plus unstaged leads."""

    stages: list[LeadPipelineStage]
    leads_without_stage_count: int = Field(..., ge=0)


ProjectHealth = Literal["on_track", "at_risk"]

DueSummaryKind = Literal["none", "overdue", "today", "tomorrow", "in_days"]


class MyProjectDueSummary(BaseModel):
    """Display-oriented deadline summary for localized UI copy."""

    kind: DueSummaryKind = Field(
        ...,
        description="none: no target_end_date; overdue/today/tomorrow/in_days.",
    )
    days: int | None = Field(
        None,
        ge=0,
        description="For overdue: days late (positive).",
    )


class MyProjectItem(BaseModel):
    """Project row for the current user (team membership)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_title: str
    status: ProjectStatus
    priority: str
    target_end_date: date_type | None = None
    start_date: date_type | None = None
    client_name: str | None = Field(
        default=None, description="Reserved; no project–client FK in v1."
    )
    progress_percent: float | None = Field(
        default=None, description="Reserved until stored or derived."
    )
    health: ProjectHealth
    due_summary: MyProjectDueSummary = Field(
        ...,
        description="Deadline for UX; map kind/days to i18n strings on the client.",
    )


class DashboardResponse(BaseModel):
    """Full dashboard payload (v1 — CRM only; operations empty)."""

    schema_version: int = Field(default=_DASHBOARD_SCHEMA_VERSION, ge=1)
    timezone: str = Field(
        ...,
        description="Resolved IANA zone from organization_members.",
    )
    crm_overview: CrmOverviewSection
    weekly_activity: WeeklyActivitySection
    lead_pipeline: LeadPipelineSection
    my_projects: list[MyProjectItem]
    operations: dict = Field(default_factory=dict)


def validate_iana_timezone(name: str) -> str:
    """Ensure ``name`` is a valid IANA zone for PostgreSQL ``AT TIME ZONE`` / ``ZoneInfo``."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError("Invalid IANA timezone") from exc
    return name
