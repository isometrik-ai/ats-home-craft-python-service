"""Availability engine: day/month views, draft validation and reschedule rules.

Pure functions over ``FacilitySnapshot`` + active reservations; ``now`` is a naive
project-local datetime. Behavior mirrors the Clubhouse reference engine (golden tests).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from apps.user_service.app.schemas.enums import (
    ACTIVE_RESERVATION_STATUSES,
    WEEKLY_CAP_COUNTABLE_STATUSES,
    FacilityBookingArchetype,
    FacilityReservationStatus,
)
from apps.user_service.app.schemas.facility_booking import (
    BookingUnitSummary,
    BusyInterval,
    DayAvailability,
    DaySummary,
    MaintenanceWindowView,
    MonthDayView,
    NextAvailabilityResponse,
    RoomAvailabilityItem,
    SlotView,
    UnitDayView,
)
from apps.user_service.app.schemas.facility_booking_config import DayHours
from apps.user_service.app.services.facility_booking.money import (
    each_date,
    fmt_clock,
    js_dow,
    local_start,
    ts_round,
)
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    BookingUnit,
    EngineReservation,
    FacilitySnapshot,
    MaintenanceWindow,
    SchedulePeriod,
    SlotBlock,
    Validation,
    ValidationIssue,
)

SLOT = FacilityBookingArchetype.SLOT.value
TEE_TIME = FacilityBookingArchetype.TEE_TIME.value
DURATION = FacilityBookingArchetype.DURATION.value
DAY_RANGE = FacilityBookingArchetype.DAY_RANGE.value
ROOM = FacilityBookingArchetype.ROOM.value

TEE_CAPACITY = 4
DEFAULT_SLOT_LEAD_MINUTES = 15
MINUTES_PER_DAY = 24 * 60
NEXT_SLOT_LOOKAHEAD_DAYS = 14
NEXT_DAY_LOOKAHEAD_DAYS = 60

# Validation codes that staff may override when booking or rescheduling for a resident.
STAFF_OVERRIDABLE_CODES: frozenset[str] = frozenset(
    {"outside_advance_window", "weekly_cap_reached"}
)


@dataclass(slots=True)
class AvailabilityContext:
    """Inputs shared by availability queries and draft validation."""

    facility: FacilitySnapshot
    active: list[EngineReservation]
    now: datetime
    host_name_of: Callable[[EngineReservation], str] = field(
        default=lambda reservation: reservation.host_name
    )
    # Reservations counted towards weekly caps (defaults to ``active``).
    weekly_pool: list[EngineReservation] | None = None


def active_for_facility(
    reservations: Iterable[EngineReservation], facility_id: str
) -> list[EngineReservation]:
    """Return active reservations for ``facility_id``."""
    return [
        reservation
        for reservation in reservations
        if reservation.facility_id == facility_id
        and reservation.status in ACTIVE_RESERVATION_STATUSES
    ]


def active_schedule(facility: FacilitySnapshot, day: date) -> SchedulePeriod | None:
    """Return the schedule period covering ``day``, if any."""
    for period in facility.schedules:
        if period.starts_on <= day <= period.ends_on:
            return period
    return None


def hours_for(facility: FacilitySnapshot, day: date) -> DayHours:
    """Operating hours for ``day``, honoring seasonal schedules when configured."""
    dow = js_dow(day)
    if facility.schedules:
        period = active_schedule(facility, day)
        if period is None:
            return DayHours(open=0, close=0, closed=True)
        return period.hours[dow]
    return facility.hours[dow]


def maintenance_for(facility: FacilitySnapshot, day: date) -> list[MaintenanceWindow]:
    """Maintenance windows on ``day``."""
    return [window for window in facility.maintenance if window.on_date == day]


def is_blackout(facility: FacilitySnapshot, day: date) -> bool:
    """Whether ``day`` is a facility closure/blackout."""
    return day in facility.closures


def slot_blocks_for(facility: FacilitySnapshot, day: date) -> list[SlotBlock]:
    """Management slot blocks covering ``day``."""
    return [
        block
        for block in facility.slot_blocks
        if block.starts_on <= day <= (block.ends_on or block.starts_on)
    ]


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True when half-open minute intervals overlap."""
    return a_start < b_end and b_start < a_end


