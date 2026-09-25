"""Default booking configuration per archetype (used when a facility becomes bookable)."""

from __future__ import annotations

from dataclasses import dataclass

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityMembershipMode,
    FacilityPriceMode,
    FacilityRateUnit,
    FacilityType,
)
from apps.user_service.app.schemas.facility_booking_config import (
    CancellationTier,
    DayHours,
    EventSetup,
    FacilitySetup,
    GolfSetup,
    NightRate,
    Policies,
    PricingConfig,
    RoomSetup,
    SlotSetup,
)

DEFAULT_ROOM_BLOCK_CATEGORIES = ["Maintenance", "Group hold", "Owner stay", "Private event"]

_SUGGESTED_ARCHETYPES: dict[str, FacilityBookingArchetype] = {
    FacilityType.SPORTS.value: FacilityBookingArchetype.SLOT,
    FacilityType.RECREATION.value: FacilityBookingArchetype.DURATION,
    FacilityType.EVENTS.value: FacilityBookingArchetype.DAY_RANGE,
    FacilityType.SERVICES.value: FacilityBookingArchetype.DURATION,
}


@dataclass(frozen=True, slots=True)
class DefaultBookingConfig:
    """Default slot length, hours, pricing, policies and setup for one archetype."""

    slot_minutes: int
    default_hours: list[DayHours]
    pricing: PricingConfig
    policies: Policies
    setup: FacilitySetup


def suggested_archetype(facility_type: str | None) -> FacilityBookingArchetype:
    """Map a facility type string to the recommended booking archetype."""
    return _SUGGESTED_ARCHETYPES.get(facility_type or "", FacilityBookingArchetype.SLOT)


def _week(open_min: int, close_min: int) -> list[DayHours]:
    """Build seven identical ``DayHours`` entries (Sun–Sat)."""
    return [DayHours(open=open_min, close=close_min, closed=False) for _ in range(7)]


def _tiers(*rows: tuple[str, int, int]) -> list[CancellationTier]:
    """Build cancellation tiers from ``(label, hours_before, fee_percent)`` tuples."""
    return [
        CancellationTier(label=label, hours_before=hours_before, fee_percent=fee_percent)
        for label, hours_before, fee_percent in rows
    ]


