"""Facility booking configuration schemas (rules stored as JSONB on the config row).

Amounts are whole currency units (e.g. rupees), matching the pricing engine.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.enums import (
    FACILITY_BOOKING_POLICY_DOCUMENT_MAX_BYTES,
    FacilityBookingArchetype,
    FacilityMembershipMode,
    FacilityPriceMode,
    FacilityRateUnit,
)

MINUTES_PER_DAY = 24 * 60


class _ConfigModel(BaseModel):
    """Base config model that forbids unknown fields."""

    model_config = ConfigDict(extra="forbid")


class DayHours(_ConfigModel):
    """Opening hours for one weekday (minutes from local midnight)."""

    open: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    close: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    closed: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> DayHours:
        """Ensure close is after open when the day is not marked closed."""
        if not self.closed and self.close <= self.open:
            raise ValueError("close must be after open")
        return self


def validate_week_hours(hours: list[DayHours]) -> list[DayHours]:
    """Require exactly seven day entries (Sunday first)."""
    if len(hours) != 7:
        raise ValueError("hours must contain 7 entries (Sunday first)")
    return hours


class TimeBand(_ConfigModel):
    """Price multiplier applied when a booking starts inside the band."""

    id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=80)
    from_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    to_min: int = Field(..., ge=0, le=MINUTES_PER_DAY)
    multiplier: float = Field(..., gt=0, le=10)
    weekdays: list[int] | None = None

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, weekdays: list[int] | None) -> list[int] | None:
        """Validate_weekdays."""
        if weekdays is not None and any(d < 0 or d > 6 for d in weekdays):
            raise ValueError("weekdays must be between 0 (Sunday) and 6 (Saturday)")
        return weekdays

    @model_validator(mode="after")
    def validate_range(self) -> TimeBand:
        """Ensure to_min is after from_min."""
        if self.to_min <= self.from_min:
            raise ValueError("to_min must be after from_min")
        return self


class NightRate(_ConfigModel):
    """Room nightly rate tier (optionally per room type)."""

    room_type: str | None = Field(None, max_length=80)
    min_nights: int = Field(..., ge=1, le=365)
    rate: int = Field(..., ge=0)


class RoomSeason(_ConfigModel):
    """Seasonal multiplier on room rates."""

    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    starts_on: date
    ends_on: date
    multiplier: float = Field(..., gt=0, le=10)

    @model_validator(mode="after")
    def validate_range(self) -> RoomSeason:
        """Ensure ends_on is on or after starts_on."""
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self


class PolicyDocument(_ConfigModel):
    """Uploaded policies PDF (stored in object storage)."""

    name: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., ge=1, le=FACILITY_BOOKING_POLICY_DOCUMENT_MAX_BYTES)
    file_path: str = Field(..., min_length=1, max_length=2000)
    uploaded_at: datetime | None = None


class RoomSetup(_ConfigModel):
    """Room setup."""

    min_nights: int = Field(1, ge=1, le=365)
    night_rates: list[NightRate] = Field(default_factory=list)
    seasons: list[RoomSeason] = Field(default_factory=list)
    amenities_included: list[str] = Field(default_factory=list)
    amenities_excluded: list[str] = Field(default_factory=list)
    room_types: list[str] = Field(default_factory=list)
    check_in_time: int = Field(14 * 60, ge=0, le=MINUTES_PER_DAY)
    check_out_time: int = Field(11 * 60, ge=0, le=MINUTES_PER_DAY)
    terms: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    block_categories: list[str] = Field(default_factory=list)


class SlotSetup(_ConfigModel):
    """Slot setup."""

    durations: list[int] = Field(default_factory=lambda: [60])
    max_players: int = Field(4, ge=1, le=100)
    lead_time_minutes: int = Field(15, ge=0, le=MINUTES_PER_DAY)

    @field_validator("durations")
    @classmethod
    def validate_durations(cls, durations: list[int]) -> list[int]:
        """Validate_durations."""
        if not durations or any(d < 5 or d > MINUTES_PER_DAY for d in durations):
            raise ValueError("durations must be between 5 and 1440 minutes")
        return durations


class GolfSetup(_ConfigModel):
    """Golf setup."""

    holes: int = Field(9, ge=1, le=36)
    starting_tees: list[int] = Field(default_factory=lambda: [1])
    min_players: int = Field(1, ge=1, le=8)
    max_players: int = Field(4, ge=1, le=8)
    allow_singles: bool = True
    starter_check_in_minutes: int = Field(20, ge=0, le=240)
    pace_minutes: int = Field(135, ge=0, le=720)

    @model_validator(mode="after")
    def validate_players(self) -> GolfSetup:
        """Ensure max_players is at least min_players."""
        if self.max_players < self.min_players:
            raise ValueError("max_players must be at least min_players")
        return self


class EventSetup(_ConfigModel):
    """Event setup."""

    allows_hourly: bool = True
    allows_full_day: bool = False
    allows_multi_day: bool = False
    max_participants: int = Field(22, ge=1, le=10_000)


class FacilitySetup(_ConfigModel):
    """Archetype-specific setup and pricing toggles."""

    price_mode: FacilityPriceMode = FacilityPriceMode.FIXED
    membership_enabled: bool | None = None
    membership_mode: FacilityMembershipMode | None = None
    fixed_enabled: bool | None = None
    rule_based_enabled: bool | None = None
    slot: SlotSetup | None = None
    golf: GolfSetup | None = None
    event: EventSetup | None = None
    room: RoomSetup | None = None


class PricingConfig(_ConfigModel):
    """Pricing config."""

    tax_percent: int = Field(0, ge=0, le=100)
    resident_rate: int = Field(0, ge=0)
    hourly_rate: int | None = Field(None, ge=0)
    guest_rate: int = Field(0, ge=0)
    guest_flat_fee: int = Field(0, ge=0)
    unit_label: FacilityRateUnit = FacilityRateUnit.HOUR
    bands: list[TimeBand] = Field(default_factory=list)
    weekend_multiplier: float = Field(1, gt=0, le=10)
    holiday_multiplier: float = Field(1, gt=0, le=10)
    holidays: list[date] = Field(default_factory=list)
    deposit: int = Field(0, ge=0)
    cleaning_fee: int = Field(0, ge=0)
    min_billable_hours: int = Field(0, ge=0, le=24)


class CancellationTier(_ConfigModel):
    """Cancellation tier."""

    label: str = Field(..., min_length=1, max_length=80)
    hours_before: int = Field(..., ge=0, le=24 * 365)
    fee_percent: int = Field(..., ge=0, le=100)


class Policies(_ConfigModel):
    """Policies."""

    requires_approval: bool = False
    advance_booking_days: int = Field(14, ge=0, le=365)
    cancellation_tiers: list[CancellationTier] = Field(
        default_factory=lambda: [
            CancellationTier(label="Free cancellation", hours_before=24, fee_percent=0),
            CancellationTier(label="Late cancellation", hours_before=0, fee_percent=100),
        ]
    )
    reschedule_cutoff_hours: int = Field(24, ge=0, le=24 * 365)
    buffer_minutes: int = Field(0, ge=0, le=240)
    weekly_cap: int = Field(0, ge=0, le=100)
    no_show_fee_percent: int = Field(100, ge=0, le=100)
    min_duration_min: int = Field(60, ge=0, le=MINUTES_PER_DAY)
    max_duration_min: int = Field(240, ge=0, le=MINUTES_PER_DAY)

    @field_validator("cancellation_tiers")
    @classmethod
    def validate_tiers(cls, tiers: list[CancellationTier]) -> list[CancellationTier]:
        """Validate_tiers."""
        if not tiers:
            raise ValueError("at least one cancellation tier is required")
        return tiers

    @model_validator(mode="after")
    def validate_duration(self) -> Policies:
        """Ensure max_duration_min is at least min_duration_min."""
        if self.max_duration_min < self.min_duration_min:
            raise ValueError("max_duration_min must be at least min_duration_min")
        return self


class UpdateFacilityBookingConfigRequest(_ConfigModel):
    """Replace one or more config sections; ``version`` guards concurrent edits."""

    version: int = Field(..., ge=1)
    description: str | None = Field(None, max_length=2000)
    slot_minutes: int | None = Field(None, ge=5, le=MINUTES_PER_DAY)
    accepting_bookings: bool | None = None
    default_hours: list[DayHours] | None = None
    pricing: PricingConfig | None = None
    policies: Policies | None = None
    setup: FacilitySetup | None = None
    policies_document: PolicyDocument | None = None
    clear_policies_document: bool = False

    @field_validator("default_hours")
    @classmethod
    def validate_hours(cls, hours: list[DayHours] | None) -> list[DayHours] | None:
        """Validate_hours."""
        return None if hours is None else validate_week_hours(hours)


class FacilityBookingConfigResponse(BaseModel):
    """Booking configuration for one facility."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    facility_id: str
    facility_name: str
    facility_type: str
    archetype: FacilityBookingArchetype
    description: str
    slot_minutes: int
    accepting_bookings: bool
    default_hours: list[DayHours]
    pricing: PricingConfig
    policies: Policies
    setup: FacilitySetup
    policies_document: PolicyDocument | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
