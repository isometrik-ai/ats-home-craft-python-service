"""Visitor logs admin schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    PassAccessStatus,
    PassEntryMethod,
    PassType,
    VisitorLogBucket,
    VisitorLogVisitStatus,
    VisitorType,
)
from apps.user_service.app.schemas.passes import PassEventResponse, PassResponse
from apps.user_service.app.schemas.walk_in import (
    WalkInDetailResponse,
    WalkInVisitUnitResponse,
)
from libs.shared_utils.status_codes import CustomStatusCode

_EXAMPLE_TIMESTAMP = "2026-08-06T12:35:18+00:00"
_EXAMPLE_PASS_ID = "323d45d8-ba1b-4bf9-988c-ede6043fe566"
_EXAMPLE_WALK_IN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_EXAMPLE_OWNER_CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
_EXAMPLE_TENANT_CONTACT_ID = "880e8400-e29b-41d4-a716-446655440003"

_EXAMPLE_UNIT_RESIDENT = {
    "contact_id": _EXAMPLE_OWNER_CONTACT_ID,
    "person_name": "Ms. Radhi Sharma",
    "role": "Owner",
}

_EXAMPLE_PASS_LOG_ITEM = {
    "source": "pass",
    "pass_id": _EXAMPLE_PASS_ID,
    "pass_type": PassType.GUEST.value,
    "guest_name": "Mayur Sharma",
    "visitor_phone_isd_code": "+91",
    "visitor_phone_number": "9876543210",
    "unit_label": "T4103",
    "tower_name": "Tower A — Luxury Royale 1",
    "resident": _EXAMPLE_UNIT_RESIDENT,
    "created_by": "Radhi Sharma",
    "scheduled_from": "2026-08-05T18:30:00+00:00",
    "scheduled_until": "2026-08-13T18:29:59+00:00",
    "validity_type": "recurring",
    "entry_method": PassEntryMethod.CODE.value,
    "guard_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
    "guard_name": "Mr. Ajay Thakur Guard",
    "access_status": PassAccessStatus.APPROVED.value,
    "visit_status": VisitorLogVisitStatus.EXITED.value,
    "visitor_type": VisitorType.GUEST.value,
    "pass_code": "4821",
    "is_private": False,
    "in_time": _EXAMPLE_TIMESTAMP,
    "out_time": "2026-08-06T12:35:29+00:00",
    "time_spent_minutes": 0,
    "pass_image_url": "https://media.houseofapps.ai/org/passes/pass-4821.png",
}

_EXAMPLE_WALK_IN_LOG_ITEM = {
    "source": "walk_in",
    "pass_id": _EXAMPLE_WALK_IN_ID,
    "pass_type": PassType.WALK_IN.value,
    "guest_name": "Ravi Delivery",
    "visitor_phone_isd_code": "+91",
    "visitor_phone_number": "9876501234",
    "unit_label": "T4102 (+1 more)",
    "tower_name": "Tower A — Luxury Royale 1",
    "resident": _EXAMPLE_UNIT_RESIDENT,
    "created_by": "Mr. Ajay Thakur Guard",
    "scheduled_from": "2026-08-07T09:15:00+00:00",
    "scheduled_until": None,
    "validity_type": None,
    "entry_method": PassEntryMethod.MANUAL.value,
    "guard_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
    "guard_name": "Mr. Ajay Thakur Guard",
    "access_status": PassAccessStatus.GRANTED.value,
    "visit_status": VisitorLogVisitStatus.EXITED.value,
    "visitor_type": VisitorType.VISITOR.value,
    "pass_code": None,
    "is_private": False,
    "in_time": "2026-08-07T09:22:00+00:00",
    "out_time": "2026-08-07T10:05:00+00:00",
    "time_spent_minutes": 43,
    "visitor_photo_urls": ["https://media.houseofapps.ai/org/walk-ins/photo-1.jpg"],
    "vehicle_photo_urls": [],
}


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
    bucket: VisitorLogBucket = Field(
        default=VisitorLogBucket.ALL,
        description="Tab filter: all, awaiting_approval, inside_now, completed, denied_expired.",
    )
    visitor_type: VisitorType | None = Field(
        None,
        description='High-level visitor category: "guest" or "visitor".',
    )
    pass_type: PassType | None = None
    entry_method: PassEntryMethod | None = None
    access_status: PassAccessStatus | None = None
    tower_id: str | None = None
    guard_user_id: str | None = None
    project_id: str = Field(..., description="Project identifier (UUID string).")
    unit_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class VisitorLogOverviewQuery(VisitorLogDateRangeQuery):
    """Query params for GET /visitor-logs/overview."""

    project_id: str = Field(..., description="Project identifier (UUID string).")
    unit_id: str | None = None


class VisitorLogResidentResponse(BaseModel):
    """Flat resident who requested or approved the visit."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(..., description="Contact UUID for the flat resident.")
    person_name: str = Field(..., description="Display name of the flat resident.")
    role: Literal["Owner", "Tenant", "Family"] | None = Field(
        None,
        description=(
            'Household role on the visited flat when available: "Owner", "Tenant", or "Family".'
        ),
    )


