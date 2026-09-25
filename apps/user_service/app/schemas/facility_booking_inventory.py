"""Schemas for bookable inventory, closures, staff assignments and project booking settings."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.enums import FacilityBookingInvoiceFrequency
from apps.user_service.app.schemas.facility_booking_config import (
    MINUTES_PER_DAY,
    DayHours,
    validate_week_hours,
)


class _Request(BaseModel):
    """Base request model that forbids unknown fields."""

    model_config = ConfigDict(extra="forbid")


def _validate_minutes(from_min: int, to_min: int) -> None:
    """Raise when to_min is not after from_min."""
    if to_min <= from_min:
        raise ValueError("to_min must be after from_min")


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


class CreateBookingUnitRequest(_Request):
    """Create booking unit request."""

    name: str = Field(..., min_length=1, max_length=120)
    tower_id: str | None = None
    floor_id: str | None = None
    room_type: str | None = Field(None, max_length=80)
    features: list[str] = Field(default_factory=list, max_length=30)
    sort_order: int = Field(0, ge=0)
    active: bool = True


class UpdateBookingUnitRequest(_Request):
    """Update booking unit request."""

    name: str | None = Field(None, min_length=1, max_length=120)
    tower_id: str | None = None
    floor_id: str | None = None
    room_type: str | None = Field(None, max_length=80)
    features: list[str] | None = Field(None, max_length=30)
    sort_order: int | None = Field(None, ge=0)
    active: bool | None = None


class BookingUnitResponse(BaseModel):
    """Booking unit response."""

    id: str
    facility_id: str
    name: str
    tower_id: str | None = None
    floor_id: str | None = None
    room_type: str | None = None
    features: list[str] = Field(default_factory=list)
    sort_order: int
    active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Schedule periods
# ---------------------------------------------------------------------------


class _ScheduleFields(_Request):
    """Shared schedule period fields and week-hours validator."""

    @field_validator("hours", check_fields=False)
    @classmethod
    def validate_hours(cls, hours: list[DayHours] | None) -> list[DayHours] | None:
        """Validate_hours."""
        return None if hours is None else validate_week_hours(hours)


class CreateSchedulePeriodRequest(_ScheduleFields):
    """Create schedule period request."""

    name: str = Field(..., min_length=1, max_length=80)
    starts_on: date
    ends_on: date
    hours: list[DayHours]

    @model_validator(mode="after")
    def validate_range(self) -> CreateSchedulePeriodRequest:
        """Ensure ends_on is on or after starts_on."""
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self


class UpdateSchedulePeriodRequest(_ScheduleFields):
    """Update schedule period request."""

    name: str | None = Field(None, min_length=1, max_length=80)
    starts_on: date | None = None
    ends_on: date | None = None
    hours: list[DayHours] | None = None


class SchedulePeriodResponse(BaseModel):
    """Schedule period response."""

    id: str
    facility_id: str
    name: str
    starts_on: date
    ends_on: date
    hours: list[DayHours]
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Slot blocks
# ---------------------------------------------------------------------------


class CreateSlotBlockRequest(_Request):
    """Create slot block request."""

    unit_id: str | None = None
    starts_on: date
    ends_on: date | None = None
    from_min: int = Field(0, ge=0, le=MINUTES_PER_DAY)
    to_min: int = Field(MINUTES_PER_DAY, ge=0, le=MINUTES_PER_DAY)
    reason: str = Field(..., min_length=1, max_length=200)
    category: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def validate_window(self) -> CreateSlotBlockRequest:
        """Validate minute window and optional end date."""
        _validate_minutes(self.from_min, self.to_min)
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self


class SlotBlockResponse(BaseModel):
    """Slot block response."""

    id: str
    facility_id: str
    unit_id: str | None = None
    starts_on: date
    ends_on: date | None = None
    from_min: int
    to_min: int
    reason: str
    category: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Closures & maintenance
# ---------------------------------------------------------------------------


class CreateClosureRequest(_Request):
    """Create closure request."""

    closed_on: date
    reason: str = Field(..., min_length=1, max_length=200)


class ClosureResponse(BaseModel):
    """Closure response."""

    id: str
    facility_id: str
    closed_on: date
    reason: str
    created_at: datetime | None = None


class CreateMaintenanceWindowRequest(_Request):
    """Create maintenance window request."""

    on_date: date
    from_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    to_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    note: str = Field(..., min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_window(self) -> CreateMaintenanceWindowRequest:
        """Validate maintenance window minute range."""
        _validate_minutes(self.from_min, self.to_min)
        return self


class MaintenanceWindowResponse(BaseModel):
    """Maintenance window response."""

    id: str
    facility_id: str
    on_date: date
    from_min: int
    to_min: int
    note: str
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Staff assignments
# ---------------------------------------------------------------------------


class UpsertStaffAssignmentRequest(_Request):
    """Upsert staff assignment request."""

    project_member_id: str
    facility_ids: list[str] = Field(default_factory=list, max_length=200)


class StaffAssignmentResponse(BaseModel):
    """Staff assignment response."""

    id: str
    project_member_id: str
    user_id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role_slug: str | None = None
    role_name: str | None = None
    member_status: str | None = None
    facility_ids: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Project booking settings
# ---------------------------------------------------------------------------


class UpdateProjectBookingSettingsRequest(_Request):
    """Update project booking settings request."""

    timezone: str = Field(..., min_length=1, max_length=64)
    currency_code: str = Field("INR", min_length=3, max_length=3)
    invoice_frequency: FacilityBookingInvoiceFrequency | None = None
    wallet_enabled: bool | None = None
    online_enabled: bool | None = None
    cash_enabled: bool | None = None
    wallet_credit_limit: int | None = Field(None, ge=0)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Validate_timezone."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("currency_code")
    @classmethod
    def upper_currency(cls, value: str) -> str:
        """Upper_currency."""
        return value.upper()


class ProjectBookingSettingsResponse(BaseModel):
    """Project booking settings response."""

    project_id: str
    timezone: str
    currency_code: str
    invoice_frequency: FacilityBookingInvoiceFrequency = FacilityBookingInvoiceFrequency.MONTHLY
    wallet_enabled: bool = True
    online_enabled: bool = True
    cash_enabled: bool = True
    wallet_credit_limit: int = 10000
    updated_at: datetime | None = None
