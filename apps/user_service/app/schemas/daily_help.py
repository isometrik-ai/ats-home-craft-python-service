"""Daily help schemas."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    DailyHelpAvailabilityPeriod,
    DailyHelpCategoryStatus,
    DailyHelpDocumentType,
    DailyHelpRatingTrait,
    DailyHelpStatus,
)
from libs.shared_utils.status_codes import CustomStatusCode


class DailyHelpDocumentInput(BaseModel):
    """Document upload slot on create or add-document."""

    model_config = ConfigDict(extra="forbid")

    document_type: DailyHelpDocumentType
    label: str | None = Field(None, max_length=255)
    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)
    mime_type: str | None = Field(None, max_length=100)
    file_size_bytes: int | None = Field(None, ge=1)
    sort_order: int = Field(0, ge=0)


class CreateDailyHelpRequest(BaseModel):
    """Admin creates a daily help profile with optional documents."""

    model_config = ConfigDict(extra="forbid")

    initials: str | None = Field(None, max_length=20)
    first_name: str = Field(..., min_length=1, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone_isd_code: str = Field(..., min_length=1, max_length=10)
    phone_number: str = Field(..., min_length=1, max_length=20)
    alternate_phone_isd_code: str | None = Field(None, max_length=10)
    alternate_phone_number: str | None = Field(None, max_length=20)
    category_id: str
    gender: str | None = Field(None, max_length=50)
    date_of_birth: date | None = None
    photo_path: str | None = Field(None, max_length=2000)
    open_to_work: bool = True
    documents: list[DailyHelpDocumentInput] = Field(default_factory=list, max_length=20)


class SetDailyHelpOpenToWorkRequest(BaseModel):
    """Resident toggles whether a household-linked helper is open to work."""

    model_config = ConfigDict(extra="forbid")

    open_to_work: bool


class RemoveDailyHelpHouseholdLinkRequest(BaseModel):
    """Optional context when a resident removes a household link."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=500)


class UpdateDailyHelpRequest(BaseModel):
    """Admin patches identity and contact fields on a profile."""

    model_config = ConfigDict(extra="forbid")

    initials: str | None = Field(None, max_length=20)
    first_name: str | None = Field(None, min_length=1, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone_isd_code: str | None = Field(None, min_length=1, max_length=10)
    phone_number: str | None = Field(None, min_length=1, max_length=20)
    alternate_phone_isd_code: str | None = Field(None, max_length=10)
    alternate_phone_number: str | None = Field(None, max_length=20)
    category_id: str | None = None
    gender: str | None = Field(None, max_length=50)
    date_of_birth: date | None = None
    photo_path: str | None = Field(None, max_length=2000)
    open_to_work: bool | None = None


class AddDailyHelpDocumentRequest(BaseModel):
    """Add one document to an existing profile."""

    model_config = ConfigDict(extra="forbid")

    document_type: DailyHelpDocumentType
    label: str | None = Field(None, max_length=255)
    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)
    mime_type: str | None = Field(None, max_length=100)
    file_size_bytes: int | None = Field(None, ge=1)
    sort_order: int = Field(0, ge=0)


class CreateDailyHelpCategoryRequest(BaseModel):
    """Create a project-scoped daily help category."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    sort_order: int = Field(0, ge=0)


class UpdateDailyHelpCategoryRequest(BaseModel):
    """Update category label, order, or status."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    sort_order: int | None = Field(None, ge=0)
    status: DailyHelpCategoryStatus | None = None


class DailyHelpAvailabilitySlotInput(BaseModel):
    """One free-time window on a profile."""

    model_config = ConfigDict(extra="forbid")

    period: DailyHelpAvailabilityPeriod = DailyHelpAvailabilityPeriod.OTHER
    start_time: time
    end_time: time
    sort_order: int = Field(0, ge=0)

    @model_validator(mode="after")
    def validate_time_order(self) -> DailyHelpAvailabilitySlotInput:
        """Require end_time after start_time (full_day may use 00:00–23:59)."""
        if self.period == DailyHelpAvailabilityPeriod.FULL_DAY:
            return self
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ReplaceDailyHelpAvailabilityRequest(BaseModel):
    """Replace all availability slots on a profile."""

    model_config = ConfigDict(extra="forbid")

    slots: list[DailyHelpAvailabilitySlotInput] = Field(default_factory=list, max_length=20)


