"""Project setup schemas: projects, media, steps, and tower group."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    """Create a project (step 1: project basics)."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional project slug. Auto-generated from name when omitted.",
    )
    name: str = Field(..., min_length=1)
    developer_name: str = Field(..., min_length=1)
    community_admin_user_id: str = Field(
        ...,
        min_length=36,
        max_length=36,
        description="Supabase auth user id of the community admin (must be an org member)",
    )
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
    community_admin_user_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        description="Supabase auth user id of the community admin (must be an org member)",
    )
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
    community_admin_email: str | None = None
    community_admin_phone_number: str | None = None
    community_admin_phone_isd_code: str | None = None


class MyProjectSummaryResponse(ProjectSummaryResponse):
    """Project list row for projects assigned to the current user."""

    role: str


class CommunityAdminSummary(BaseModel):
    """Community admin assigned to a project."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    email: str | None = None
    phone_number: str | None = None
    phone_isd_code: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    salutation: str | None = None
    avatar_url: str | None = None
    display_name: str | None = None


class ProjectDetailsResponse(BaseModel):
    """Full project detail row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    code: str
    name: str
    developer_name: str
    community_admin_user_id: str
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
    community_admin: CommunityAdminSummary | None = None


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
    code: str | None = Field(default=None, min_length=1, max_length=64)
    tower_type: TowerType
    basement_count: int = Field(default=0, ge=0)
    upper_floor_count: int = Field(default=0, ge=0)
    units_per_floor_default: int | None = Field(default=None, ge=0)
    numbering_pattern: UnitNumberingPattern = UnitNumberingPattern.FLOOR_UNIT
    starting_unit_number: int = Field(default=1, ge=0)
    custom_prefix: str | None = Field(default=None, max_length=32)
    has_wings: bool = False
    latitude: float | None = None
    longitude: float | None = None
    sort_order: int = Field(default=0, ge=0)
    active: bool = True
    wings: list["CreateTowerWingRequest"] | None = None
    gates: list["CreateTowerGateBulkItem"] | None = None
    lifts: list[CreateTowerLiftRequest] | None = None
    floors: list["CreateFloorBulkItem"] | None = None


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
    custom_prefix: str | None = Field(default=None, max_length=32)
    has_wings: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    sort_order: int | None = Field(default=None, ge=0)
    active: bool | None = None
    wings: list["UpdateTowerWingItem"] | None = None
    gates: list["UpdateTowerGateBulkItem"] | None = None
    lifts: list["UpdateTowerLiftBulkItem"] | None = None
    floors: list["UpdateFloorBulkItem"] | None = None


class TowerDetailResponse(BaseModel):
    """Tower with nested wings, gates, lifts, and floors for the builder edit page."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    name: str
    code: str
    tower_type: str
    basement_count: int = 0
    upper_floor_count: int = 0
    units_per_floor_default: int | None = None
    numbering_pattern: str
    starting_unit_number: int = 1
    custom_prefix: str | None = None
    has_wings: bool = False
    latitude: float | None = None
    longitude: float | None = None
    sort_order: int = 0
    active: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    wings: list[dict[str, Any]] = Field(default_factory=list)
    gates: list[dict[str, Any]] = Field(default_factory=list)
    lifts: list[dict[str, Any]] = Field(default_factory=list)
    floors: list[dict[str, Any]] = Field(default_factory=list)


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


class CreateTowerGateBulkItem(CreateTowerGateRequest):
    """Gate payload nested under tower create (use wing_client_key, not wing_id)."""

    wing_client_key: str | None = Field(
        default=None,
        max_length=32,
        description="Optional wing reference (matches wing `code` or `name` from the same request).",
    )

    @model_validator(mode="after")
    def validate_bulk_wing_ref(self) -> CreateTowerGateBulkItem:
        """Nested tower create resolves wings by code or name."""
        if self.wing_id is not None:
            raise ValidationException(
                message_key="project_setup.errors.nested_wing_id_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


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


class CreateFloorBulkItem(CreateFloorRequest):
    """Floor payload nested under tower create (use wing_client_key, not wing_id)."""

    wing_client_key: str | None = Field(
        default=None,
        max_length=32,
        description="Optional wing reference (matches wing `code` or `name` from the same request).",
    )

    @model_validator(mode="after")
    def validate_bulk_wing_ref(self) -> CreateFloorBulkItem:
        """Nested tower create resolves wings by code or name."""
        if self.wing_id is not None:
            raise ValidationException(
                message_key="project_setup.errors.nested_wing_id_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class UpdateTowerWingItem(BaseModel):
    """Upsert a wing when patching a tower (omit id to create)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = Field(default=None, min_length=1)
    code: str | None = None
    has_own_gate: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_create_requires_name(self) -> UpdateTowerWingItem:
        """New wings require a name."""
        if self.id is None and not self.name:
            raise ValidationException(
                message_key="project_setup.errors.nested_item_name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class UpdateTowerGateBulkItem(BaseModel):
    """Upsert a gate when patching a tower (omit id to create)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    wing_client_key: str | None = Field(
        default=None,
        max_length=32,
        description="Optional wing reference (matches wing `code`, `name`, or `id`).",
    )
    name: str | None = Field(default=None, min_length=1)
    gate_type: GateType | None = None
    status: GateStatus | None = None
    is_open_24x7: bool | None = None
    operating_hours: dict[str, Any] | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bulk_gate(self) -> UpdateTowerGateBulkItem:
        """New gates require a name; nested updates resolve wings by wing_client_key."""
        if self.id is None and not self.name:
            raise ValidationException(
                message_key="project_setup.errors.nested_item_name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class UpdateTowerLiftBulkItem(BaseModel):
    """Upsert a lift when patching a tower (omit id to create)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = Field(default=None, min_length=1)
    lift_type: LiftType | None = None
    capacity_persons: int | None = Field(default=None, ge=0)
    brand: str | None = None
    status: LiftStatus | None = None
    serves_floors: list[int] | None = None
    sort_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_create_requires_name(self) -> UpdateTowerLiftBulkItem:
        """New lifts require a name."""
        if self.id is None and not self.name:
            raise ValidationException(
                message_key="project_setup.errors.nested_item_name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class UpdateFloorBulkItem(BaseModel):
    """Upsert a floor when patching a tower (omit id to create)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    wing_client_key: str | None = Field(
        default=None,
        max_length=32,
        description="Optional wing reference (matches wing `code`, `name`, or `id`).",
    )
    level_number: int | None = None
    display_name: str | None = Field(default=None, min_length=1)
    sort_order: int | None = Field(default=None, ge=0)
    is_parking: bool | None = None

    @model_validator(mode="after")
    def validate_bulk_floor(self) -> UpdateFloorBulkItem:
        """New floors require level_number and display_name."""
        if self.id is None:
            if self.level_number is None or not self.display_name:
                raise ValidationException(
                    message_key="project_setup.errors.nested_floor_fields_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return self


class CompleteStepRequest(BaseModel):
    """Optional data payload when completing a step."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] | None = None
