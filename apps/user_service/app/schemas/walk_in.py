"""Walk-in request/response schemas."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import WalkInStatus, WalkInVisitUnitStatus


class WalkInFlatInput(BaseModel):
    """One flat targeted by a walk-in visit."""

    model_config = ConfigDict(extra="forbid")

    tower_id: str
    unit_id: str


class CreateWalkInRequest(BaseModel):
    """Security creates a walk-in visit for one or more flats."""

    model_config = ConfigDict(extra="forbid")

    visitor_first_name: str = Field(..., min_length=1, max_length=100)
    visitor_last_name: str | None = Field(None, max_length=100)
    visitor_phone_isd_code: str = Field(..., min_length=1, max_length=5)
    visitor_phone_number: str = Field(..., min_length=1, max_length=20)
    visitor_photo_paths: list[str] = Field(..., min_length=1, max_length=10)
    vehicle_photo_paths: list[str] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(None, max_length=1000)
    gate_id: str | None = None
    flats: list[WalkInFlatInput] = Field(..., min_length=1, max_length=20)

    @field_validator("visitor_photo_paths")
    @classmethod
    def validate_photo_paths(cls, paths: list[str]) -> list[str]:
        """Ensure each photo path is non-empty."""
        cleaned = [path.strip() for path in paths if path and path.strip()]
        if not cleaned:
            raise ValueError("At least one visitor photo path is required.")
        return cleaned

    @field_validator("vehicle_photo_paths")
    @classmethod
    def validate_vehicle_photo_paths(cls, paths: list[str]) -> list[str]:
        """Drop empty vehicle photo paths."""
        return [path.strip() for path in paths if path and path.strip()]

    @field_validator("flats")
    @classmethod
    def validate_unique_flats(cls, flats: list[WalkInFlatInput]) -> list[WalkInFlatInput]:
        """Reject duplicate unit targets in one visit."""
        unit_ids = [flat.unit_id for flat in flats]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("Duplicate unit_id values are not allowed in flats.")
        return flats


class RejectWalkInVisitUnitRequest(BaseModel):
    """Resident rejects a walk-in for their flat."""

    model_config = ConfigDict(extra="forbid")

    rejection_reason: str | None = Field(None, max_length=500)


class WalkInListQuery(BaseModel):
    """Security list filters for walk-in visits."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: WalkInStatus | None = None
    on_date: date | None = Field(default=None, alias="date")


class ResidentWalkInVisitUnitListQuery(BaseModel):
    """Resident list filters for visit units on my flats."""

    model_config = ConfigDict(extra="forbid")

    status: WalkInVisitUnitStatus | None = None


class WalkInVisitUnitResponse(BaseModel):
    """One flat row on a walk-in visit."""

    model_config = ConfigDict(extra="ignore")

    id: str
    tower_id: str
    unit_id: str
    tower_name: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None
    status: str
    rejection_reason: str | None = None
    approved_at: str | None = None
    rejected_at: str | None = None
    sort_order: int = 0


class WalkInEventResponse(BaseModel):
    """Timeline event on a walk-in visit."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: str
    actor_type: str | None = None
    actor_label: str | None = None
    occurred_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WalkInMilestoneResponse(BaseModel):
    """Derived milestone for mobile timeline."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    completed: bool
    occurred_at: str | None = None


class WalkInSummaryResponse(BaseModel):
    """Walk-in row for security list."""

    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: str
    visitor_first_name: str
    visitor_last_name: str | None = None
    visitor_phone_isd_code: str
    visitor_phone_number: str
    status: str
    flats_count: int
    approved_flats_count: int
    primary_unit_label: str | None = None
    notes: str | None = None
    requested_at: str
    entered_at: str | None = None
    exited_at: str | None = None


class WalkInDetailResponse(WalkInSummaryResponse):
    """Full walk-in detail with visit units and timeline."""

    visitor_photo_paths: list[str] = Field(default_factory=list)
    vehicle_photo_paths: list[str] = Field(default_factory=list)
    visit_units: list[WalkInVisitUnitResponse] = Field(default_factory=list)
    events: list[WalkInEventResponse] = Field(default_factory=list)
    milestones: list[WalkInMilestoneResponse] = Field(default_factory=list)


class ResidentWalkInVisitUnitListItemResponse(BaseModel):
    """Pending visit unit row for a resident."""

    model_config = ConfigDict(extra="ignore")

    visit_unit_id: str
    walk_in_entry_id: str
    tower_id: str
    unit_id: str
    tower_name: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None
    status: str
    visitor_first_name: str
    visitor_last_name: str | None = None
    visitor_phone_isd_code: str
    visitor_phone_number: str
    visitor_photo_paths: list[str] = Field(default_factory=list)
    notes: str | None = None
    requested_at: str
    flats_count: int
