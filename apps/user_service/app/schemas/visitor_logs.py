"""Visitor logs admin schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    PassAccessStatus,
    PassEntryMethod,
    PassType,
)
from apps.user_service.app.schemas.passes import PassEventResponse, PassResponse


def _ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class VisitorLogDateRangeQuery(BaseModel):
    """Optional UTC date range for visitor log queries."""

    model_config = ConfigDict(extra="forbid")

    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "VisitorLogDateRangeQuery":
        """Require start_at and end_at together; end must be after start."""
        if self.start_at is None and self.end_at is None:
            return self
        if self.start_at is None or self.end_at is None:
            raise ValueError("start_at and end_at must be provided together")
        start = _ensure_utc(self.start_at)
        end = _ensure_utc(self.end_at)
        if end <= start:
            raise ValueError("end_at must be after start_at")
        self.start_at = start
        self.end_at = end
        return self


class VisitorLogQuery(VisitorLogDateRangeQuery):
    """Query params for GET /visitor-logs."""

    search: str | None = Field(None, max_length=200)
    pass_type: PassType | None = None
    entry_method: PassEntryMethod | None = None
    access_status: PassAccessStatus | None = None
    tower_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class VisitorLogOverviewQuery(VisitorLogDateRangeQuery):
    """Query params for GET /visitor-logs/overview."""


class VisitorLogItemResponse(BaseModel):
    """Single row in the Visitor Logs table."""

    model_config = ConfigDict(extra="ignore")

    pass_id: str
    pass_type: str
    unit_label: str | None = None
    tower_name: str | None = None
    created_by: str | None = None
    scheduled_from: str | None = None
    scheduled_until: str | None = None
    entry_method: str | None = None
    guard_name: str | None = None
    access_status: str | None = None
    in_time: str | None = None
    out_time: str | None = None
    time_spent_minutes: int | None = None


class VisitorLogOverviewResponse(BaseModel):
    """Overview cards for the Visitor Logs dashboard."""

    model_config = ConfigDict(extra="ignore")

    start_at: str
    end_at: str
    total_visitors: int
    in_count: int
    deliveries: int
    daily_help: int


class VisitorLogDetailResponse(PassResponse):
    """Pass detail with full timeline for admin visitor logs."""

    model_config = ConfigDict(extra="ignore")

    events: list[PassEventResponse] = Field(default_factory=list)
