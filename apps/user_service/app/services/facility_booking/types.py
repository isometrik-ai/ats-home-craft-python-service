"""Value objects consumed by the pure booking engines.

Dates are project-local calendar dates; minutes are offsets from local midnight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from apps.user_service.app.schemas.facility_booking_config import (
    DayHours,
    FacilitySetup,
    Policies,
    PricingConfig,
)


@dataclass(frozen=True, slots=True)
class BookingUnit:
    """One bookable unit (court, tee sheet row, room, etc.)."""

    id: str
    name: str
    room_type: str | None = None
    features: tuple[str, ...] = ()
    tower_id: str | None = None
    floor_id: str | None = None
    sort_order: int = 0
    active: bool = True


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    """Scheduled maintenance blocking part of a day."""

    id: str
    on_date: date
    from_min: int
    to_min: int
    note: str


@dataclass(frozen=True, slots=True)
class SchedulePeriod:
    """Seasonal override of weekly operating hours."""

    id: str
    name: str
    starts_on: date
    ends_on: date
    hours: list[DayHours]


@dataclass(frozen=True, slots=True)
class SlotBlock:
    """Management block on a unit or the whole facility."""

    id: str
    starts_on: date
    from_min: int
    to_min: int
    reason: str
    ends_on: date | None = None
    unit_id: str | None = None
    category: str | None = None


@dataclass(slots=True)
class FacilitySnapshot:
    """Everything the engines need to know about one bookable facility."""

    id: str
    name: str
    archetype: str
    slot_minutes: int
    hours: list[DayHours]
    pricing: PricingConfig
    policies: Policies
    setup: FacilitySetup
    units: list[BookingUnit] = field(default_factory=list)
    closures: dict[date, str] = field(default_factory=dict)
    maintenance: list[MaintenanceWindow] = field(default_factory=list)
    schedules: list[SchedulePeriod] = field(default_factory=list)
    slot_blocks: list[SlotBlock] = field(default_factory=list)
    description: str = ""
    accepting_bookings: bool = True


@dataclass(frozen=True, slots=True)
class Participant:
    """Guest or co-resident attached to a reservation."""

    kind: str
    name: str
    contact_id: str | None = None


@dataclass(slots=True)
class EngineReservation:
    """Reservation record passed into availability and pricing engines."""

    id: str
    facility_id: str
    unit_id: str | None
    local_date: date
    end_local_date: date
    start_min: int
    end_min: int
    host_contact_id: str
    status: str
    participants: list[Participant] = field(default_factory=list)
    quote_total: int = 0
    host_name: str = ""


@dataclass(slots=True)
class BookingDraft:
    """Proposed booking before persistence."""

    facility_id: str
    unit_id: str | None
    local_date: date
    end_local_date: date
    start_min: int
    end_min: int
    host_contact_id: str
    participants: list[Participant] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Single validation failure with a stable machine code."""

    code: str
    message: str


@dataclass(slots=True)
class Validation:
    """Aggregate result of draft or reschedule validation."""

    ok: bool
    errors: list[ValidationIssue]

    @property
    def codes(self) -> list[str]:
        """Stable error codes in encounter order."""
        return [issue.code for issue in self.errors]