def _block_covers(block: SlotBlock, unit_id: str | None, start_min: int, end_min: int) -> bool:
    """True when ``block`` covers ``[start_min, end_min)`` for ``unit_id``."""
    return (block.unit_id is None or block.unit_id == unit_id) and _overlaps(
        block.from_min, block.to_min, start_min, end_min
    )


def within_advance_window(facility: FacilitySnapshot, day: date, now: datetime) -> bool:
    """True when ``day`` is within the facility's advance-booking window from ``now``."""
    limit = (now + timedelta(days=facility.policies.advance_booking_days)).date()
    return now.date() <= day <= limit


def _raw_intervals(reservation: EngineReservation, day: date) -> list[tuple[int, int]]:
    """Minute intervals a reservation occupies on ``day``."""
    if reservation.local_date == day and reservation.end_local_date == day:
        return [(reservation.start_min, reservation.end_min)]
    if (
        reservation.local_date != reservation.end_local_date
        and reservation.local_date <= day <= reservation.end_local_date
    ):
        return [(reservation.start_min, reservation.end_min)]
    return []


def _busy_intervals(
    facility: FacilitySnapshot,
    active: Iterable[EngineReservation],
    day: date,
    host_name_of: Callable[[EngineReservation], str],
) -> list[tuple[int, int, str]]:
    """Buffered busy intervals on ``day`` with host labels."""
    buffer = facility.policies.buffer_minutes
    out: list[tuple[int, int, str]] = []
    for reservation in active:
        for start_min, end_min in _raw_intervals(reservation, day):
            out.append(
                (
                    max(0, start_min - buffer),
                    min(MINUTES_PER_DAY, end_min + buffer),
                    host_name_of(reservation),
                )
            )
    return out


def _tee_capacity(facility: FacilitySnapshot) -> int:
    """Maximum players per tee slot."""
    return facility.setup.golf.max_players if facility.setup.golf is not None else TEE_CAPACITY


def _slot_lead_minutes(facility: FacilitySnapshot) -> int:
    """Minimum minutes before a slot opens for booking."""
    return (
        facility.setup.slot.lead_time_minutes
        if facility.setup.slot is not None
        else DEFAULT_SLOT_LEAD_MINUTES
    )


def _players_in(reservation: EngineReservation) -> int:
    """Headcount including the host."""
    return len(reservation.participants) + 1


def unit_summary(unit: BookingUnit) -> BookingUnitSummary:
    """API summary for one booking unit."""
    return BookingUnitSummary(id=unit.id, name=unit.name, room_type=unit.room_type)


def _slot_view(
    facility: FacilitySnapshot,
    unit: BookingUnit,
    unit_active: list[EngineReservation],
    unit_busy: list[tuple[int, int, str]],
    day: date,
    start_min: int,
    ctx_flags: tuple[bool, date, int, DayHours, list[MaintenanceWindow], list[SlotBlock], int],
) -> SlotView:
    """Build one slot tile for a unit/day."""
    is_past_day, today, now_min, day_hours, maintenance, blocks, tee_capacity = ctx_flags
    end_min = start_min + facility.slot_minutes
    state = "available"
    reason: str | None = None
    if is_past_day or (day == today and end_min <= now_min):
        state, reason = "past", "Past"
    elif day_hours.closed:
        state, reason = "blocked", "Closed"
    elif is_blackout(facility, day):
        state, reason = "blocked", "Blackout date"
    elif any(
        _overlaps(start_min, end_min, window.from_min, window.to_min) for window in maintenance
    ):
        state, reason = "blocked", "Maintenance"
    elif any(_block_covers(block, unit.id, start_min, end_min) for block in blocks):
        state, reason = "blocked", "Blocked"
    elif any(
        _overlaps(start_min, end_min, busy_start, busy_end) for busy_start, busy_end, _ in unit_busy
    ):
        state = "booked"

    remaining: int | None = None
    if facility.archetype == TEE_TIME:
        players = sum(
            _players_in(reservation)
            for reservation in unit_active
            if any(
                _overlaps(start_min, end_min, reserve_start, reserve_end)
                for reserve_start, reserve_end in _raw_intervals(reservation, day)
            )
        )
        if state == "available" and players > 0:
            state = "booked"
            reason = "Full" if players >= tee_capacity else None
        remaining = max(0, tee_capacity - players)
    return SlotView(
        start_min=start_min, end_min=end_min, state=state, reason=reason, remaining=remaining
    )