class VisitorLogItemResponse(BaseModel):
    """Single row in the Visitor Logs table."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_PASS_LOG_ITEM},
    )

    source: str = Field(..., description='Visit source: "pass" or "walk_in".')
    pass_id: str = Field(..., description="Pass UUID or walk-in entry UUID.")
    pass_type: str = Field(..., description="Pass type, or walk_in for gate walk-ins.")
    guest_name: str | None = None
    visitor_phone_isd_code: str | None = Field(
        None,
        description="Visitor phone country/ISD code.",
    )
    visitor_phone_number: str | None = Field(
        None,
        description="Visitor phone number without ISD code.",
    )
    unit_label: str | None = None
    tower_name: str | None = None
    resident: VisitorLogResidentResponse | None = Field(
        None,
        description=(
            "Person who requested (pass) or approved (walk-in) the visit; "
            "role is included when they hold Owner, Tenant, or Family on the flat."
        ),
    )
    created_by: str | None = None
    scheduled_from: str | None = None
    scheduled_until: str | None = None
    validity_type: str | None = None
    entry_method: str | None = None
    guard_user_id: str | None = None
    guard_name: str | None = None
    access_status: str | None = None
    visit_status: str = Field(
        ...,
        description=(
            "Unified row status: awaiting_approval, approved, inside, exited, expired, or denied."
        ),
    )
    visitor_type: str = Field(
        ...,
        description='High-level category: "guest" or "visitor".',
    )
    pass_code: str | None = Field(
        None,
        description="Pass entry code (pass rows only).",
    )
    is_private: bool = Field(
        False,
        description="Whether the pass is marked private (pass rows only).",
    )
    in_time: str | None = None
    out_time: str | None = None
    time_spent_minutes: int | None = None
    pass_image_url: str | None = Field(
        None,
        description="Public URL for the pass QR/image (pass rows only).",
    )
    visitor_photo_urls: list[str] = Field(
        default_factory=list,
        description="Public visitor photo URLs (walk-in rows only).",
    )
    vehicle_photo_urls: list[str] = Field(
        default_factory=list,
        description="Public vehicle photo URLs (walk-in rows only).",
    )


class VisitorLogListApiResponse(BaseModel):
    """API envelope for GET /visitor-logs."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Visitor logs retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": [_EXAMPLE_PASS_LOG_ITEM, _EXAMPLE_WALK_IN_LOG_ITEM],
                "total": 2,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
            }
        }
    )

    status: str = Field(..., description="Response status.")
    message: str = Field(..., description="Human-readable message.")
    statusCode: int = Field(..., description="HTTP status code.")
    code: str = Field(..., description="Application status code.")
    data: list[VisitorLogItemResponse] = Field(..., description="Visitor log rows.")
    total: int = Field(..., description="Total rows matching filters.")
    page: int = Field(..., description="Current page number.")
    page_size: int = Field(..., description="Page size.")
    total_pages: int = Field(..., description="Total number of pages.")


