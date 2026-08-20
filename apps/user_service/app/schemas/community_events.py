"""Community events schemas."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import (
    COMMUNITY_EVENT_MAX_DESCRIPTION_LENGTH,
    COMMUNITY_EVENT_MAX_GALLERY,
    COMMUNITY_EVENT_MAX_MEDIA_BYTES,
    COMMUNITY_EVENT_MAX_TITLE_LENGTH,
    CommunityEventCategory,
    CommunityEventChildTicketMode,
    CommunityEventListTab,
    CommunityEventPublishMode,
    CommunityEventType,
    ResidentEventTimeframe,
)


class CommunityEventMediaInput(BaseModel):
    """Gallery image metadata on create or update."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    size_bytes: int = Field(..., ge=1, le=COMMUNITY_EVENT_MAX_MEDIA_BYTES)
    sort_order: int = Field(..., ge=0, le=COMMUNITY_EVENT_MAX_GALLERY - 1)


class CommunityEventMediaResponse(BaseModel):
    """Gallery row in event detail."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    file_path: str
    file_name: str | None = None
    mime_type: str
    size_bytes: int
    sort_order: int


class CreateCommunityEventRequest(BaseModel):
    """Create a community event (draft or publish)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=COMMUNITY_EVENT_MAX_TITLE_LENGTH)
    description: str = Field("", max_length=COMMUNITY_EVENT_MAX_DESCRIPTION_LENGTH)
    category: CommunityEventCategory = CommunityEventCategory.SOCIAL
    is_multi_day: bool = False
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    event_type: CommunityEventType = CommunityEventType.FREE
    total_capacity: int | None = Field(None, ge=1)
    max_tickets_per_resident: int = Field(4, ge=1, le=50)
    booking_closes_at: datetime | None = None
    adult_price_minor: int = Field(0, ge=0)
    child_ticket_mode: CommunityEventChildTicketMode = CommunityEventChildTicketMode.NOT_APPLICABLE
    child_price_minor: int = Field(0, ge=0)
    apply_tax: bool = False
    tax_rate: float = Field(18.0, ge=0, le=100)
    facility_id: str | None = None
    cover_image_path: str | None = Field(None, max_length=2000)
    gallery: list[CommunityEventMediaInput] | None = None
    publish_mode: CommunityEventPublishMode = CommunityEventPublishMode.DRAFT

    @field_validator("gallery")
    @classmethod
    def validate_gallery_count(
        cls,
        gallery: list[CommunityEventMediaInput] | None,
    ) -> list[CommunityEventMediaInput] | None:
        """Enforce max gallery count."""
        if gallery is not None and len(gallery) > COMMUNITY_EVENT_MAX_GALLERY:
            raise ValueError(f"gallery cannot exceed {COMMUNITY_EVENT_MAX_GALLERY}")
        return gallery


class UpdateCommunityEventRequest(BaseModel):
    """Update a draft event or safe fields on published."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=COMMUNITY_EVENT_MAX_TITLE_LENGTH)
    description: str | None = Field(None, max_length=COMMUNITY_EVENT_MAX_DESCRIPTION_LENGTH)
    category: CommunityEventCategory | None = None
    is_multi_day: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    event_type: CommunityEventType | None = None
    total_capacity: int | None = Field(None, ge=1)
    max_tickets_per_resident: int | None = Field(None, ge=1, le=50)
    booking_closes_at: datetime | None = None
    adult_price_minor: int | None = Field(None, ge=0)
    child_ticket_mode: CommunityEventChildTicketMode | None = None
    child_price_minor: int | None = Field(None, ge=0)
    apply_tax: bool | None = None
    tax_rate: float | None = Field(None, ge=0, le=100)
    facility_id: str | None = None
    cover_image_path: str | None = Field(None, max_length=2000)
    gallery: list[CommunityEventMediaInput] | None = None

    @field_validator("gallery")
    @classmethod
    def validate_gallery_count(
        cls,
        gallery: list[CommunityEventMediaInput] | None,
    ) -> list[CommunityEventMediaInput] | None:
        """Enforce max gallery count."""
        if gallery is not None and len(gallery) > COMMUNITY_EVENT_MAX_GALLERY:
            raise ValueError(f"gallery cannot exceed {COMMUNITY_EVENT_MAX_GALLERY}")
        return gallery


class CancelCommunityEventRequest(BaseModel):
    """Cancel a published event."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=500)


