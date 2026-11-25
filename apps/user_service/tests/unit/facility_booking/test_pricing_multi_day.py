"""Multi-day quotes repeat the daily window on every inclusive date."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityRateUnit,
)
from apps.user_service.app.schemas.facility_booking_config import (
    DayHours,
    PricingConfig,
    TimeBand,
)
from apps.user_service.app.services.facility_booking import pricing
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.services.facility_booking.money import MAX_DATE_SPAN_DAYS
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    BookingUnit,
    FacilitySnapshot,
    Participant,
)


def _hours() -> list[DayHours]:
    return [DayHours(open=6 * 60, close=22 * 60, closed=False) for _ in range(7)]


def _pricing(**updates) -> PricingConfig:
    base = dict(
        tax_percent=18,
        resident_rate=400,
        guest_rate=800,
        guest_flat_fee=200,
        unit_label=FacilityRateUnit.HOUR,
        weekend_multiplier=1.25,
        holiday_multiplier=1.5,
    )
    base.update(updates)
    return PricingConfig(**base)


def _facility(archetype: FacilityBookingArchetype, **overrides) -> FacilitySnapshot:
    defaults = default_booking_config(archetype)
    base = FacilitySnapshot(
        id="fac-1",
        name="Badminton Court",
        archetype=archetype.value,
        slot_minutes=defaults.slot_minutes,
        hours=_hours(),
        pricing=_pricing(),
        policies=defaults.policies,
        setup=defaults.setup,
        units=[BookingUnit(id="unit-1", name="Court 1")],
    )
    return replace(base, **overrides) if overrides else base


def _draft(**overrides) -> BookingDraft:
    base = BookingDraft(
        facility_id="fac-1",
        unit_id="unit-1",
        local_date=date(2026, 11, 15),
        end_local_date=date(2026, 11, 16),
        start_min=15 * 60,
        end_min=16 * 60,
        host_contact_id="host-1",
        participants=[],
    )
    return replace(base, **overrides)


def test_slot_single_day_quote_stays_one_hour():
    """A same-day court booking is still one hour at the day's multiplier."""
    quote = pricing.quote_booking(
        _facility(FacilityBookingArchetype.SLOT),
        _draft(local_date=date(2026, 11, 17), end_local_date=date(2026, 11, 17)),
    )
    rental = quote.lines[0]
    assert rental.detail == "1 hr at ₹400/hr"
    assert rental.amount == 400
    assert quote.total == 472


def test_slot_two_day_quote_adds_each_days_multiplier():
    """Sunday ×1.25 plus Monday ×1 is two hours, not one weekend hour."""
    quote = pricing.quote_booking(_facility(FacilityBookingArchetype.SLOT), _draft())
    rental = quote.lines[0]
    assert rental.amount == 900
    assert rental.detail == "2 days × 1 hr/day at ₹400/hr (×1.25, ×1.00)"
    assert quote.lines[1].amount == 162
    assert quote.total == 1062
    assert quote.due_now == 1062


def test_slot_two_weekend_days_share_one_multiplier():
    """Saturday and Sunday both use the weekend multiplier."""
    quote = pricing.quote_booking(
        _facility(FacilityBookingArchetype.SLOT),
        _draft(local_date=date(2026, 11, 14), end_local_date=date(2026, 11, 15)),
    )
    rental = quote.lines[0]
    assert rental.amount == 1000
    assert rental.detail == "2 days × 1 hr/day at ₹400/hr (×1.25)"


def test_slot_quote_applies_weekday_bands_per_day():
    """A weekday-only time band does not multiply the weekend day."""
    band = TimeBand(
        id="evening",
        label="Evening peak",
        from_min=14 * 60,
        to_min=18 * 60,
        multiplier=1.5,
        weekdays=[1, 2, 3, 4, 5],
    )
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(bands=[band], tax_percent=0),
    )
    quote = pricing.quote_booking(facility, _draft())
    assert quote.lines[0].amount == 1100
    assert "×1.25, ×1.50" in quote.lines[0].detail


def test_slot_guest_fee_stays_flat_across_days():
    """Guest fee is charged once, while the court rental scales with days."""
    facility = _facility(FacilityBookingArchetype.SLOT, pricing=_pricing(tax_percent=0))
    draft = _draft(
        local_date=date(2026, 11, 17),
        end_local_date=date(2026, 11, 18),
        participants=[Participant(kind="guest", name="Guest", contact_id=None)],
    )
    quote = pricing.quote_booking(facility, draft)
    assert quote.lines[0].amount == 800
    assert quote.lines[1].label == "Guest fee × 1"
    assert quote.lines[1].amount == 200
    assert quote.total == 1000