def get_day_availability(ctx: AvailabilityContext, day: date) -> DayAvailability:
    """Full day view with units, slots and busy intervals."""
    facility = ctx.facility
    day_hours = hours_for(facility, day)
    maintenance = maintenance_for(facility, day)
    today = ctx.now.date()
    is_past_day = day < today
    busy = _busy_intervals(facility, ctx.active, day, ctx.host_name_of)
    blocks = slot_blocks_for(facility, day)

    units: list[UnitDayView] = []
    if facility.archetype in (SLOT, TEE_TIME):
        now_min = ctx.now.hour * 60 + ctx.now.minute + _slot_lead_minutes(facility)
        flags = (
            is_past_day,
            today,
            now_min,
            day_hours,
            maintenance,
            blocks,
            _tee_capacity(facility),
        )
        for unit in facility.units:
            unit_active = (
                [reservation for reservation in ctx.active if reservation.unit_id == unit.id]
                if facility.archetype == SLOT
                else list(ctx.active)
            )
            unit_busy = _busy_intervals(facility, unit_active, day, ctx.host_name_of)
            slots: list[SlotView] = []
            start_min = day_hours.open
            while start_min + facility.slot_minutes <= day_hours.close:
                slots.append(
                    _slot_view(facility, unit, unit_active, unit_busy, day, start_min, flags)
                )
                start_min += facility.slot_minutes
            units.append(UnitDayView(unit=unit_summary(unit), slots=slots))

    buffer = facility.policies.buffer_minutes
    return DayAvailability(
        local_date=day,
        open_min=day_hours.open,
        close_min=day_hours.close,
        closed=day_hours.closed,
        blackout=is_blackout(facility, day),
        closure_reason=facility.closures.get(day),
        past=is_past_day,
        maintenance=[
            MaintenanceWindowView(
                id=window.id,
                on_date=window.on_date,
                from_min=window.from_min,
                to_min=window.to_min,
                note=window.note,
            )
            for window in maintenance
        ],
        units=units,
        busy=[
            BusyInterval(start_min=busy_start + buffer, end_min=busy_end - buffer, label=label)
            for busy_start, busy_end, label in busy
        ],
    )


def get_month_overview(
    ctx: AvailabilityContext, month_start: date, days: int = 42
) -> list[MonthDayView]:
    """Calendar overview cells for a month grid."""
    facility = ctx.facility
    today = ctx.now.date()
    out: list[MonthDayView] = []
    for offset in range(days):
        day = month_start + timedelta(days=offset)
        day_hours = hours_for(facility, day)
        state = "free"
        note: str | None = None
        if day < today:
            state = "past"
        elif day_hours.closed:
            state = "closed"
            note = (
                "Outside schedule"
                if (facility.schedules and active_schedule(facility, day) is None)
                else "Closed"
            )
        elif is_blackout(facility, day):
            state = "blackout"
            note = facility.closures.get(day) or "Blackout"
        else:
            maintenance = maintenance_for(facility, day)
            if any(
                window.from_min <= day_hours.open and window.to_min >= day_hours.close
                for window in maintenance
            ):
                state = "maintenance"
                note = ", ".join(window.note for window in maintenance)
        if state == "free":
            day_busy = _busy_intervals(facility, ctx.active, day, ctx.host_name_of)
            if any(
                busy_start <= day_hours.open and busy_end >= day_hours.close
                for busy_start, busy_end, _ in day_busy
            ):
                state = "busy"
                note = "Booked"
        out.append(MonthDayView(local_date=day, state=state, note=note))
    return out