def default_booking_config(archetype: FacilityBookingArchetype | str) -> DefaultBookingConfig:
    """Return the starter booking config for ``archetype``."""
    kind = FacilityBookingArchetype(archetype)
    if kind == FacilityBookingArchetype.SLOT:
        return DefaultBookingConfig(
            slot_minutes=60,
            default_hours=_week(6 * 60, 22 * 60),
            pricing=PricingConfig(unit_label=FacilityRateUnit.HOUR),
            policies=Policies(
                advance_booking_days=7,
                cancellation_tiers=_tiers(("Free cancellation", 24, 0), ("Non-refundable", 0, 100)),
                reschedule_cutoff_hours=12,
                buffer_minutes=0,
                weekly_cap=0,
                no_show_fee_percent=50,
                min_duration_min=60,
                max_duration_min=120,
            ),
            setup=FacilitySetup(
                price_mode=FacilityPriceMode.FIXED,
                fixed_enabled=True,
                slot=SlotSetup(durations=[60], max_players=4, lead_time_minutes=15),
            ),
        )
    if kind == FacilityBookingArchetype.TEE_TIME:
        return DefaultBookingConfig(
            slot_minutes=10,
            default_hours=_week(6 * 60, 18 * 60),
            pricing=PricingConfig(unit_label=FacilityRateUnit.PLAYER),
            policies=Policies(
                advance_booking_days=7,
                cancellation_tiers=_tiers(("Free cancellation", 24, 0), ("Non-refundable", 0, 100)),
                reschedule_cutoff_hours=12,
                no_show_fee_percent=50,
                min_duration_min=60,
                max_duration_min=240,
            ),
            setup=FacilitySetup(
                price_mode=FacilityPriceMode.FIXED,
                fixed_enabled=True,
                golf=GolfSetup(),
            ),
        )
    if kind == FacilityBookingArchetype.DURATION:
        return DefaultBookingConfig(
            slot_minutes=30,
            default_hours=_week(10 * 60, 23 * 60),
            pricing=PricingConfig(unit_label=FacilityRateUnit.HOUR),
            policies=Policies(
                requires_approval=True,
                advance_booking_days=30,
                cancellation_tiers=_tiers(
                    ("Free cancellation", 72, 0),
                    ("25% cancellation fee", 48, 25),
                    ("Non-refundable", 0, 100),
                ),
                reschedule_cutoff_hours=48,
                buffer_minutes=30,
                no_show_fee_percent=25,
                min_duration_min=120,
                max_duration_min=300,
            ),
            setup=FacilitySetup(
                price_mode=FacilityPriceMode.FIXED,
                fixed_enabled=True,
                event=EventSetup(
                    allows_hourly=True,
                    allows_full_day=False,
                    allows_multi_day=False,
                    max_participants=22,
                ),
            ),
        )
    if kind == FacilityBookingArchetype.DAY_RANGE:
        return DefaultBookingConfig(
            slot_minutes=60,
            default_hours=_week(8 * 60, 22 * 60),
            pricing=PricingConfig(unit_label=FacilityRateUnit.DAY, min_billable_hours=4),
            policies=Policies(
                requires_approval=True,
                advance_booking_days=90,
                cancellation_tiers=_tiers(
                    ("Free cancellation", 168, 0),
                    ("50% cancellation fee", 48, 50),
                    ("Non-refundable", 0, 100),
                ),
                reschedule_cutoff_hours=168,
                buffer_minutes=60,
                no_show_fee_percent=25,
                min_duration_min=0,
                max_duration_min=0,
            ),
            setup=FacilitySetup(
                price_mode=FacilityPriceMode.FIXED,
                fixed_enabled=True,
                event=EventSetup(
                    allows_hourly=True,
                    allows_full_day=True,
                    allows_multi_day=True,
                    max_participants=300,
                ),
            ),
        )
    return DefaultBookingConfig(
        slot_minutes=60,
        default_hours=_week(0, 24 * 60),
        pricing=PricingConfig(unit_label=FacilityRateUnit.DAY),
        policies=Policies(
            requires_approval=True,
            advance_booking_days=90,
            cancellation_tiers=_tiers(("Free cancellation", 72, 0), ("Non-refundable", 24, 100)),
            reschedule_cutoff_hours=24,
            no_show_fee_percent=20,
            min_duration_min=0,
            max_duration_min=0,
        ),
        setup=FacilitySetup(
            price_mode=FacilityPriceMode.FIXED,
            fixed_enabled=True,
            room=RoomSetup(
                min_nights=1,
                night_rates=[NightRate(min_nights=1, rate=0)],
                room_types=["Standard"],
                block_categories=list(DEFAULT_ROOM_BLOCK_CATEGORIES),
            ),
        ),
    )


def normalize_setup(setup: FacilitySetup) -> FacilitySetup:
    """Derive ``price_mode`` from the pricing toggles, as the setup workspace does."""
    membership_on = (
        setup.membership_enabled
        if setup.membership_enabled is not None
        else setup.price_mode == FacilityPriceMode.INCLUDED
    )
    membership_mode = setup.membership_mode or (
        FacilityMembershipMode.INCLUDED
        if setup.price_mode == FacilityPriceMode.INCLUDED
        else FacilityMembershipMode.EXCLUDED
    )
    rule_on = (
        setup.rule_based_enabled
        if setup.rule_based_enabled is not None
        else setup.price_mode == FacilityPriceMode.RULE_BASED
    )
    if membership_on and membership_mode == FacilityMembershipMode.INCLUDED:
        price_mode = FacilityPriceMode.INCLUDED
    elif rule_on:
        price_mode = FacilityPriceMode.RULE_BASED
    else:
        price_mode = FacilityPriceMode.FIXED
    return setup.model_copy(
        update={
            "price_mode": price_mode,
            "membership_enabled": membership_on,
            "membership_mode": membership_mode,
        }
    )


def ensure_archetype_setup(
    archetype: FacilityBookingArchetype | str, setup: FacilitySetup
) -> FacilitySetup:
    """Fill the archetype's setup block with defaults when missing."""
    kind = FacilityBookingArchetype(archetype)
    defaults = default_booking_config(kind).setup
    updates: dict[str, object] = {}
    if kind == FacilityBookingArchetype.SLOT and setup.slot is None:
        updates["slot"] = defaults.slot
    elif kind == FacilityBookingArchetype.TEE_TIME and setup.golf is None:
        updates["golf"] = defaults.golf
    elif (
        kind in (FacilityBookingArchetype.DURATION, FacilityBookingArchetype.DAY_RANGE)
        and setup.event is None
    ):
        updates["event"] = defaults.event
    elif kind == FacilityBookingArchetype.ROOM and setup.room is None:
        updates["room"] = defaults.room
    return setup.model_copy(update=updates) if updates else setup