class VisitorLogOverviewResponse(BaseModel):
    """Overview cards for the Visitor Logs dashboard."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "start_at": "2026-08-01T00:00:00+00:00",
                "end_at": "2026-09-01T00:00:00+00:00",
                "total_entries": 12,
                "inside_now": 3,
                "awaiting_approval": 1,
                "walk_ins": 6,
                "exited": 2,
                "denied_expired": 3,
            }
        },
    )

    start_at: str
    end_at: str
    total_entries: int = Field(..., description="All pass and walk-in rows in the date range.")
    inside_now: int = Field(..., description="Entered visits not yet marked exit.")
    awaiting_approval: int = Field(..., description="Walk-ins pending resident approval.")
    walk_ins: int = Field(..., description="Walk-in entries raised at the gate in range.")
    exited: int = Field(
        ...,
        description="Visits marked exit (visit_status=exited; matches the Completed tab filter).",
    )
    denied_expired: int = Field(
        ...,
        description="Expired passes and denied/cancelled visits with no entry.",
    )


class VisitorLogOverviewApiResponse(BaseModel):
    """API envelope for GET /visitor-logs/overview."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Visitor log overview retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {
                    "start_at": "2026-08-01T00:00:00+00:00",
                    "end_at": "2026-09-01T00:00:00+00:00",
                    "total_entries": 12,
                    "inside_now": 3,
                    "awaiting_approval": 1,
                    "walk_ins": 6,
                    "exited": 2,
                    "denied_expired": 3,
                },
            }
        }
    )

    status: str = Field(..., description="Response status.")
    message: str = Field(..., description="Human-readable message.")
    statusCode: int = Field(..., description="HTTP status code.")
    code: str = Field(..., description="Application status code.")
    data: VisitorLogOverviewResponse = Field(..., description="Overview card metrics.")


