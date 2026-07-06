"""Project setup schemas: projects, media, steps, and tower group."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import (
    GateStatus,
    GateType,
    LiftStatus,
    LiftType,
    MeasurementUnit,
    ProjectMediaKind,
    PropertyProjectStatus,
    PropertyType,
    TowerType,
    UnitNumberingPattern,
)

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    """Create a project (step 1: project basics)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    developer_name: str = Field(..., min_length=1)
    community_admin_email: str = Field(..., min_length=3)
    gstin: str = Field(..., min_length=15, max_length=15)
    possession_date: date | None = None
    address_line_1: str = Field(..., min_length=1)
    address_line_2: str | None = None
    pin_code: str = Field(..., min_length=1)
    city: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    country: str = Field(..., min_length=1)
    latitude: float | None = None
    longitude: float | None = None
    property_types: list[PropertyType] = Field(default_factory=list)
    primary_measurement_unit: MeasurementUnit
    units_count: int | None = Field(default=None, ge=0)


class UpdateProjectRequest(BaseModel):
    """Patch a project. property_types changes re-seed setup steps."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1)
    developer_name: str | None = Field(default=None, min_length=1)
    community_admin_email: str | None = Field(default=None, min_length=3)
    gstin: str | None = Field(default=None, min_length=15, max_length=15)
    possession_date: date | None = None
    address_line_1: str | None = Field(default=None, min_length=1)
    address_line_2: str | None = None
    pin_code: str | None = Field(default=None, min_length=1)
    city: str | None = Field(default=None, min_length=1)
    state: str | None = Field(default=None, min_length=1)
    country: str | None = Field(default=None, min_length=1)
    latitude: float | None = None
    longitude: float | None = None
    property_types: list[PropertyType] | None = None
    primary_measurement_unit: MeasurementUnit | None = None
    status: PropertyProjectStatus | None = None


class ListProjectsRequest(BaseModel):
    """Request body for listing projects."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(default=None, min_length=2)
    status: PropertyProjectStatus | None = None
    property_type: PropertyType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ProjectSummaryResponse(BaseModel):
    """List row for a project."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    code: str
    name: str
    developer_name: str
    city: str
    state: str
    status: str
    property_types: list[str] = Field(default_factory=list)
    primary_measurement_unit: str
    units_count: int = 0
    setup_current_step: str
    created_at: str
    updated_at: str


class ProjectDetailsResponse(BaseModel):
    """Full project detail row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    code: str
    name: str
    developer_name: str
    community_admin_email: str
    gstin: str
    possession_date: str | None = None
    address_line_1: str
    address_line_2: str | None = None
    pin_code: str
    city: str
    state: str
    country: str
    latitude: float | None = None
    longitude: float | None = None
    property_types: list[str] = Field(default_factory=list)
    primary_measurement_unit: str
    status: str
    units_count: int = 0
    setup_current_step: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Project media
# ---------------------------------------------------------------------------


class ProjectMediaRequest(BaseModel):
    """Store media metadata exactly as provided in the payload."""

    model_config = ConfigDict(extra="forbid")

    kind: ProjectMediaKind
    path: str = Field(..., min_length=1)
    mime: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)
    original_name: str | None = None
    sort_order: int = Field(default=0, ge=0)


class ProjectMediaResponse(BaseModel):
    """Project media row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: str
    kind: str
    path: str
    mime: str
    size_bytes: int
    original_name: str | None = None
    sort_order: int = 0
    created_at: str


# ---------------------------------------------------------------------------
# Setup steps / status
# ---------------------------------------------------------------------------


class ProjectStepResponse(BaseModel):
    """One setup step row."""

    model_config = ConfigDict(extra="ignore")

    step_key: str
    status: str
    completed_at: str | None = None
    updated_at: str | None = None


class ProjectStatusResponse(BaseModel):
    """Wizard status snapshot."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    status: str
    setup_current_step: str
    is_completed: bool = False
    steps: list[ProjectStepResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tower group (towers, wings, gates, lifts, floors)
# ---------------------------------------------------------------------------


class CreateTowerRequest(BaseModel):
    """Create a tower."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1, max_length=64)
    tower_type: TowerType
    basement_count: int = Field(default=0, ge=0)
    upper_floor_count: int = Field(default=0, ge=0)
    units_per_floor_default: int | None = Field(default=None, ge=0)
    numbering_pattern: UnitNumberingPattern = UnitNumberingPattern.FLOOR_UNIT
    starting_unit_number: int = Field(default=1, ge=0)
    has_wings: bool = False
    latitude: float | None = None
    longitude: float | None = None
    sort_order: int = Field(default=0, ge=0)
    active: bool = True


class UpdateTowerRequest(BaseModel):
    """Patch a tower."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    tower_type: TowerType | None = None
    basement_count: int | None = Field(default=None, ge=0)
    upper_floor_count: int | None = Field(default=None, ge=0)
    units_per_floor_default: int | None = Field(default=None, ge=0)
    numbering_pattern: UnitNumberingPattern | None = None
    starting_unit_number: int | None = Field(default=None, ge=0)
    has_wings: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    sort_order: int | None = Field(default=None, ge=0)
    active: bool | None = None


class CreateTowerWingRequest(BaseModel):
    """Create a tower wing."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    code: str | None = None
    has_own_gate: bool = False
    sort_order: int = Field(default=0, ge=0)


class CreateTowerGateRequest(BaseModel):
    """Create a tower gate."""

    model_config = ConfigDict(extra="forbid")

    wing_id: str | None = None
    name: str = Field(..., min_length=1)
    gate_type: GateType = GateType.BOTH
    status: GateStatus = GateStatus.ACTIVE
    is_open_24x7: bool = False
    operating_hours: dict[str, Any] | None = None
    sort_order: int = Field(default=0, ge=0)


class CreateTowerLiftRequest(BaseModel):
    """Create a tower lift."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    lift_type: LiftType = LiftType.PASSENGER
    capacity_persons: int | None = Field(default=None, ge=0)
    brand: str | None = None
    status: LiftStatus = LiftStatus.OPERATIONAL
    serves_floors: list[int] = Field(default_factory=list)
    sort_order: int = Field(default=0, ge=0)


class CreateFloorRequest(BaseModel):
    """Create a floor."""

    model_config = ConfigDict(extra="forbid")

    wing_id: str | None = None
    level_number: int
    display_name: str = Field(..., min_length=1)
    sort_order: int = Field(default=0, ge=0)
    is_parking: bool = False


class CompleteStepRequest(BaseModel):
    """Optional data payload when completing a step."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] | None = None
