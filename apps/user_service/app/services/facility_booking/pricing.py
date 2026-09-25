"""Pricing engine: quotes, cancellation fees and reschedule adjustments.

Amounts are whole currency units; rounding mirrors the Clubhouse reference engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from apps.user_service.app.schemas.enums import (
    FacilityMembershipMode,
    FacilityParticipantKind,
    FacilityPriceMode,
    FacilityRateUnit,
    FacilityReservationStatus,
)
from apps.user_service.app.schemas.facility_booking import (
    CancelQuote,
    PriceLine,
    PriceQuote,
)
from apps.user_service.app.schemas.facility_booking_config import (
    CancellationTier,
    TimeBand,
)
from apps.user_service.app.services.facility_booking.availability import (
    DURATION,
    ROOM,
    SLOT,
    TEE_TIME,
    reservation_start,
)
from apps.user_service.app.services.facility_booking.money import (
    DEFAULT_CURRENCY_SYMBOL,
    each_date,
    fmt_hours,
    fmt_money,
    js_dow,
    ts_round,
)
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    EngineReservation,
    FacilitySnapshot,
)

HOURS_PER_DAY = 8
DAYS_PER_MONTH = 30

# Ledger entry types that count towards what a resident has paid for a reservation.
PAID_ENTRY_TYPES: frozenset[str] = frozenset({"charge", "deposit", "balance", "adjustment"})


class _Money:
    """Callable wrapper that formats amounts with a fixed currency symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def __call__(self, amount: int) -> str:
        return fmt_money(amount, self.symbol)


def day_multiplier(facility: FacilitySnapshot, day: date) -> float:
    """Weekend/holiday multiplier for ``day``."""
    if day in facility.pricing.holidays:
        return facility.pricing.holiday_multiplier
    return facility.pricing.weekend_multiplier if js_dow(day) in (0, 6) else 1


def band_multiplier(facility: FacilitySnapshot, start_min: int, day: date) -> TimeBand | None:
    """Return the time-band rule matching ``start_min`` on ``day``, if any."""
    dow = js_dow(day)
    for band in facility.pricing.bands:
        if band.from_min <= start_min < band.to_min and (
            band.weekdays is None or dow in band.weekdays
        ):
            return band
    return None


def effective_hour_rate(facility: FacilitySnapshot) -> float:
    """Resident rate normalized to per-hour regardless of configured unit."""
    rate = facility.pricing.resident_rate
    if facility.pricing.unit_label == FacilityRateUnit.DAY:
        return rate / HOURS_PER_DAY
    if facility.pricing.unit_label == FacilityRateUnit.MONTH:
        return rate / (DAYS_PER_MONTH * HOURS_PER_DAY)
    return rate


def effective_day_rate(facility: FacilitySnapshot) -> float:
    """Resident rate normalized to per-day regardless of configured unit."""
    rate = facility.pricing.resident_rate
    if facility.pricing.unit_label == FacilityRateUnit.HOUR:
        return rate * HOURS_PER_DAY
    if facility.pricing.unit_label == FacilityRateUnit.MONTH:
        return rate / DAYS_PER_MONTH
    return rate


def _rate_basis_note(facility: FacilitySnapshot, money: _Money) -> str:
    """Suffix explaining how a non-hourly list rate was derived."""
    unit = facility.pricing.unit_label
    if unit in (FacilityRateUnit.HOUR, FacilityRateUnit.PLAYER):
        return ""
    suffix = "/day" if unit == FacilityRateUnit.DAY else "/month"
    return f" (from {money(facility.pricing.resident_rate)}{suffix})"


def _mult_factor(
    facility: FacilitySnapshot, start_min: int, day: date
) -> tuple[float, TimeBand | None]:
    """Combined day and time-band multiplier for a start time."""
    band = band_multiplier(facility, start_min, day)
    return day_multiplier(facility, day) * (band.multiplier if band else 1), band


def _factor_suffix(
    facility: FacilitySnapshot, factor: float, band: TimeBand | None, day: date
) -> str:
    """Detail suffix when a non-unity multiplier applies."""
    if band or day_multiplier(facility, day) != 1:
        return f" (×{factor:.2f})"
    return ""


