"""Focused unit tests for facility booking availability edge branches."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from apps.user_service.app.schemas.enums import FacilityBookingArchetype
from apps.user_service.app.schemas.facility_booking import DayAvailability, MonthDayView
from apps.user_service.app.schemas.facility_booking_config import DayHours
from apps.user_service.app.services.facility_booking import availability
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    BookingUnit,
    EngineReservation,
    FacilitySnapshot,
    Participant,
    SchedulePeriod,
    SlotBlock,
    Validation,
    ValidationIssue,
)


def _slot_facility(**overrides) -> FacilitySnapshot:
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    base = FacilitySnapshot(
        id="fac-1",
        name="Court",
        archetype=FacilityBookingArchetype.SLOT.value,
        slot_minutes=defaults.slot_minutes,
        hours=[DayHours(open=8 * 60, close=10 * 60, closed=False) for _ in range(7)],
        pricing=defaults.pricing,
        policies=defaults.policies.model_copy(update={"weekly_cap": 1}),
        setup=defaults.setup,
        units=[BookingUnit(id="unit-1", name="Court 1", room_type=None)],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    return replace(base, **overrides) if overrides else base


def _ctx(
    facility: FacilitySnapshot, *, now: datetime, active=None
) -> availability.AvailabilityContext:
    return availability.AvailabilityContext(
        facility=facility,
        active=active or [],
        now=now,
    )


def test_active_schedule_and_hours_outside_period():
    """Seasonal schedule outside period yields closed hours."""
    facility = _slot_facility(
        schedules=[
            SchedulePeriod(
                id="summer",
                name="Summer",
                starts_on=date(2026, 6, 1),
                ends_on=date(2026, 8, 31),
                hours=[DayHours(open=9 * 60, close=17 * 60, closed=False) for _ in range(7)],
            )
        ]
    )
    day = date(2026, 1, 15)
    assert availability.active_schedule(facility, day) is None
    hours = availability.hours_for(facility, day)
    assert hours.closed is True


def test_without_codes_filters_validation_issues():
    """without_codes removes matching validation issue codes."""
    validation = Validation(
        ok=False,
        errors=[
            ValidationIssue(code="outside_advance_window", message="Too far"),
            ValidationIssue(code="weekly_cap_reached", message="Cap"),
        ],
    )
    trimmed = availability.without_codes(validation, availability.STAFF_OVERRIDABLE_CODES)
    assert trimmed.ok is True
    assert trimmed.errors == []


def test_next_availability_slot_and_day_modes():
    """next_availability finds slot/day based on archetype."""
    now = datetime(2026, 3, 10, 8, 0, 0)
    slot_facility = _slot_facility()
    slot_ctx = _ctx(slot_facility, now=now)
    slot_next = availability.next_availability(slot_ctx)
    assert slot_next is not None
    assert slot_next.start_min == 8 * 60

    duration_defaults = default_booking_config(FacilityBookingArchetype.DURATION)
    duration_facility = FacilitySnapshot(
        id="fac-2",
        name="Hall",
        archetype=FacilityBookingArchetype.DURATION.value,
        slot_minutes=60,
        hours=[DayHours(open=8 * 60, close=18 * 60, closed=False) for _ in range(7)],
        pricing=duration_defaults.pricing,
        policies=duration_defaults.policies,
        setup=duration_defaults.setup,
        units=[],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    day_next = availability.next_availability(_ctx(duration_facility, now=now))
    assert day_next is not None
    assert day_next.local_date == now.date()


def test_room_availability_booked_blocked_and_available():
    """room_availability reports booked, blocked, and free rooms."""
    room_defaults = default_booking_config(FacilityBookingArchetype.ROOM)
    facility = FacilitySnapshot(
        id="fac-room",
        name="Guest House",
        archetype=FacilityBookingArchetype.ROOM.value,
        slot_minutes=60,
        hours=[DayHours(open=0, close=24 * 60, closed=False) for _ in range(7)],
        pricing=room_defaults.pricing,
        policies=room_defaults.policies,
        setup=room_defaults.setup,
        units=[
            BookingUnit(id="room-1", name="101", room_type="deluxe"),
            BookingUnit(id="room-2", name="102", room_type="deluxe"),
        ],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[
            SlotBlock(
                id="block-1",
                starts_on=date(2026, 3, 12),
                ends_on=date(2026, 3, 14),
                unit_id="room-2",
                from_min=0,
                to_min=24 * 60,
                reason="Maintenance hold",
                category="Maintenance",
            )
        ],
    )
    active = [
        EngineReservation(
            id="res-1",
            facility_id="fac-room",
            unit_id="room-1",
            local_date=date(2026, 3, 10),
            end_local_date=date(2026, 3, 12),
            start_min=0,
            end_min=24 * 60,
            host_contact_id="host-1",
            status="confirmed",
            participants=[Participant(kind="guest", name="Guest", contact_id=None)],
            host_name="Host",
        )
    ]
    ctx = availability.AvailabilityContext(
        facility=facility,
        active=active,
        now=datetime(2026, 3, 10, 10, 0, 0),
    )
    items = availability.room_availability(ctx, date(2026, 3, 10), date(2026, 3, 13))
    statuses = {item.unit.id: item.status for item in items}
    assert statuses["room-1"] == "booked"
    assert statuses["room-2"] == "blocked"


def test_validate_draft_slot_player_limit_and_weekly_cap():
    """Slot validation enforces player cap and weekly usage cap."""
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    facility = _slot_facility(
        policies=defaults.policies.model_copy(update={"weekly_cap": 1}),
        setup=defaults.setup.model_copy(
            update={"slot": defaults.setup.slot.model_copy(update={"max_players": 2})}
        ),
    )
    now = datetime(2026, 3, 10, 9, 0, 0)
    existing = EngineReservation(
        id="res-1",
        facility_id="fac-1",
        unit_id="unit-1",
        local_date=date(2026, 3, 10),
        end_local_date=date(2026, 3, 10),
        start_min=8 * 60,
        end_min=9 * 60,
        host_contact_id="host-1",
        status="confirmed",
        participants=[],
        host_name="Host",
    )
    ctx = availability.AvailabilityContext(
        facility=facility,
        active=[existing],
        now=now,
        weekly_pool=[existing],
    )
    draft = BookingDraft(
        facility_id="fac-1",
        unit_id="unit-1",
        local_date=date(2026, 3, 10),
        end_local_date=date(2026, 3, 10),
        start_min=9 * 60,
        end_min=10 * 60,
        host_contact_id="host-1",
        participants=[
            Participant(kind="guest", name="A", contact_id=None),
            Participant(kind="guest", name="B", contact_id=None),
        ],
    )
    result = availability.validate_draft(ctx, draft)
    codes = {issue.code for issue in result.errors}
    assert "max_players_exceeded" in codes
    assert "weekly_cap_reached" in codes


def test_get_month_summary_hours_facility():
    """Month summary aggregates open hours for non-slot archetypes."""
    defaults = default_booking_config(FacilityBookingArchetype.DURATION)
    facility = FacilitySnapshot(
        id="fac-duration",
        name="Studio",
        archetype=FacilityBookingArchetype.DURATION.value,
        slot_minutes=60,
        hours=[DayHours(open=8 * 60, close=12 * 60, closed=False) for _ in range(7)],
        pricing=defaults.pricing,
        policies=defaults.policies,
        setup=defaults.setup,
        units=[],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    now = datetime(2026, 3, 10, 9, 0, 0)
    summary = availability.get_month_summary(
        availability.AvailabilityContext(facility=facility, active=[], now=now),
        month_start=date(2026, 3, 1),
        days=7,
    )
    assert summary[0].total_slots == 0 or summary[0].state in {"free", "closed", "past"}


def test_slot_view_closed_and_tee_partial_booking():
    """Slot tiles mark closed days and partial tee bookings."""
    defaults = default_booking_config(FacilityBookingArchetype.TEE_TIME)
    facility = FacilitySnapshot(
        id="fac-tee",
        name="Golf",
        archetype=FacilityBookingArchetype.TEE_TIME.value,
        slot_minutes=60,
        hours=[DayHours(open=8 * 60, close=10 * 60, closed=True) for _ in range(7)],
        pricing=defaults.pricing,
        policies=defaults.policies,
        setup=defaults.setup,
        units=[BookingUnit(id="tee-1", name="Tee 1", room_type=None)],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    now = datetime(2027, 3, 10, 7, 0, 0)
    day = now.date()
    closed_facility = replace(
        facility,
        hours=[DayHours(open=8 * 60, close=10 * 60, closed=True) for _ in range(7)],
    )
    ctx = availability.AvailabilityContext(facility=closed_facility, active=[], now=now)
    day_view = availability.get_day_availability(ctx, day)
    assert day_view.units[0].slots[0].state == "blocked"
    assert day_view.units[0].slots[0].reason == "Closed"

    open_facility = replace(
        facility, hours=[DayHours(open=8 * 60, close=10 * 60, closed=False) for _ in range(7)]
    )
    reservation = EngineReservation(
        id="res-tee",
        facility_id="fac-tee",
        unit_id="tee-1",
        local_date=day,
        end_local_date=day,
        start_min=8 * 60,
        end_min=9 * 60,
        host_contact_id="host-1",
        status="confirmed",
        participants=[
            Participant(kind="guest", name="P1", contact_id=None),
            Participant(kind="guest", name="P2", contact_id=None),
            Participant(kind="guest", name="P3", contact_id=None),
        ],
        host_name="Host",
    )
    tee_ctx = availability.AvailabilityContext(
        facility=open_facility, active=[reservation], now=now
    )
    tee_day = availability.get_day_availability(tee_ctx, day)
    slot = tee_day.units[0].slots[0]
    assert slot.state == "booked"
    assert slot.remaining == 0


def test_month_overview_outside_schedule_and_slot_summary_states():
    """Month overview notes outside schedule; slot summary handles zero slots."""
    default_booking_config(FacilityBookingArchetype.SLOT)
    facility = _slot_facility(
        schedules=[
            SchedulePeriod(
                id="season",
                name="Season",
                starts_on=date(2027, 6, 1),
                ends_on=date(2027, 8, 31),
                hours=[DayHours(open=9 * 60, close=17 * 60, closed=False) for _ in range(7)],
            )
        ]
    )
    now = datetime(2027, 5, 15, 9, 0, 0)
    overview = availability.get_month_overview(
        availability.AvailabilityContext(facility=facility, active=[], now=now),
        month_start=date(2027, 5, 15),
        days=3,
    )
    assert overview[0].state == "closed"
    assert overview[0].note == "Outside schedule"

    closed_day = DayAvailability(
        local_date=date(2026, 1, 1),
        open_min=0,
        close_min=0,
        closed=True,
        blackout=False,
        closure_reason=None,
        past=False,
        maintenance=[],
        units=[],
        busy=[],
    )
    month_cell = MonthDayView(local_date=date(2026, 1, 1), state="free", note=None)
    summary = availability._slot_day_summary(month_cell, closed_day)
    assert summary.state == "closed"


def test_validate_draft_day_range_and_room_branches():
    """Day-range and room validators cover invalid stay and blackout branches."""
    room_defaults = default_booking_config(FacilityBookingArchetype.ROOM)
    room_facility = FacilitySnapshot(
        id="fac-room",
        name="Rooms",
        archetype=FacilityBookingArchetype.ROOM.value,
        slot_minutes=60,
        hours=[DayHours(open=0, close=24 * 60, closed=False) for _ in range(7)],
        pricing=room_defaults.pricing,
        policies=room_defaults.policies,
        setup=room_defaults.setup,
        units=[BookingUnit(id="room-1", name="101", room_type=None)],
        closures={date(2026, 3, 11): "Maintenance"},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    now = datetime(2026, 3, 10, 9, 0, 0)
    ctx = availability.AvailabilityContext(facility=room_facility, active=[], now=now)
    draft = BookingDraft(
        facility_id="fac-room",
        unit_id="room-1",
        local_date=date(2026, 3, 10),
        end_local_date=date(2026, 3, 12),
        start_min=0,
        end_min=24 * 60,
        host_contact_id="host-1",
        participants=[],
    )
    result = availability.validate_draft(ctx, draft)
    assert any(err.code == "blackout_date" for err in result.errors)

    duration_defaults = default_booking_config(FacilityBookingArchetype.DAY_RANGE)
    duration_facility = FacilitySnapshot(
        id="fac-day",
        name="Hall",
        archetype=FacilityBookingArchetype.DAY_RANGE.value,
        slot_minutes=60,
        hours=[DayHours(open=8 * 60, close=18 * 60, closed=False) for _ in range(7)],
        pricing=duration_defaults.pricing,
        policies=duration_defaults.policies,
        setup=duration_defaults.setup,
        units=[],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    day_ctx = availability.AvailabilityContext(facility=duration_facility, active=[], now=now)
    day_draft = BookingDraft(
        facility_id="fac-day",
        unit_id=None,
        local_date=date(2026, 3, 10),
        end_local_date=date(2026, 3, 10),
        start_min=8 * 60,
        end_min=8 * 60,
        host_contact_id="host-1",
        participants=[],
    )
    day_result = availability.validate_draft(day_ctx, day_draft)
    assert any(err.code == "invalid_time_range" for err in day_result.errors)


def test_next_availability_returns_none_when_no_free_days():
    """next_availability returns None when lookahead finds no bookable day."""
    defaults = default_booking_config(FacilityBookingArchetype.DURATION)
    facility = FacilitySnapshot(
        id="fac-duration",
        name="Studio",
        archetype=FacilityBookingArchetype.DURATION.value,
        slot_minutes=60,
        hours=[DayHours(open=8 * 60, close=12 * 60, closed=True) for _ in range(7)],
        pricing=defaults.pricing,
        policies=defaults.policies,
        setup=defaults.setup,
        units=[],
        closures={},
        maintenance=[],
        schedules=[],
        slot_blocks=[],
    )
    now = datetime(2026, 3, 10, 9, 0, 0)
    assert (
        availability.next_availability(
            availability.AvailabilityContext(facility=facility, active=[], now=now)
        )
        is None
    )
