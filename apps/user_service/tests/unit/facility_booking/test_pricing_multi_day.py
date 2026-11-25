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


def test_day_rate_slot_bills_the_day_not_an_eighth_of_it():
    """A per-day court rate is the price for that date, including a one-hour slot."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(
            unit_label=FacilityRateUnit.DAY,
            resident_rate=400,
            guest_flat_fee=500,
            weekend_multiplier=1.25,
        ),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 24),
            end_local_date=date(2026, 11, 24),
            start_min=17 * 60 + 55,
            end_min=18 * 60 + 55,
        ),
    )
    rental = quote.lines[0]
    assert rental.label == "Badminton Court Rental — Full Day Rate"
    assert rental.detail == "1 day at ₹400/day"
    assert rental.amount == 400
    assert quote.lines[1].amount == 72
    assert quote.total == 472
    assert quote.due_now == 472


def test_day_rate_ignores_how_many_hours_fall_on_that_day():
    """Two hours on a per-day court still cost one day rate."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(unit_label=FacilityRateUnit.DAY, resident_rate=400, tax_percent=0),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 24),
            end_local_date=date(2026, 11, 24),
            start_min=17 * 60,
            end_min=19 * 60,
        ),
    )
    assert quote.lines[0].amount == 400
    assert quote.total == 400


def test_day_rate_slot_still_applies_the_weekend_multiplier():
    """Sunday uses the configured weekend multiplier on the day rate."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(unit_label=FacilityRateUnit.DAY, resident_rate=400, tax_percent=0),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 22),
            end_local_date=date(2026, 11, 23),
            start_min=17 * 60,
            end_min=18 * 60,
        ),
    )
    assert quote.lines[0].amount == 900
    assert quote.lines[0].detail == "2 days at ₹400/day (×1.25, ×1.00)"


def test_explicit_hourly_override_still_bills_a_day_priced_slot_by_the_hour():
    """An hourly override replaces the day rate for the booked hours."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(
            unit_label=FacilityRateUnit.DAY,
            resident_rate=400,
            hourly_rate=80,
            tax_percent=0,
        ),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(local_date=date(2026, 11, 24), end_local_date=date(2026, 11, 24)),
    )
    assert quote.lines[0].label.endswith("Hourly Rate")
    assert quote.lines[0].detail == "1 hr at ₹80/hr"
    assert quote.lines[0].amount == 80


def test_partial_day_venue_without_an_hourly_override_uses_the_day_rate():
    """A day-priced hall is not divided by eight when no hourly price is set."""
    facility = _facility(
        FacilityBookingArchetype.DAY_RANGE,
        pricing=_pricing(unit_label=FacilityRateUnit.DAY, resident_rate=400, tax_percent=0),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(
            local_date=date(2026, 11, 24),
            end_local_date=date(2026, 11, 24),
            start_min=17 * 60,
            end_min=18 * 60,
        ),
    )
    assert quote.lines[0].detail == "1 day at ₹400/day"
    assert quote.lines[0].amount == 400


def test_month_rate_is_charged_once_for_a_short_booking():
    """A monthly rate is the booking price, not a slice of an eight-hour day."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(unit_label=FacilityRateUnit.MONTH, resident_rate=3000, tax_percent=0),
    )
    quote = pricing.quote_booking(
        facility,
        _draft(local_date=date(2026, 11, 24), end_local_date=date(2026, 11, 24)),
    )
    assert quote.lines[0].label.endswith("Monthly Rate")
    assert quote.lines[0].detail == "1 month at ₹3,000/month"
    assert quote.lines[0].amount == 3000


def test_month_rate_with_hourly_override_bills_the_booked_hours():
    """An hourly override on a monthly slot or event replaces the month rate."""
    pricing_config = _pricing(
        unit_label=FacilityRateUnit.MONTH,
        resident_rate=3000,
        hourly_rate=100,
        tax_percent=0,
    )
    draft = _draft(local_date=date(2026, 11, 24), end_local_date=date(2026, 11, 24))
    for archetype in (FacilityBookingArchetype.SLOT, FacilityBookingArchetype.DURATION):
        quote = pricing.quote_booking(_facility(archetype, pricing=pricing_config), draft)
        assert quote.lines[0].label.endswith("Hourly Rate")
        assert quote.lines[0].detail == "1 hr at ₹100/hr"
        assert quote.lines[0].amount == 100


def test_month_rate_uses_the_highest_weekend_or_holiday_multiplier():
    """A month charge takes the highest day multiplier among the dates it covers."""
    facility = _facility(
        FacilityBookingArchetype.SLOT,
        pricing=_pricing(
            unit_label=FacilityRateUnit.MONTH,
            resident_rate=3000,
            tax_percent=0,
            holidays=[date(2026, 11, 23)],
        ),
    )
    sunday = pricing.quote_booking(
        facility,
        _draft(local_date=date(2026, 11, 22), end_local_date=date(2026, 11, 22)),
    )
    assert sunday.lines[0].detail == "1 month at ₹3,000/month (×1.25)"
    assert sunday.lines[0].amount == 3750

    mixed = pricing.quote_booking(
        facility,
        _draft(local_date=date(2026, 11, 22), end_local_date=date(2026, 11, 23)),
    )
    assert mixed.lines[0].detail == "1 month at ₹3,000/month (×1.50)"
    assert mixed.lines[0].amount == 4500


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