def party_of(draft: BookingDraft) -> tuple[int, int, int]:
    """Return (residents incl. host, guests, players)."""
    guests = sum(
        1
        for participant in draft.participants
        if participant.kind == FacilityParticipantKind.GUEST.value
    )
    residents = (
        sum(
            1
            for participant in draft.participants
            if participant.kind == FacilityParticipantKind.RESIDENT.value
        )
        + 1
    )
    return residents, guests, residents + guests


def _sum(lines: Iterable[PriceLine]) -> int:
    """Rounded total of price line amounts."""
    return ts_round(sum(line.amount for line in lines))


def _guest_fee_line(facility: FacilitySnapshot, guests: int, money: _Money) -> PriceLine | None:
    """Optional flat guest-fee line when guests are present."""
    fee = facility.pricing.guest_flat_fee
    if guests > 0 and fee > 0:
        return PriceLine(
            label=f"Guest fee × {guests}", detail=f"{money(fee)} per guest", amount=fee * guests
        )
    return None


def _included_quote() -> PriceQuote:
    """Zero-cost quote for membership-included facilities."""
    return PriceQuote(
        lines=[
            PriceLine(
                label="Included in membership",
                detail="No charge — part of your community membership benefits",
                amount=0,
            )
        ],
    )


def _slot_lines(facility: FacilitySnapshot, draft: BookingDraft, money: _Money) -> list[PriceLine]:
    """Price lines for fixed slot (court) bookings."""
    _, guests, _ = party_of(draft)
    mins = draft.end_min - draft.start_min
    factor, band = _mult_factor(facility, draft.start_min, draft.local_date)
    hour_rate = ts_round(effective_hour_rate(facility))
    detail = f"{fmt_hours(mins)} @ {money(hour_rate)}/hr{_rate_basis_note(facility, money)}"
    detail += _factor_suffix(facility, factor, band, draft.local_date)
    lines = [
        PriceLine(
            label=f"{facility.name} hire",
            detail=detail,
            amount=ts_round(hour_rate * (mins / 60) * factor),
        )
    ]
    guest_line = _guest_fee_line(facility, guests, money)
    if guest_line:
        lines.append(guest_line)
    return lines


def _tee_lines(facility: FacilitySnapshot, draft: BookingDraft, money: _Money) -> list[PriceLine]:
    """Price lines for tee-time (per-player) bookings."""
    pricing = facility.pricing
    _, guests, players = party_of(draft)
    factor, band = _mult_factor(facility, draft.start_min, draft.local_date)
    lines: list[PriceLine] = []
    resident_players = players - guests
    if resident_players > 0:
        detail = f"{money(pricing.resident_rate)}/player"
        detail += _factor_suffix(facility, factor, band, draft.local_date)
        lines.append(
            PriceLine(
                label=f"Green fee — residents × {resident_players}",
                detail=detail,
                amount=ts_round(pricing.resident_rate * resident_players * factor),
            )
        )
    if guests > 0:
        lines.append(
            PriceLine(
                label=f"Green fee — guests × {guests}",
                detail=f"{money(pricing.guest_rate)}/player (×{factor:.2f})",
                amount=ts_round(pricing.guest_rate * guests * factor),
            )
        )
    return lines


def _duration_lines(
    facility: FacilitySnapshot, draft: BookingDraft, money: _Money
) -> list[PriceLine]:
    """Price lines for flexible-duration event bookings."""
    pricing = facility.pricing
    _, guests, _ = party_of(draft)
    mins = draft.end_min - draft.start_min
    factor, band = _mult_factor(facility, draft.start_min, draft.local_date)
    hour_rate = ts_round(effective_hour_rate(facility))
    detail = f"{fmt_hours(mins)} @ {money(hour_rate)}/hr{_rate_basis_note(facility, money)}"
    detail += _factor_suffix(facility, factor, band, draft.local_date)
    lines = [
        PriceLine(
            label=f"{facility.name} hire",
            detail=detail,
            amount=ts_round(hour_rate * (mins / 60) * factor),
        )
    ]
    if pricing.cleaning_fee > 0:
        lines.append(
            PriceLine(label="Setup & cleaning", detail="Flat fee", amount=pricing.cleaning_fee)
        )
    guest_line = _guest_fee_line(facility, guests, money)
    if guest_line:
        lines.append(guest_line)
    return lines