class CommunityEventListQuery(BaseModel):
    """Admin list filters."""

    model_config = ConfigDict(extra="forbid")

    tab: CommunityEventListTab = CommunityEventListTab.ALL
    search: str | None = Field(None, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class CommunityEventExportQuery(BaseModel):
    """Export filters (same as list tab)."""

    model_config = ConfigDict(extra="forbid")

    tab: CommunityEventListTab = CommunityEventListTab.ALL
    search: str | None = Field(None, max_length=200)


class CommunityEventBookingListQuery(BaseModel):
    """Admin bookings list filters."""

    model_config = ConfigDict(extra="forbid")

    booking_status: str | None = None
    payment_status: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class MarkBookingPaidRequest(BaseModel):
    """Admin mark booking paid."""

    model_config = ConfigDict(extra="forbid")

    payment_notes: str | None = Field(None, max_length=500)


class MarkBookingWaivedRequest(BaseModel):
    """Admin waive booking payment."""

    model_config = ConfigDict(extra="forbid")

    payment_notes: str | None = Field(None, max_length=500)


class CommunityEventSummaryResponse(BaseModel):
    """Admin dashboard summary."""

    total_events: int
    upcoming: int
    total_rsvps: int
    revenue_collected_minor: int
    revenue_currency: str = "INR"
    tabs: dict[str, int]


class CommunityEventListItemResponse(BaseModel):
    """Admin list row."""

    id: str
    display_code: str
    title: str
    category: str
    category_label: str
    start_date: date
    end_date: date
    is_multi_day: bool
    event_type: str
    facility_name: str | None = None
    facility_location_label: str | None = None
    bookings_count: int
    tickets_booked: int
    total_capacity: int | None = None
    ticket_breakdown_adult: int = 0
    ticket_breakdown_child: int = 0
    paid_bookings_count: int
    revenue_collected_minor: int
    publish_status: str
    record_status: str
    booking_state: str


class CommunityEventDetailResponse(BaseModel):
    """Admin event detail."""

    id: str
    display_code: str
    title: str
    description: str
    category: str
    category_label: str
    publish_status: str
    record_status: str
    facility_id: str | None = None
    facility_name: str | None = None
    facility_location_label: str | None = None
    is_multi_day: bool
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    event_type: str
    total_capacity: int | None = None
    max_tickets_per_resident: int
    booking_closes_at: datetime | None = None
    adult_price_minor: int
    child_ticket_mode: str
    child_price_minor: int
    apply_tax: bool
    tax_rate: float
    currency: str
    cover_image_path: str | None = None
    gallery: list[CommunityEventMediaResponse] = Field(default_factory=list)
    tickets_booked: int
    bookings_count: int
    paid_bookings_count: int
    revenue_collected_minor: int
    published_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class CommunityEventBookingListItemResponse(BaseModel):
    """Admin booking row."""

    id: str
    display_code: str
    contact_id: str
    contact_name: str | None = None
    unit_id: str
    unit_code: str | None = None
    adult_tickets: int
    child_tickets: int
    total_tickets: int
    total_amount_minor: int
    currency: str
    booking_status: str
    payment_status: str
    paid_at: datetime | None = None
    booked_at: datetime


class ResidentEventListQuery(BaseModel):
    """Resident events list."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    unit_id: str
    timeframe: ResidentEventTimeframe = ResidentEventTimeframe.UPCOMING
    category: CommunityEventCategory | None = None
    search: str | None = Field(None, max_length=200)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=50)


class CreateEventBookingRequest(BaseModel):
    """Resident book tickets."""

    model_config = ConfigDict(extra="forbid")

    adult_tickets: int = Field(..., ge=0, le=50)
    child_tickets: int = Field(0, ge=0, le=50)

    @field_validator("child_tickets")
    @classmethod
    def at_least_one_ticket(cls, child_tickets: int, info) -> int:
        """Require at least one ticket total."""
        adult = info.data.get("adult_tickets", 0)
        if adult + child_tickets < 1:
            raise ValueError("at least one ticket required")
        return child_tickets


class ResidentEventListItemResponse(BaseModel):
    """Resident event card."""

    id: str
    title: str
    category: str
    category_label: str
    price_label: str
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    is_multi_day: bool
    facility_name: str | None = None
    location_label: str | None = None
    tickets_booked: int
    total_capacity: int | None = None
    booking_state: str
    my_tickets_count: int = 0
    cta: str


class ResidentEventDetailResponse(BaseModel):
    """Resident event detail."""

    id: str
    title: str
    description: str
    category: str
    category_label: str
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    is_multi_day: bool
    facility_name: str | None = None
    location_label: str | None = None
    facility_type: str | None = None
    facility_subtype: str | None = None
    location_notes: str | None = None
    event_type: str
    adult_price_minor: int
    child_ticket_mode: str
    child_price_minor: int
    apply_tax: bool
    tax_rate: float
    currency: str
    max_tickets_per_resident: int
    booking_closes_at: datetime | None = None
    booking_state: str
    tickets_booked: int
    total_capacity: int | None = None
    cover_image_path: str | None = None
    gallery: list[CommunityEventMediaResponse] = Field(default_factory=list)
    my_tickets_count: int = 0
    my_booking_id: str | None = None
    price_label: str
    cta: str


class BookEventResponse(BaseModel):
    """Booking confirmation."""

    booking_id: str
    display_code: str
    adult_tickets: int
    child_tickets: int
    total_tickets: int
    subtotal_minor: int
    tax_minor: int
    total_amount_minor: int
    currency: str
    payment_status: str
    booking_status: str
    gate_qr_token: str | None = None
    payment_instruction: str | None = None


class MyBookingsSummaryResponse(BaseModel):
    """Badge count for resident."""

    active_ticket_count: int
    active_booking_count: int


class MyBookingItemResponse(BaseModel):
    """Resident my bookings row."""

    booking_id: str
    display_code: str
    event_id: str
    event_title: str
    event_start_date: date
    total_tickets: int
    total_amount_minor: int
    payment_status: str
    booking_status: str
    gate_qr_token: str | None = None


class VerifyBookingRequest(BaseModel):
    """Gate QR scan request."""

    model_config = ConfigDict(extra="forbid")

    gate_qr_token: str = Field(..., min_length=1, max_length=100)


class VerifyBookingResponse(BaseModel):
    """Gate verification result."""

    booking_id: str
    display_code: str
    event_title: str
    event_start_date: date
    contact_name: str | None = None
    unit_code: str | None = None
    adult_tickets: int
    child_tickets: int
    total_tickets: int
    payment_status: str
    booking_status: str


class CommunityEventExportFormat(str, Enum):
    """Export format."""

    CSV = "csv"