class VisitorLogPassDetailData(PassResponse):
    """Pass detail payload returned when source is pass."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "source": "pass",
                "id": _EXAMPLE_PASS_ID,
                "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "project_id": "550e8400-e29b-41d4-a716-446655440000",
                "unit_id": "660e8400-e29b-41d4-a716-446655440001",
                "host_contact_id": "770e8400-e29b-41d4-a716-446655440002",
                "pass_type": PassType.GUEST.value,
                "guest_name": "Mayur Sharma",
                "guest_phone_isd_code": "+91",
                "guest_phone_number": "9876543210",
                "visitor_count": 1,
                "vehicle_number": None,
                "purpose": "Visit",
                "valid_from": "2026-08-05T18:30:00+00:00",
                "valid_until": "2026-08-13T18:29:59+00:00",
                "validity_type": "recurring",
                "allow_multiple_entries": False,
                "is_private": False,
                "max_entries": None,
                "entry_count": 1,
                "status": "active",
                "display_status": "active",
                "code": "4821",
                "pass_image_path": None,
                "notes": None,
                "unit_code": "T4103",
                "unit_label": "T4103",
                "tower_name": "Tower A — Luxury Royale 1",
                "floor_name": None,
                "config_label": None,
                "created_at": "2026-08-05T18:00:00+00:00",
                "updated_at": _EXAMPLE_TIMESTAMP,
                "created_by": "Radhi Sharma",
                "guard_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
                "guard_name": "Mr. Ajay Thakur Guard",
                "visit_status": VisitorLogVisitStatus.EXITED.value,
                "visitor_type": VisitorType.GUEST.value,
                "entry_method": PassEntryMethod.CODE.value,
                "time_spent_minutes": 0,
                "resident": _EXAMPLE_UNIT_RESIDENT,
                "pass_image_url": "https://media.houseofapps.ai/org/passes/pass-4821.png",
                "image_urls": ["https://media.houseofapps.ai/org/passes/pass-4821.png"],
                "events": [
                    {
                        "id": "evt-check-in-1",
                        "event_type": "checked_in",
                        "gate_id": "gate-1",
                        "actor_type": "staff",
                        "actor_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
                        "actor_label": None,
                        "occurred_at": _EXAMPLE_TIMESTAMP,
                        "notes": None,
                        "metadata": {},
                        "entry_method": PassEntryMethod.CODE.value,
                        "access_status": PassAccessStatus.APPROVED.value,
                    }
                ],
            }
        },
    )

    source: Literal["pass"] = Field(default="pass", description='Always "pass" for this shape.')
    created_by: str | None = Field(None, description="Resident who created the pass.")
    guard_user_id: str | None = Field(None, description="Staff user who checked the visitor in.")
    guard_name: str | None = Field(None, description="Display name of the guard at check-in.")
    visit_status: str = Field(
        ...,
        description=(
            "Unified visit status aligned with the list API: awaiting_approval, approved, "
            "inside, exited, expired, or denied."
        ),
    )
    visitor_type: str = Field(
        ...,
        description="High-level visitor category (guest or visitor), aligned with the list API.",
    )
    entry_method: str | None = Field(
        None,
        description="How the visitor was admitted at the gate (from the latest check-in event).",
    )
    time_spent_minutes: int | None = Field(
        None,
        description="Minutes between check-in and check-out when both exist.",
    )
    visitor_phone_isd_code: str | None = Field(
        None,
        description="Visitor phone country/ISD code (same as guest_phone_isd_code for passes).",
    )
    visitor_phone_number: str | None = Field(
        None,
        description="Visitor phone number (same as guest_phone_number for passes).",
    )
    resident: VisitorLogResidentResponse | None = Field(
        None,
        description="Flat resident who requested the pass.",
    )
    pass_image_url: str | None = Field(None, description="Public URL for the pass QR/image.")
    image_urls: list[str] = Field(
        default_factory=list,
        description="All image URLs for this visit (pass image).",
    )
    events: list[PassEventResponse] = Field(default_factory=list)


class VisitorLogWalkInVisitUnitResponse(WalkInVisitUnitResponse):
    """Walk-in visit unit with the approving flat resident."""

    resident: VisitorLogResidentResponse | None = None


class VisitorLogWalkInDetailData(WalkInDetailResponse):
    """Walk-in detail payload returned when source is walk_in."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "source": "walk_in",
                "id": _EXAMPLE_WALK_IN_ID,
                "project_id": "550e8400-e29b-41d4-a716-446655440000",
                "visitor_first_name": "Ravi",
                "visitor_last_name": "Delivery",
                "visitor_phone_isd_code": "+91",
                "visitor_phone_number": "9876501234",
                "status": "exited",
                "flats_count": 2,
                "approved_flats_count": 2,
                "primary_unit_label": "T4102",
                "notes": "Delivery at gate",
                "requested_at": "2026-08-07T09:15:00+00:00",
                "entered_at": "2026-08-07T09:22:00+00:00",
                "exited_at": "2026-08-07T10:05:00+00:00",
                "created_by": "Mr. Ajay Thakur Guard",
                "guard_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
                "guard_name": "Mr. Ajay Thakur Guard",
                "visit_status": VisitorLogVisitStatus.EXITED.value,
                "visitor_type": VisitorType.VISITOR.value,
                "entry_method": PassEntryMethod.MANUAL.value,
                "vehicle_number": None,
                "time_spent_minutes": 43,
                "resident": _EXAMPLE_UNIT_RESIDENT,
                "visitor_photo_urls": ["https://media.houseofapps.ai/org/walk-ins/photo-1.jpg"],
                "vehicle_photo_urls": [],
                "image_urls": ["https://media.houseofapps.ai/org/walk-ins/photo-1.jpg"],
                "visitor_photo_paths": ["/storage/walk-ins/photo-1.jpg"],
                "vehicle_photo_paths": [],
                "visit_units": [
                    {
                        "id": "vu-1",
                        "tower_id": "tower-1",
                        "unit_id": "unit-1",
                        "tower_name": "Tower A — Luxury Royale 1",
                        "unit_code": "T4102",
                        "unit_label": "T4102",
                        "status": "approved",
                        "rejection_reason": None,
                        "approved_at": "2026-08-07T09:18:00+00:00",
                        "rejected_at": None,
                        "sort_order": 0,
                        "resident": _EXAMPLE_UNIT_RESIDENT,
                    }
                ],
                "events": [
                    {
                        "id": "evt-enter-1",
                        "event_type": "entered",
                        "actor_type": "staff",
                        "actor_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
                        "actor_label": None,
                        "occurred_at": "2026-08-07T09:22:00+00:00",
                        "payload": {},
                    }
                ],
                "milestones": [],
            }
        },
    )

    source: Literal["walk_in"] = Field(
        default="walk_in",
        description='Always "walk_in" for this shape.',
    )
    created_by: str | None = Field(None, description="Staff user who registered the walk-in.")
    guard_user_id: str | None = Field(
        None, description="Staff user who marked the visitor entered."
    )
    guard_name: str | None = Field(None, description="Display name of the guard at entry.")
    visit_status: str = Field(
        ...,
        description=(
            "Unified visit status aligned with the list API: awaiting_approval, approved, "
            "inside, exited, expired, or denied."
        ),
    )
    visitor_type: str = Field(
        ...,
        description="High-level visitor category (always visitor for walk-ins).",
    )
    entry_method: str = Field(
        ...,
        description="Walk-ins are always recorded as manual gate entry.",
    )
    vehicle_number: str | None = Field(
        None,
        description="Vehicle registration when captured (walk-ins do not store this today).",
    )
    time_spent_minutes: int | None = Field(
        None,
        description="Minutes between entered_at and exited_at when both exist.",
    )
    resident: VisitorLogResidentResponse | None = Field(
        None,
        description="Flat resident who approved the primary visited flat.",
    )
    visitor_photo_urls: list[str] = Field(
        default_factory=list,
        description="Public URLs for visitor photos.",
    )
    vehicle_photo_urls: list[str] = Field(
        default_factory=list,
        description="Public URLs for vehicle photos.",
    )
    image_urls: list[str] = Field(
        default_factory=list,
        description="All image URLs for this visit (visitor + vehicle photos).",
    )
    visit_units: list[VisitorLogWalkInVisitUnitResponse] = Field(default_factory=list)