def _room_lines(facility: FacilitySnapshot, draft: BookingDraft, money: _Money) -> list[PriceLine]:
    """Price lines for multi-night room stays."""
    nights = max(len(each_date(draft.local_date, draft.end_local_date)) - 1, 1)
    setup = facility.setup.room
    unit = next((unit for unit in facility.units if unit.id == draft.unit_id), None)
    room_type = unit.room_type if unit else None
    night_rates = setup.night_rates if setup else []
    exact = [
        tier for tier in night_rates if tier.room_type == room_type and nights >= tier.min_nights
    ]
    generic = [tier for tier in night_rates if not tier.room_type and nights >= tier.min_nights]
    tiers = sorted([*exact, *generic], key=lambda tier: -tier.min_nights)
    tier = tiers[0] if tiers else None
    season = None
    if setup:
        season = next(
            (
                season
                for season in setup.seasons
                if season.starts_on <= draft.local_date <= season.ends_on
            ),
            None,
        )
    base_rate = tier.rate if tier else ts_round(effective_day_rate(facility))
    rate = ts_round(base_rate * (season.multiplier if season else 1))
    notes: list[str] = []
    if tier and tier.min_nights > 1:
        notes.append(f"long-stay rate, {nights} nights")
    if room_type:
        notes.append(room_type)
    if season:
        notes.append(f"{season.name} ×{season.multiplier:g}")
    detail = f"{money(base_rate)}/night"
    if notes:
        detail += f" ({' · '.join(notes)})"
    return [
        PriceLine(
            label=f"{facility.name} — {nights} night{'s' if nights > 1 else ''}",
            detail=detail,
            amount=ts_round(rate * nights),
        )
    ]


def _day_range_lines(
    facility: FacilitySnapshot, draft: BookingDraft, money: _Money
) -> list[PriceLine]:
    """Price lines for day-range / venue hire bookings."""
    pricing = facility.pricing
    _, guests, _ = party_of(draft)
    hourly = (
        pricing.hourly_rate
        if pricing.hourly_rate is not None
        else ts_round(effective_hour_rate(facility))
    )
    day_rate = ts_round(effective_day_rate(facility))
    first_hours = facility.hours[js_dow(draft.local_date)]
    lines: list[PriceLine] = []
    if draft.end_local_date != draft.local_date:
        days = each_date(draft.local_date, draft.end_local_date)
        plural = "s" if len(days) > 1 else ""
        day_mins = draft.end_min - draft.start_min
        full_day = draft.start_min <= first_hours.open and draft.end_min >= max(
            first_hours.close, first_hours.open + 60
        )
        if full_day:
            total_days = sum(ts_round(day_rate * day_multiplier(facility, day)) for day in days)
            weekend_days = sum(1 for day in days if day_multiplier(facility, day) != 1)
            detail = f"{money(day_rate)}/day{_rate_basis_note(facility, money)}"
            if weekend_days > 0:
                detail += (
                    f" · {weekend_days} weekend/holiday day(s) ×{pricing.weekend_multiplier:g}"
                )
            lines.append(
                PriceLine(
                    label=f"{facility.name} hire — {len(days)} day{plural}, full day",
                    detail=detail,
                    amount=total_days,
                )
            )
        else:
            mult_sum = sum(day_multiplier(facility, day) for day in days)
            spread = (
                f"weekend/holiday days priced ×{pricing.weekend_multiplier:g}"
                if len(days) > 1
                else "single window across all days"
            )
            hire_label = (
                f"{facility.name} hire — {len(days)} day{plural} × {fmt_hours(day_mins)}/day"
            )
            lines.append(
                PriceLine(
                    label=hire_label,
                    detail=f"{money(hourly)}/hr · {spread}",
                    amount=ts_round(hourly * (day_mins / 60) * mult_sum),
                )
            )
    elif draft.start_min <= first_hours.open and draft.end_min >= first_hours.close:
        day_mult = day_multiplier(facility, draft.local_date)
        detail = f"{money(day_rate)}/day{_rate_basis_note(facility, money)}"
        if day_mult != 1:
            detail += f" (×{day_mult:g})"
        lines.append(
            PriceLine(
                label=f"{facility.name} hire — full day",
                detail=detail,
                amount=ts_round(day_rate * day_mult),
            )
        )
    else:
        mins = draft.end_min - draft.start_min
        billable = max(mins, (pricing.min_billable_hours or 0) * 60)
        factor, band = _mult_factor(facility, draft.start_min, draft.local_date)
        detail = fmt_hours(billable)
        if billable > mins:
            detail += f" (min {pricing.min_billable_hours} hrs)"
        detail += f" @ {money(hourly)}/hr"
        detail += _factor_suffix(facility, factor, band, draft.local_date)
        lines.append(
            PriceLine(
                label=f"{facility.name} hire — hourly",
                detail=detail,
                amount=ts_round(hourly * (billable / 60) * factor),
            )
        )
    if pricing.cleaning_fee > 0:
        lines.append(PriceLine(label="Cleaning", detail="Flat fee", amount=pricing.cleaning_fee))
    guest_line = _guest_fee_line(facility, guests, money)
    if guest_line:
        lines.append(guest_line)
    return lines


