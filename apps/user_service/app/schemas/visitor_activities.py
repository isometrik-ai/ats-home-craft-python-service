"""Resident visitor activities schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import PassType, VisitorLogBucket
from apps.user_service.app.schemas.passes import PassEventResponse
from apps.user_service.app.schemas.visitor_logs import (
    VisitorLogDateRangeQuery,
    VisitorLogResidentResponse,
)
from apps.user_service.app.schemas.walk_in import (
    WalkInEventResponse,
    WalkInMilestoneResponse,
    WalkInVisitUnitResponse,
)


def _ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ResidentVisitorActivityListQuery(VisitorLogDateRangeQuery):
    """Query params for GET /units/{unit_id}/visitor-activities."""

    bucket: VisitorLogBucket = Field(
        default=VisitorLogBucket.ALL,
        description="Tab filter: all, awaiting_approval, inside_now, completed, denied_expired.",
    )
    type: PassType | None = Field(
        None,
        description=(
            "Filter by visit type: guest, delivery, cab, service, daily_help, or other. "
            "Excludes the walk_in filter-only value."
        ),
    )
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @model_validator(mode="after")
    def reject_walk_in_type_filter(self) -> "ResidentVisitorActivityListQuery":
        """Resident type filter uses stored visit types, not the walk_in log alias."""
        if self.type == PassType.WALK_IN:
            raise ValueError("type=walk_in is not valid for resident activities.")
        return self


class ResidentVisitorActivityListItemResponse(BaseModel):
    """Single visitor activity row for a resident unit feed."""

    model_config = ConfigDict(extra="ignore")

    source: Literal["pass", "walk_in"]
    id: str
    type: str
    sub_type: str | None = None
    visitor_name: str | None = None
    visitor_phone_isd_code: str | None = None
    visitor_phone_number: str | None = None
    visit_status: str
    visitor_type: str
    pass_code: str | None = None
    daily_help_category_name: str | None = None
    daily_help_profile_id: str | None = Field(
        None,
        description=(
            "Daily help profile UUID for type=daily_help. "
            "Use to navigate to GET /v1/daily-help/{profile_id}."
        ),
    )
    validity_type: str | None = None
    scheduled_from: str | None = None
    scheduled_until: str | None = None
    entry_method: str | None = None
    access_status: str | None = None
    in_time: str | None = None
    out_time: str | None = None
    time_spent_minutes: int | None = None
    is_private: bool = False
    pass_image_url: str | None = None
    visitor_photo_urls: list[str] = Field(default_factory=list)
    vehicle_photo_urls: list[str] = Field(default_factory=list)
    allowed_by: VisitorLogResidentResponse | None = Field(
        None,
        description=(
            "Flat resident who pre-approved the pass or allowed the walk-in. "
            "Omitted for daily help."
        ),
    )
    entries_on_day: int | None = Field(
        None,
        description=(
            "Number of gate check-ins on the visit day for recurring daily help. "
            "Use to render labels such as 'Entered 2 times'."
        ),
    )


class ResidentVisitorActivityPassDetailResponse(BaseModel):
    """Pass-based visitor activity detail for residents."""

    model_config = ConfigDict(extra="ignore")

    source: Literal["pass"] = "pass"
    id: str
    unit_id: str
    type: str
    sub_type: str | None = None
    visitor_name: str
    visitor_phone_isd_code: str | None = None
    visitor_phone_number: str | None = None
    visitor_count: int = 1
    vehicle_number: str | None = None
    purpose: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    validity_type: str
    allow_multiple_entries: bool = False
    is_private: bool = False
    max_entries: int | None = None
    entry_count: int = 0
    status: str
    display_status: str
    pass_code: str
    visit_status: str
    visitor_type: str
    daily_help_category_name: str | None = None
    daily_help_profile_id: str | None = Field(
        None,
        description="Daily help profile UUID when type=daily_help.",
    )
    in_time: str | None = None
    out_time: str | None = None
    time_spent_minutes: int | None = None
    entry_method: str | None = None
    access_status: str | None = None
    pass_image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    notes: str | None = None
    unit_label: str | None = None
    tower_name: str | None = None
    events: list[PassEventResponse] = Field(default_factory=list)


class ResidentVisitorActivityWalkInDetailResponse(BaseModel):
    """Walk-in visitor activity detail scoped to the resident's flat."""

    model_config = ConfigDict(extra="ignore")

    source: Literal["walk_in"] = "walk_in"
    id: str
    unit_id: str
    type: str
    sub_type: str | None = None
    visitor_first_name: str
    visitor_last_name: str | None = None
    visitor_phone_isd_code: str
    visitor_phone_number: str
    status: str
    visit_status: str
    visitor_type: str
    flats_count: int
    notes: str | None = None
    requested_at: str
    entered_at: str | None = None
    exited_at: str | None = None
    in_time: str | None = None
    out_time: str | None = None
    time_spent_minutes: int | None = None
    entry_method: str | None = None
    visitor_photo_urls: list[str] = Field(default_factory=list)
    vehicle_photo_urls: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    visit_unit: WalkInVisitUnitResponse
    events: list[WalkInEventResponse] = Field(default_factory=list)
    milestones: list[WalkInMilestoneResponse] = Field(default_factory=list)


def resident_activity_detail_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a resident activity detail payload by source."""
    source = str(data.get("source") or "pass")
    if source == "walk_in":
        return ResidentVisitorActivityWalkInDetailResponse.model_validate(data).model_dump()
    return ResidentVisitorActivityPassDetailResponse.model_validate(data).model_dump()