class VisitorLogDetailApiResponse(BaseModel):
    """API envelope for GET /visitor-logs/{pass_id}."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Visitor log detail retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {
                    "source": "pass",
                    "id": _EXAMPLE_PASS_ID,
                    "pass_type": PassType.GUEST.value,
                    "guest_name": "Mayur Sharma",
                    "visitor_phone_isd_code": "+91",
                    "visitor_phone_number": "9876543210",
                    "status": "active",
                    "display_status": "active",
                    "code": "4821",
                    "created_by": "Radhi Sharma",
                    "guard_user_id": "d4772ff8-eb05-47f7-84ee-b235ff512157",
                    "guard_name": "Mr. Ajay Thakur Guard",
                    "visit_status": VisitorLogVisitStatus.EXITED.value,
                    "visitor_type": VisitorType.GUEST.value,
                    "entry_method": PassEntryMethod.CODE.value,
                    "vehicle_number": None,
                    "time_spent_minutes": 43,
                    "resident": _EXAMPLE_UNIT_RESIDENT,
                    "pass_image_url": "https://media.houseofapps.ai/org/passes/pass-4821.png",
                    "image_urls": ["https://media.houseofapps.ai/org/passes/pass-4821.png"],
                    "events": [
                        {
                            "id": "evt-check-in-1",
                            "event_type": "checked_in",
                            "occurred_at": _EXAMPLE_TIMESTAMP,
                            "entry_method": PassEntryMethod.CODE.value,
                            "access_status": PassAccessStatus.APPROVED.value,
                        }
                    ],
                },
            }
        }
    )

    status: str = Field(..., description="Response status.")
    message: str = Field(..., description="Human-readable message.")
    statusCode: int = Field(..., description="HTTP status code.")
    code: str = Field(..., description="Application status code.")
    data: VisitorLogPassDetailData | VisitorLogWalkInDetailData = Field(
        ...,
        description='Pass detail when source is "pass"; walk-in detail when source is "walk_in".',
    )


class VisitorLogDetailResponse(PassResponse):
    """Pass detail with full timeline for admin visitor logs."""

    model_config = ConfigDict(extra="ignore")

    events: list[PassEventResponse] = Field(default_factory=list)
