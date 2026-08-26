"""Parking allotment admin API schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import (
    ParkingAllotmentBasis,
    ParkingSlotDisplayStatus,
    ParkingSlotEventType,
    ParkingSlotType,
)


class ParkingAllotmentSummaryResponse(BaseModel):
    """Dashboard summary cards for parking allotment."""

    model_config = ConfigDict(extra="ignore")

    total_slots: int = 0
    allotted: int = 0
    free_to_allot: int = 0
    visitor_pool: int = 0
    blocked: int = 0
    units_short_of_entitlement: int = 0


class ParkingAllotmentUnitRefResponse(BaseModel):
    """Minimal unit reference on a slot row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str


class ParkingAllotmentSlotListItemResponse(BaseModel):
    """One row in the by-slot parking allotment table."""

    model_config = ConfigDict(extra="ignore")

    id: str
    slot_code: str
    level_label: str | None = None
    bay_label: str | None = None
    slot_type: ParkingSlotType
    slot_type_label: str
    status: ParkingSlotDisplayStatus
    allotted_to_unit: ParkingAllotmentUnitRefResponse | None = None
    allotted_since: str | None = None
    facility_id: str
    slot_number: int
    tower_id: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)


class ParkingAllotmentSlotHeldResponse(BaseModel):
    """Active slot held by a unit."""

    model_config = ConfigDict(extra="ignore")

    allotment_id: str
    slot_id: str
    slot_code: str
    slot_type: ParkingSlotType
    slot_type_label: str
    effective_from: str
    allotment_basis: ParkingAllotmentBasis


class ParkingAllotmentUnitListItemResponse(BaseModel):
    """One row in the by-unit parking allotment table."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str
    configuration_label: str | None = None
    two_wheeler_parking_entitlement: int = 0
    four_wheeler_parking_entitlement: int = 0
    slots_assigned: int = 0
    entitlement_status: str
    entitlement_short_by: int = 0
    slots_held: list[ParkingAllotmentSlotHeldResponse] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)


class ParkingAllotmentSlotDetailResponse(ParkingAllotmentSlotListItemResponse):
    """Full slot detail for the details drawer."""

    allotment_basis: ParkingAllotmentBasis | None = None
    effective_from: str | None = None
    facility_name: str | None = None
    tower_name: str | None = None
    updated_at: str | None = None


class ParkingAllotmentSlotEventResponse(BaseModel):
    """Audit timeline event for a parking slot."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: ParkingSlotEventType
    unit_id: str | None = None
    unit_code: str | None = None
    allotment_id: str | None = None
    actor_user_id: str | None = None
    payload: dict = Field(default_factory=dict)
    occurred_at: str


class AllotParkingSlotRequest(BaseModel):
    """Allot a free resident slot to a unit."""

    unit_id: str
    effective_from: date
    allotment_basis: ParkingAllotmentBasis = ParkingAllotmentBasis.INCLUDED_WITH_UNIT


class ReassignParkingSlotRequest(BaseModel):
    """Move an allotted slot to another unit."""

    unit_id: str
    effective_from: date
    allotment_basis: ParkingAllotmentBasis = ParkingAllotmentBasis.INCLUDED_WITH_UNIT
    reason: str | None = Field(default=None, max_length=500)


class ReleaseParkingSlotRequest(BaseModel):
    """Release an allotted slot back to inventory."""

    reason: str | None = Field(default=None, max_length=500)


class BlockParkingSlotRequest(BaseModel):
    """Block a free slot from allotment."""

    reason: str | None = Field(default=None, max_length=500)


class UnitAllotParkingSlotRequest(BaseModel):
    """Allot a specific slot to a unit from the by-unit view."""

    slot_id: str
    effective_from: date
    allotment_basis: ParkingAllotmentBasis = ParkingAllotmentBasis.INCLUDED_WITH_UNIT


class ParkingAllotmentListQuery(BaseModel):
    """Shared pagination for parking allotment list endpoints."""

    tower_id: str | None = None
    search: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class ParkingAllotmentSlotListQuery(ParkingAllotmentListQuery):
    """Filters for by-slot parking list."""

    facility_id: str | None = None
    floor_level: str | None = Field(default=None, max_length=50)
    slot_type: ParkingSlotType | None = None
    status: ParkingSlotDisplayStatus | None = None


class ParkingAllotmentUnitListQuery(ParkingAllotmentListQuery):
    """Filters for by-unit parking list."""

    entitlement_status: str | None = Field(
        default=None,
        description="Filter by entitlement met or short: met | short",
    )


class ParkingAllotmentSummaryQuery(BaseModel):
    """Optional tower/facility scope for parking summary."""

    tower_id: str | None = None
    facility_id: str | None = None


class ParkingAllotmentSummaryApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: ParkingAllotmentSummaryResponse


class ParkingAllotmentSlotListApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: list[ParkingAllotmentSlotListItemResponse]
    total: int
    page: int
    page_size: int


class ParkingAllotmentUnitListApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: list[ParkingAllotmentUnitListItemResponse]
    total: int
    page: int
    page_size: int


class ParkingAllotmentUnitDetailApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: ParkingAllotmentUnitListItemResponse


class ParkingAllotmentSlotDetailApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: ParkingAllotmentSlotDetailResponse


class ParkingAllotmentSlotHistoryApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: list[ParkingAllotmentSlotEventResponse]


class ParkingAllotmentSlotMutationApiResponse(BaseModel):
    status: str
    message: str
    statusCode: int
    code: str
    data: ParkingAllotmentSlotDetailResponse