def week_start(day: date) -> date:
    """Monday-aligned start of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def weekly_usage(
    reservations: Iterable[EngineReservation],
    contact_id: str,
    facility_id: str,
    day: date,
) -> int:
    """Count weekly bookings for ``contact_id`` at ``facility_id`` in the week of ``day``."""
    week_start_date = week_start(day)
    week_end_date = week_start_date + timedelta(days=6)
    return sum(
        1
        for reservation in reservations
        if reservation.facility_id == facility_id
        and week_start_date <= reservation.local_date <= week_end_date
        and reservation.status in WEEKLY_CAP_COUNTABLE_STATUSES
        and (
            reservation.host_contact_id == contact_id
            or any(participant.contact_id == contact_id for participant in reservation.participants)
        )
    )


def cap_reached(facility: FacilitySnapshot, usage: int) -> bool:
    """True when ``usage`` meets or exceeds the facility weekly cap."""
    return facility.policies.weekly_cap > 0 and usage >= facility.policies.weekly_cap


def _draft_as_reservation(draft: BookingDraft) -> EngineReservation:
    """Wrap a draft as a synthetic reservation for cap/overlap checks."""
    return EngineReservation(
        id="draft",
        facility_id=draft.facility_id,
        unit_id=draft.unit_id,
        local_date=draft.local_date,
        end_local_date=draft.end_local_date,
        start_min=draft.start_min,
        end_min=draft.end_min,
        host_contact_id=draft.host_contact_id,
        participants=list(draft.participants),
        status=FacilityReservationStatus.PENDING_APPROVAL.value,
    )


class _Issues:
    """Mutable collector for validation issues."""

    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []

    def add(self, code: str, message: str) -> None:
        """Record one validation issue."""
        self.items.append(ValidationIssue(code=code, message=message))

    def result(self) -> Validation:
        """Deduplicated validation result."""
        unique = list(dict.fromkeys(self.items))
        return Validation(ok=not unique, errors=unique)


def _hours_label(day_hours: DayHours) -> str:
    """Human-readable operating-hours range."""
    return f"{fmt_clock(day_hours.open)}–{fmt_clock(day_hours.close)}"


def _validate_common(ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues) -> None:
    """Checks shared by every archetype."""
    facility = ctx.facility
    today = ctx.now.date()
    if draft.local_date < today:
        issues.add("past_date", "Cannot book in the past.")
    if not within_advance_window(facility, draft.local_date, ctx.now):
        issues.add(
            "outside_advance_window",
            f"Bookings open {facility.policies.advance_booking_days} days in advance.",
        )
    if is_blackout(facility, draft.local_date):
        issues.add("blackout_date", "Facility is closed on this date.")
    if draft.end_local_date < draft.local_date:
        issues.add("invalid_date_range", "Invalid date range.")

    pool = ctx.weekly_pool if ctx.weekly_pool is not None else ctx.active
    usage = weekly_usage(
        [*pool, _draft_as_reservation(draft)], draft.host_contact_id, facility.id, draft.local_date
    )
    if cap_reached(facility, usage):
        cap = facility.policies.weekly_cap
        issues.add(
            "weekly_cap_reached",
            f"Weekly limit reached — max {cap} booking(s) per week at {facility.name}.",
        )


def _validate_slot_schedule(
    draft: BookingDraft, issues: _Issues, day_hours: DayHours, slot_minutes: int
) -> None:
    """Time and alignment rules for slot bookings."""
    if day_hours.closed:
        issues.add("closed_day", "Closed on this day.")
    start_min, end_min = draft.start_min, draft.end_min
    if end_min <= start_min:
        issues.add("invalid_time_range", "Invalid time.")
    if start_min < day_hours.open or end_min > day_hours.close:
        issues.add("outside_hours", f"Outside operating hours ({_hours_label(day_hours)}).")
    if (start_min - day_hours.open) % slot_minutes != 0:
        issues.add("misaligned_slot", "Pick a valid slot time.")


def _validate_slot_conflicts(
    facility: FacilitySnapshot,
    draft: BookingDraft,
    issues: _Issues,
    maintenance: list[MaintenanceWindow],
    blocks: list[SlotBlock],
    active: list[EngineReservation],
) -> None:
    """Overlap checks for slot bookings."""
    start_min, end_min = draft.start_min, draft.end_min
    if any(_overlaps(start_min, end_min, window.from_min, window.to_min) for window in maintenance):
        issues.add("maintenance_overlap", "Overlaps maintenance window.")
    if any(_block_covers(block, draft.unit_id, start_min, end_min) for block in blocks):
        issues.add("blocked", "Blocked by management for this time.")
    if not draft.unit_id:
        return
    buffer = facility.policies.buffer_minutes
    clash = any(
        _overlaps(start_min, end_min, reserve_start - buffer, reserve_end + buffer)
        for reservation in active
        if reservation.unit_id == draft.unit_id
        for reserve_start, reserve_end in _raw_intervals(reservation, draft.local_date)
    )
    if clash:
        issues.add("unit_booked", "Court already booked around this time (incl. buffer).")


def _validate_slot_player_limit(
    facility: FacilitySnapshot, draft: BookingDraft, issues: _Issues
) -> None:
    """Enforce per-slot player cap when configured."""
    slot_setup = facility.setup.slot
    if slot_setup is not None and len(draft.participants) + 1 > slot_setup.max_players:
        issues.add("max_players_exceeded", f"Max {slot_setup.max_players} players per slot.")


def _validate_slot(
    ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues, day_hours: DayHours
) -> None:
    """Validation rules for court/slot archetype."""
    facility = ctx.facility
    if not draft.unit_id:
        issues.add("unit_required", "Select a court.")
    _validate_slot_schedule(draft, issues, day_hours, facility.slot_minutes)
    _validate_slot_conflicts(
        facility,
        draft,
        issues,
        maintenance_for(facility, draft.local_date),
        slot_blocks_for(facility, draft.local_date),
        ctx.active,
    )
    _validate_slot_player_limit(facility, draft, issues)


def _validate_tee(
    ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues, day_hours: DayHours
) -> None:
    """Validation rules for tee-time archetype."""
    facility = ctx.facility
    maintenance = maintenance_for(facility, draft.local_date)
    blocks = slot_blocks_for(facility, draft.local_date)
    capacity = _tee_capacity(facility)
    if day_hours.closed:
        issues.add("closed_day", "Closed on this day.")
    start_min, end_min = draft.start_min, draft.end_min
    if (start_min - day_hours.open) % facility.slot_minutes != 0:
        issues.add("misaligned_slot", "Pick a valid tee time.")
    if start_min < day_hours.open or end_min > day_hours.close:
        issues.add("outside_hours", "Outside operating hours.")
    if any(_overlaps(start_min, end_min, window.from_min, window.to_min) for window in maintenance):
        issues.add("maintenance_overlap", "Overlaps maintenance window.")
    if any(_block_covers(block, None, start_min, end_min) for block in blocks):
        issues.add("blocked", "Blocked by management for this time.")
    players = len(draft.participants) + 1
    if players > capacity:
        issues.add("max_players_exceeded", f"Max {capacity} players per tee slot.")
    taken = sum(
        _players_in(reservation)
        for reservation in ctx.active
        if any(
            _overlaps(start_min, end_min, reserve_start, reserve_end)
            for reserve_start, reserve_end in _raw_intervals(reservation, draft.local_date)
        )
    )
    if taken + players > capacity:
        issues.add(
            "tee_capacity_exceeded",
            f"Only {max(capacity - taken, 0)} player slot(s) left at this tee time.",
        )


def _max_event_participants(facility: FacilitySnapshot) -> int | None:
    """Configured attendee cap for event archetypes."""
    return facility.setup.event.max_participants if facility.setup.event is not None else None


def _validate_duration(
    ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues, day_hours: DayHours
) -> None:
    """Validation rules for flexible-duration event archetype."""
    facility = ctx.facility
    maintenance = maintenance_for(facility, draft.local_date)
    blocks = slot_blocks_for(facility, draft.local_date)
    if day_hours.closed:
        issues.add("closed_day", "Closed on this day.")
    start_min, end_min = draft.start_min, draft.end_min
    if end_min <= start_min:
        issues.add("invalid_time_range", "End time must be after start time.")
    if start_min < day_hours.open or end_min > day_hours.close:
        issues.add("outside_hours", f"Outside operating hours ({_hours_label(day_hours)}).")
    mins = end_min - start_min
    if mins < facility.policies.min_duration_min:
        issues.add(
            "below_min_duration",
            f"Minimum booking is {fmt_clock(facility.policies.min_duration_min)}.",
        )
    if mins > facility.policies.max_duration_min:
        issues.add(
            "above_max_duration",
            f"Maximum booking is {fmt_clock(facility.policies.max_duration_min)}.",
        )
    if any(_overlaps(start_min, end_min, window.from_min, window.to_min) for window in maintenance):
        issues.add("maintenance_overlap", "Overlaps maintenance window.")
    if any(_block_covers(block, None, start_min, end_min) for block in blocks):
        issues.add("blocked", "Blocked by management for this time.")
    busy = _busy_intervals(facility, ctx.active, draft.local_date, ctx.host_name_of)
    if any(_overlaps(start_min, end_min, busy_start, busy_end) for busy_start, busy_end, _ in busy):
        issues.add("facility_busy", f"{facility.name} is busy around this time (incl. buffer).")
    max_people = _max_event_participants(facility)
    if max_people is not None and len(draft.participants) + 1 > max_people:
        issues.add("max_participants_exceeded", f"Max {max_people} attendees per booking.")


def _room_block_overlaps(
    facility: FacilitySnapshot, unit_id: str, start: date, end: date
) -> SlotBlock | None:
    """Return a management block overlapping a room stay, if any."""
    return next(
        (
            block
            for block in facility.slot_blocks
            if (block.unit_id is None or block.unit_id == unit_id)
            and (block.ends_on or block.starts_on) >= start
            and end >= block.starts_on
        ),
        None,
    )


def _validate_room(ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues) -> None:
    """Validation rules for room-stay archetype."""
    facility = ctx.facility
    check_in, check_out = draft.local_date, draft.end_local_date
    nights = max(len(each_date(check_in, check_out)) - 1, 1)
    min_nights = facility.setup.room.min_nights if facility.setup.room is not None else 1
    if check_out <= check_in:
        issues.add("invalid_stay_range", "Check-out must be after check-in.")
    if nights < min_nights:
        issues.add(
            "below_min_nights",
            f"Minimum stay is {min_nights} night{'s' if min_nights > 1 else ''}.",
        )
    if not draft.unit_id:
        issues.add("unit_required", "Select a room.")
    for stay_day in each_date(check_in, check_out):
        if is_blackout(facility, stay_day):
            issues.add("blackout_date", f"Closed on {stay_day.isoformat()}.")
    if draft.unit_id:
        clash = any(
            reservation.unit_id == draft.unit_id
            and reservation.local_date <= check_out
            and check_in <= reservation.end_local_date
            for reservation in ctx.active
        )
        if clash:
            issues.add("unit_booked", "Room already reserved for overlapping dates.")
        block = _room_block_overlaps(facility, draft.unit_id, check_in, check_out)
        if block is not None:
            issues.add(
                "blocked",
                f"Room is blocked by management for overlapping dates ({block.reason}).",
            )


def _validate_day_range_dates(
    facility: FacilitySnapshot, dates: list[date], issues: _Issues
) -> None:
    """Per-date closure and maintenance checks for day-range bookings."""
    for stay_day in dates:
        day_hours = hours_for(facility, stay_day)
        if day_hours.closed:
            issues.add("closed_day", f"Closed on {stay_day.isoformat()}.")
            continue
        if is_blackout(facility, stay_day):
            issues.add("blackout_date", f"Closed on {stay_day.isoformat()}.")
        if maintenance_for(facility, stay_day):
            issues.add("maintenance_overlap", f"Maintenance on {stay_day.isoformat()}.")


def _validate_day_range_single_day(
    ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues, day_hours: DayHours
) -> None:
    """Time, block and busy checks for a single-day day-range booking."""
    facility = ctx.facility
    start_min, end_min = draft.start_min, draft.end_min
    if end_min <= start_min:
        issues.add("invalid_time_range", "End time must be after start time.")
    if start_min < day_hours.open or end_min > day_hours.close:
        issues.add("outside_hours", f"Outside operating hours ({_hours_label(day_hours)}).")
    min_hours = facility.pricing.min_billable_hours or 0
    if end_min - start_min < min_hours * 60:
        issues.add("below_min_duration", f"Minimum booking is {min_hours} hrs.")
    blocks = slot_blocks_for(facility, draft.local_date)
    if any(_block_covers(block, None, start_min, end_min) for block in blocks):
        issues.add("blocked", "Blocked by management for this time.")
    busy = _busy_intervals(facility, ctx.active, draft.local_date, ctx.host_name_of)
    if any(_overlaps(start_min, end_min, busy_start, busy_end) for busy_start, busy_end, _ in busy):
        issues.add("facility_busy", f"{facility.name} is busy around this time (incl. buffer).")


def _validate_day_range_multi_day(
    facility: FacilitySnapshot,
    active: list[EngineReservation],
    dates: list[date],
    issues: _Issues,
) -> None:
    """Ensure no active reservation occupies any day in a multi-day range."""
    for stay_day in dates:
        if any(_raw_intervals(reservation, stay_day) for reservation in active):
            issues.add(
                "facility_busy",
                f"{facility.name} already booked on {stay_day.isoformat()}.",
            )


def _validate_event_participants(
    facility: FacilitySnapshot, draft: BookingDraft, issues: _Issues
) -> None:
    """Enforce attendee cap when configured."""
    max_people = _max_event_participants(facility)
    if max_people is not None and len(draft.participants) + 1 > max_people:
        issues.add("max_participants_exceeded", f"Max {max_people} attendees per booking.")


def _validate_day_range(
    ctx: AvailabilityContext, draft: BookingDraft, issues: _Issues, day_hours: DayHours
) -> None:
    """Validation rules for day-range / venue hire archetype."""
    facility = ctx.facility
    dates = each_date(draft.local_date, draft.end_local_date)
    _validate_day_range_dates(facility, dates, issues)
    if len(dates) == 1 and day_hours.closed:
        issues.add("closed_day", "Closed on this day.")
    if len(dates) == 1:
        _validate_day_range_single_day(ctx, draft, issues, day_hours)
    else:
        _validate_day_range_multi_day(facility, ctx.active, dates, issues)
    _validate_event_participants(facility, draft, issues)


def validate_draft(ctx: AvailabilityContext, draft: BookingDraft) -> Validation:
    """Validate a booking draft against facility rules and current occupancy."""
    facility = ctx.facility
    issues = _Issues()
    day_hours = hours_for(facility, draft.local_date)
    _validate_common(ctx, draft, issues)
    if facility.archetype == SLOT:
        _validate_slot(ctx, draft, issues, day_hours)
    elif facility.archetype == TEE_TIME:
        _validate_tee(ctx, draft, issues, day_hours)
    elif facility.archetype == DURATION:
        _validate_duration(ctx, draft, issues, day_hours)
    elif facility.archetype == ROOM:
        _validate_room(ctx, draft, issues)
    else:
        _validate_day_range(ctx, draft, issues, day_hours)
    return issues.result()


def without_codes(validation: Validation, codes: Iterable[str]) -> Validation:
    """Drop issues whose codes appear in ``codes``."""
    skip = set(codes)
    kept = [issue for issue in validation.errors if issue.code not in skip]
    return Validation(ok=not kept, errors=kept)


def reservation_start(reservation: EngineReservation) -> datetime:
    """Naive local start datetime for a reservation."""
    return local_start(reservation.local_date, reservation.start_min)


def can_reschedule(
    facility: FacilitySnapshot, reservation: EngineReservation, now: datetime
) -> tuple[bool, str]:
    """Whether ``reservation`` may be rescheduled at ``now``, with a reason when false."""
    if reservation.status not in (
        FacilityReservationStatus.CONFIRMED.value,
        FacilityReservationStatus.PENDING_APPROVAL.value,
    ):
        return False, "This booking can no longer be rescheduled."
    mins_to_start = (reservation_start(reservation) - now).total_seconds() / 60
    if mins_to_start < facility.policies.reschedule_cutoff_hours * 60:
        return (
            False,
            f"Reschedules are allowed until {facility.policies.reschedule_cutoff_hours} hrs "
            "before the start time.",
        )
    return True, ""


def get_month_summary(
    ctx: AvailabilityContext, month_start: date, days: int = 42
) -> list[DaySummary]:
    """Month grid with slot or hour aggregates per day."""
    out: list[DaySummary] = []
    for overview in get_month_overview(ctx, month_start, days):
        if overview.state in ("past", "closed", "blackout", "maintenance"):
            out.append(
                DaySummary(
                    local_date=overview.local_date,
                    state=overview.state,
                    note=overview.note,
                    total_slots=0,
                    available_slots=0,
                    booked_slots=0,
                    blocked_slots=0,
                )
            )
            continue
        day = get_day_availability(ctx, overview.local_date)
        if ctx.facility.archetype in (SLOT, TEE_TIME):
            out.append(_slot_day_summary(overview, day))
        else:
            out.append(_hours_day_summary(ctx.facility, overview, day))
    return out


def _slot_day_summary(overview: MonthDayView, day: DayAvailability) -> DaySummary:
    """Aggregate slot counts for one calendar cell."""
    total = available = blocked = 0
    for unit_view in day.units:
        for slot in unit_view.slots:
            total += 1
            if slot.state == "available":
                available += 1
            elif slot.state == "blocked":
                blocked += 1
    if total == 0:
        state = "closed"
    elif available == 0 and blocked > 0:
        state = "maintenance"
    elif available == 0:
        state = "busy"
    else:
        state = "free"
    return DaySummary(
        local_date=overview.local_date,
        state=state,
        note=overview.note,
        total_slots=total,
        available_slots=available,
        booked_slots=total - available - blocked,
        blocked_slots=blocked,
    )


def _hours_day_summary(
    facility: FacilitySnapshot, overview: MonthDayView, day: DayAvailability
) -> DaySummary:
    """Aggregate open vs booked hours for one calendar cell."""
    day_hours = hours_for(facility, overview.local_date)
    open_hours = max(0, (day_hours.close - day_hours.open) / 60)
    busy_mins = 0
    for busy in day.busy:
        clip_start = max(busy.start_min, day_hours.open)
        clip_end = min(busy.end_min, day_hours.close)
        if clip_end > clip_start:
            busy_mins += clip_end - clip_start
    booked_hours = ts_round(busy_mins / 60 * 10) / 10
    available_hours = max(0, ts_round((open_hours - booked_hours) * 10) / 10)
    if open_hours == 0:
        state = "closed"
    elif available_hours == 0:
        state = "busy"
    else:
        state = "free"
    return DaySummary(
        local_date=overview.local_date,
        state=state,
        note=overview.note,
        total_slots=open_hours,
        available_slots=available_hours,
        booked_slots=booked_hours,
        blocked_slots=0,
    )


def next_availability(ctx: AvailabilityContext) -> NextAvailabilityResponse | None:
    """Earliest bookable slot or day from ``ctx.now``."""
    today = ctx.now.date()
    if ctx.facility.archetype in (SLOT, TEE_TIME):
        for offset in range(NEXT_SLOT_LOOKAHEAD_DAYS):
            day = today + timedelta(days=offset)
            for unit_view in get_day_availability(ctx, day).units:
                slot = next((slot for slot in unit_view.slots if slot.state == "available"), None)
                if slot is not None:
                    return NextAvailabilityResponse(
                        local_date=day, start_min=slot.start_min, unit_id=unit_view.unit.id
                    )
        return None
    month_overview = get_month_overview(ctx, today, NEXT_DAY_LOOKAHEAD_DAYS)
    free_day = next((day for day in month_overview if day.state == "free"), None)
    if free_day is None:
        return None
    return NextAvailabilityResponse(local_date=free_day.local_date)


def room_availability(
    ctx: AvailabilityContext, check_in: date, check_out: date
) -> list[RoomAvailabilityItem]:
    """Per-room availability for a stay window."""
    facility = ctx.facility
    items: list[RoomAvailabilityItem] = []
    for unit in facility.units:
        booking = next(
            (
                reservation
                for reservation in ctx.active
                if reservation.unit_id == unit.id
                and reservation.local_date <= check_out
                and check_in <= reservation.end_local_date
            ),
            None,
        )
        if booking is not None:
            guest_names = [participant.name for participant in booking.participants]
            guests = [ctx.host_name_of(booking), *guest_names][:2]
            items.append(
                RoomAvailabilityItem(
                    unit=unit_summary(unit),
                    status="booked",
                    detail=(
                        f"{booking.local_date.isoformat()} → "
                        f"{booking.end_local_date.isoformat()} · {', '.join(guests)}"
                    ),
                )
            )
            continue
        block = _room_block_overlaps(facility, unit.id, check_in, check_out)
        if block is not None:
            detail = (
                f"{block.category + ': ' if block.category else ''}{block.reason}"
                f" · {block.starts_on.isoformat()}"
                f"{' → ' + block.ends_on.isoformat() if block.ends_on else ''}"
            )
            items.append(
                RoomAvailabilityItem(unit=unit_summary(unit), status="blocked", detail=detail)
            )
            continue
        items.append(
            RoomAvailabilityItem(
                unit=unit_summary(unit),
                status="available",
                detail="Available for the selected dates",
            )
        )
    return items