_LINE_BUILDERS = {
    SLOT: _slot_lines,
    TEE_TIME: _tee_lines,
    DURATION: _duration_lines,
    ROOM: _room_lines,
}


def is_billed_later(facility: FacilitySnapshot) -> bool:
    """Membership-excluded facilities bill on the periodic invoice instead of upfront."""
    return facility.setup.membership_enabled is True and (
        (facility.setup.membership_mode or FacilityMembershipMode.EXCLUDED)
        == FacilityMembershipMode.EXCLUDED
    )


def quote_booking(
    facility: FacilitySnapshot,
    draft: BookingDraft,
    currency_symbol: str = DEFAULT_CURRENCY_SYMBOL,
) -> PriceQuote:
    """Build a full price quote for ``draft`` at ``facility``."""
    if facility.setup.price_mode == FacilityPriceMode.INCLUDED:
        return _included_quote()
    money = _Money(currency_symbol)
    pricing = facility.pricing
    builder = _LINE_BUILDERS.get(facility.archetype, _day_range_lines)
    lines = builder(facility, draft, money)

    subtotal = _sum(lines)
    if pricing.tax_percent > 0:
        tax = ts_round((max(subtotal - pricing.deposit, 0) * pricing.tax_percent) / 100)
        if tax > 0:
            lines.append(
                PriceLine(
                    label=f"Tax ({pricing.tax_percent}% GST)",
                    detail="Applied on non-refundable charges",
                    amount=tax,
                )
            )
    total = _sum(lines)
    deposit = pricing.deposit
    if facility.policies.requires_approval:
        due_now, due_later = deposit, max(total - deposit, 0)
    elif is_billed_later(facility):
        due_now, due_later = 0, 0
    else:
        due_now, due_later = total, 0
    return PriceQuote(
        lines=lines, total=total, deposit=deposit, due_now=due_now, due_later=due_later
    )


def applicable_tier(facility: FacilitySnapshot, hours_before: float) -> CancellationTier:
    """Pick the cancellation tier matching ``hours_before`` start time."""
    tiers = sorted(facility.policies.cancellation_tiers, key=lambda tier: -tier.hours_before)
    for tier in tiers:
        if hours_before > tier.hours_before:
            return tier
    return tiers[-1]


def cancel_quote(
    facility: FacilitySnapshot,
    reservation: EngineReservation,
    paid: int,
    now: datetime,
) -> CancelQuote:
    """Fee/refund for cancelling ``reservation`` now given the net amount already paid."""
    if reservation.status == FacilityReservationStatus.PENDING_APPROVAL.value:
        return CancelQuote(tier=None, hours_before=0, paid=0, fee=0, refund=0, free=True)
    hours_before = (reservation_start(reservation) - now).total_seconds() / 3600
    tier = applicable_tier(facility, hours_before)
    fee = ts_round((paid * tier.fee_percent) / 100)
    return CancelQuote(
        tier=tier, hours_before=hours_before, paid=paid, fee=fee, refund=paid - fee, free=False
    )


def net_paid(entries: Iterable[tuple[str, int]]) -> int:
    """Sum (entry_type, amount) pairs that count as paid towards a reservation."""
    return sum(amount for entry_type, amount in entries if entry_type in PAID_ENTRY_TYPES)


def reschedule_adjustment(old_quote: PriceQuote, new_quote: PriceQuote) -> int:
    """Signed delta between two quotes when rescheduling."""
    return ts_round(new_quote.total - old_quote.total)