class CreateDailyHelpRatingRequest(BaseModel):
    """Resident rates a daily help profile after a visit."""

    model_config = ConfigDict(extra="forbid")

    stars: Decimal = Field(..., ge=Decimal("0.5"), le=Decimal("5.0"))
    comment: str | None = Field(None, max_length=2000)
    traits: list[DailyHelpRatingTrait] = Field(default_factory=list, max_length=10)


class DailyHelpListQuery(BaseModel):
    """Admin list filters for GET /projects/{project_id}/daily-help."""

    model_config = ConfigDict(extra="forbid")

    status: DailyHelpStatus | None = None
    category_id: str | None = None
    search: str | None = Field(None, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class DailyHelpExportQuery(BaseModel):
    """Export filters aligned with the admin list."""

    model_config = ConfigDict(extra="forbid")

    status: DailyHelpStatus | None = None
    category_id: str | None = None
    search: str | None = Field(None, max_length=200)
    format: str = Field("csv", pattern="^(csv|xlsx)$")


class ResidentDailyHelpListQuery(BaseModel):
    """Resident directory list filters."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    category_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ResidentDailyHelpSearchQuery(BaseModel):
    """Resident search by name, mobile, or gate passcode."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    q: str = Field(..., min_length=1, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class DailyHelpCategoryResponse(BaseModel):
    """Project category row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    name: str
    sort_order: int = 0
    status: str
    created_at: str | None = None
    updated_at: str | None = None


class DailyHelpDocumentResponse(BaseModel):
    """Document row on a daily help profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    document_type: str
    label: str | None = None
    file_path: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size_bytes: int | None = None
    sort_order: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class DailyHelpEventResponse(BaseModel):
    """Audit timeline event on a profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: str
    actor_type: str | None = None
    actor_user_id: str | None = None
    actor_contact_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = None


class DailyHelpHouseholdLinkResponse(BaseModel):
    """Active or removed household link."""

    model_config = ConfigDict(extra="ignore")

    id: str
    unit_id: str
    linked_by_contact_id: str | None = None
    status: str
    started_at: str | None = None
    removed_at: str | None = None
    removal_reason: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None


class DailyHelpAvailabilitySlotResponse(BaseModel):
    """Availability slot on a profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    period: str
    start_time: str
    end_time: str
    sort_order: int = 0


class DailyHelpRatingSummaryResponse(BaseModel):
    """Aggregated rating stats for a profile."""

    model_config = ConfigDict(extra="ignore")

    rating_count: int = 0
    average_stars: float = 0.0
    trait_counts: dict[str, int] = Field(default_factory=dict)


class DailyHelpAttendanceCheckInResponse(BaseModel):
    """One gate check-in event for a daily help profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    occurred_at: str | None = None


class DailyHelpAttendanceResponse(BaseModel):
    """Check-in attendance derived from the linked gate pass."""

    model_config = ConfigDict(extra="ignore")

    check_in_count: int = 0
    events: list[DailyHelpAttendanceCheckInResponse] = Field(default_factory=list)


class DailyHelpSummaryResponse(BaseModel):
    """Admin dashboard summary cards."""

    model_config = ConfigDict(extra="ignore")

    total: int = 0
    active: int = 0
    inactive: int = 0
    deleted: int = 0


class DailyHelpListItemResponse(BaseModel):
    """Daily help row for admin list table."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    display_name: str
    gender: str | None = None
    category_id: str
    category_name: str | None = None
    phone_isd_code: str
    phone_number: str
    phone: str | None = None
    document_count: int = 0
    household_link_count: int = 0
    status: str
    gate_passcode: str | None = None
    open_to_work: bool = False
    created_at: str | None = None
    created_on: str | None = None


class CreateDailyHelpResponse(BaseModel):
    """Response after admin creates a daily help profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    display_name: str
    category_id: str
    category_name: str | None = None
    status: str
    gate_passcode: str
    document_count: int = 0
    linked_pass_id: str | None = None
    created_at: str | None = None
    created_by_name: str | None = None


class DailyHelpDetailResponse(BaseModel):
    """Full daily help profile detail."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    initials: str | None = None
    first_name: str
    middle_name: str | None = None
    last_name: str
    display_name: str
    phone_isd_code: str
    phone_number: str
    phone: str | None = None
    alternate_phone_isd_code: str | None = None
    alternate_phone_number: str | None = None
    category_id: str
    category_name: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    photo_path: str | None = None
    gate_passcode: str
    status: str
    open_to_work: bool = False
    linked_pass_id: str | None = None
    document_count: int = 0
    household_link_count: int = 0
    documents: list[DailyHelpDocumentResponse] = Field(default_factory=list)
    events: list[DailyHelpEventResponse] = Field(default_factory=list)
    household_links: list[DailyHelpHouseholdLinkResponse] = Field(default_factory=list)
    availability_slots: list[DailyHelpAvailabilitySlotResponse] = Field(default_factory=list)
    rating_summary: DailyHelpRatingSummaryResponse | None = None
    created_by_user_id: str | None = None
    created_by_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


class ResidentDailyHelpListItemResponse(BaseModel):
    """Resident directory card — phone may be masked by service layer."""

    model_config = ConfigDict(extra="ignore")

    id: str
    display_name: str
    category_id: str
    category_name: str | None = None
    photo_path: str | None = None
    phone: str | None = None
    phone_masked: bool = False
    gate_passcode: str | None = None
    household_link_count: int = 0
    open_to_work: bool = False
    average_stars: float | None = None
    is_inside: bool = False
    is_newly_added: bool = False
    has_household_link: bool = False


class ResidentDailyHelpProfilePreviewResponse(BaseModel):
    """Compact profile row for category home avatar strip (max 4 per category)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    display_name: str
    photo_path: str | None = None
    initials: str | None = None
    phone: str | None = None
    open_to_work: bool = False
    household_link_count: int = 0
    average_stars: float | None = None


class ResidentDailyHelpHouseholdLinkItemResponse(BaseModel):
    """Daily help profile linked to the resident's unit."""

    model_config = ConfigDict(extra="ignore")

    link_id: str
    started_at: str | None = None
    profile_id: str
    display_name: str
    photo_path: str | None = None
    initials: str | None = None
    phone: str | None = None
    gate_passcode: str | None = None
    open_to_work: bool = False
    average_stars: float | None = None
    is_inside: bool = False


class ResidentDailyHelpHouseholdLinksCategoryResponse(BaseModel):
    """Household-linked daily help profiles grouped by category."""

    model_config = ConfigDict(extra="ignore")

    category_id: str
    category_name: str
    linked_count: int = 0
    inside_count: int = 0
    open_to_work_count: int = 0
    linked_profiles: list[ResidentDailyHelpHouseholdLinkItemResponse] = Field(default_factory=list)


class ResidentDailyHelpDetailResponse(BaseModel):
    """Resident profile detail — no admin audit or internal org fields."""

    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: str
    initials: str | None = None
    first_name: str
    middle_name: str | None = None
    last_name: str
    display_name: str
    phone_isd_code: str
    phone_number: str
    phone: str | None = None
    alternate_phone_isd_code: str | None = None
    alternate_phone_number: str | None = None
    category_id: str
    category_name: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    photo_path: str | None = None
    gate_passcode: str
    status: str
    open_to_work: bool = False
    linked_pass_id: str | None = None
    document_count: int = 0
    household_link_count: int = 0
    documents: list[DailyHelpDocumentResponse] = Field(default_factory=list)
    household_links: list[DailyHelpHouseholdLinkResponse] = Field(default_factory=list)
    availability_slots: list[DailyHelpAvailabilitySlotResponse] = Field(default_factory=list)
    rating_summary: DailyHelpRatingSummaryResponse | None = None
    created_at: str | None = None


class ResidentDailyHelpCategoryStatsResponse(BaseModel):
    """Footer stats on resident category home."""

    model_config = ConfigDict(extra="ignore")

    category_id: str
    category_name: str
    inside_count: int = 0
    open_to_work_count: int = 0
    newly_added_count: int = 0
    profile_count: int = 0
    preview_profiles: list[ResidentDailyHelpProfilePreviewResponse] = Field(default_factory=list)


class DailyHelpMessageResponse(BaseModel):
    """Simple mutation payload (deactivate, delete, restore)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    status: str


class DailyHelpOpenToWorkResponse(BaseModel):
    """Result after toggling open_to_work on a profile."""

    model_config = ConfigDict(extra="ignore")

    id: str
    open_to_work: bool


class DailyHelpCategoryListApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/daily-help/categories."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[DailyHelpCategoryResponse]


class DailyHelpCategoryApiResponse(BaseModel):
    """API envelope for category create/update."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpCategoryResponse


class DailyHelpSummaryApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/daily-help/summary."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpSummaryResponse


class DailyHelpListApiResponse(BaseModel):
    """API envelope for admin daily help list."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[DailyHelpListItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class DailyHelpHouseholdLinkListApiResponse(BaseModel):
    """API envelope for GET profile household links (admin)."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[DailyHelpHouseholdLinkResponse]


class ResidentDailyHelpHouseholdLinkListApiResponse(BaseModel):
    """API envelope for GET /daily-help/household-links (resident)."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ResidentDailyHelpHouseholdLinksCategoryResponse]


class ResidentDailyHelpListApiResponse(BaseModel):
    """API envelope for resident daily help directory list/search."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ResidentDailyHelpListItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class DailyHelpDetailApiResponse(BaseModel):
    """API envelope for admin profile detail."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpDetailResponse


class ResidentDailyHelpDetailApiResponse(BaseModel):
    """API envelope for GET /daily-help/{profile_id} (resident)."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ResidentDailyHelpDetailResponse


class CreateDailyHelpApiResponse(BaseModel):
    """API envelope for POST /projects/{project_id}/daily-help."""

    status: str
    message: str
    statusCode: int
    code: str
    data: CreateDailyHelpResponse


class DailyHelpDocumentApiResponse(BaseModel):
    """API envelope for document add."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpDocumentResponse


class DailyHelpMessageApiResponse(BaseModel):
    """API envelope for status mutations without extra payload."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Daily help profile deactivated.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {"id": "profile-uuid", "status": "inactive"},
            }
        }
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpMessageResponse


class DailyHelpOpenToWorkApiResponse(BaseModel):
    """API envelope for PATCH /daily-help/{profile_id}/open-to-work."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpOpenToWorkResponse


class DailyHelpHouseholdLinkApiResponse(BaseModel):
    """API envelope for POST household link."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpHouseholdLinkResponse


class DailyHelpAvailabilityApiResponse(BaseModel):
    """API envelope for availability slot list/replace."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[DailyHelpAvailabilitySlotResponse]


class DailyHelpRatingSummaryApiResponse(BaseModel):
    """API envelope for profile rating summary."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpRatingSummaryResponse


class ResidentDailyHelpCategoryStatsApiResponse(BaseModel):
    """API envelope for resident category footer stats."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ResidentDailyHelpCategoryStatsResponse]


class DailyHelpAttendanceApiResponse(BaseModel):
    """API envelope for profile attendance (admin or resident)."""

    status: str
    message: str
    statusCode: int
    code: str
    data: DailyHelpAttendanceResponse