def test_duration_and_tee_quotes_repeat_the_daily_window():
    """Duration and tee-time ranges bill every occupied day, not the first only."""
    duration = pricing.quote_booking(
        _facility(
            FacilityBookingArchetype.DURATION,
            pricing=_pricing(tax_percent=0, cleaning_fee=50),
        ),
        _draft(
            local_date=date(2026, 11, 17),
            end_local_date=date(2026, 11, 18),
            end_min=17 * 60,
        ),
    )
    assert duration.lines[0].amount == 1600
    assert duration.lines[0].detail.startswith("2 days × 2 hrs/day")
    assert duration.lines[1].amount == 50

    tee = pricing.quote_booking(
        _facility(FacilityBookingArchetype.TEE_TIME, pricing=_pricing(tax_percent=0)),
        _draft(
            participants=[Participant(kind="guest", name="Guest", contact_id=None)],
        ),
    )
    assert tee.lines[0].amount == 900
    assert tee.lines[0].detail == "₹400/player · 2 days (×1.25, ×1.00)"
    assert tee.lines[1].amount == 1800
    assert tee.lines[1].detail == "₹800/player · 2 days (×1.25, ×1.00)"


def test_monthly_partial_day_charges_eight_hour_rate_with_evening_surge():
    """Club House: ₹6,000/month is ₹750/hr, then the evening band, not ₹25/hr."""
    band = TimeBand(
        id="evening",
        label="Evening surge",
        from_min=19 * 60,
        to_min=21 * 60,
        multiplier=1.25,
        weekdays=None,
    )
    facility = _facility(
        FacilityBookingArchetype.DAY_RANGE,
        name="Club House",
        pricing=_pricing(
            resident_rate=6000,
            hourly_rate=None,
            unit_label=FacilityRateUnit.MONTH,
            weekend_multiplier=1,
            holiday_multiplier=1,
            guest_flat_fee=2000,
            tax_percent=18,
            bands=[band],
            min_billable_hours=2,
        ),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 26),
            end_local_date=date(2026, 11, 26),
            start_min=20 * 60,
            end_min=22 * 60,
        ),
    )
    rental = quote.lines[0]
    assert rental.label == "Club House Rental — Hourly Rate"
    assert rental.detail == "2 hrs at ₹750/hr (×1.25)"
    assert rental.amount == 1875
    assert quote.lines[1].amount == 338
    assert quote.total == 2213
    assert quote.due_now == 0
    assert quote.due_later == 2213


def test_explicit_hourly_rate_overrides_the_monthly_list_rate():
    """A saved member hourly rate is charged as entered."""
    facility = _facility(
        FacilityBookingArchetype.DAY_RANGE,
        pricing=_pricing(
            resident_rate=6000,
            hourly_rate=500,
            unit_label=FacilityRateUnit.MONTH,
            weekend_multiplier=1,
            tax_percent=0,
            min_billable_hours=0,
        ),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 26),
            end_local_date=date(2026, 11, 26),
            start_min=20 * 60,
            end_min=22 * 60,
        ),
    )
    assert quote.lines[0].detail == "2 hrs at ₹500/hr"
    assert quote.lines[0].amount == 1000


def test_monthly_full_day_still_uses_the_thirty_day_rate():
    """A full-day booking bills the monthly rate across 30 days."""
    facility = _facility(
        FacilityBookingArchetype.DAY_RANGE,
        pricing=_pricing(
            resident_rate=6000,
            unit_label=FacilityRateUnit.MONTH,
            weekend_multiplier=1,
            tax_percent=0,
            min_billable_hours=0,
        ),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 26),
            end_local_date=date(2026, 11, 26),
            start_min=6 * 60,
            end_min=22 * 60,
        ),
    )
    assert quote.lines[0].amount == 200
    assert "₹200/day" in (quote.lines[0].detail or "")


def test_over_cap_range_is_not_priced_from_a_truncated_prefix():
    """A span longer than each_date's cap is not billed as the first 400 days."""
    start = date(2026, 11, 15)
    quote = pricing.quote_booking(
        _facility(FacilityBookingArchetype.SLOT),
        _draft(local_date=start, end_local_date=start + timedelta(days=MAX_DATE_SPAN_DAYS)),
    )
    assert quote.total == 0
    assert quote.due_now == 0
    assert quote.lines[0].amount == 0
    assert "400" in (quote.lines[0].detail or "")
