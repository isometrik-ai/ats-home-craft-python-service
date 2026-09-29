"""Unit tests for facility booking default configuration helpers."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityMembershipMode,
    FacilityPriceMode,
    FacilityType,
)
from apps.user_service.app.schemas.facility_booking_config import (
    FacilitySetup,
    SlotSetup,
)
from apps.user_service.app.services.facility_booking.defaults import (
    DEFAULT_ROOM_BLOCK_CATEGORIES,
    default_booking_config,
    ensure_archetype_setup,
    normalize_setup,
    suggested_archetype,
)


def test_suggested_archetype_maps_facility_types() -> None:
    assert suggested_archetype(FacilityType.SPORTS.value) == FacilityBookingArchetype.SLOT
    assert suggested_archetype(FacilityType.EVENTS.value) == FacilityBookingArchetype.DAY_RANGE
    assert suggested_archetype(None) == FacilityBookingArchetype.SLOT
    assert suggested_archetype("unknown") == FacilityBookingArchetype.SLOT


def test_default_booking_config_covers_each_archetype() -> None:
    for kind in FacilityBookingArchetype:
        cfg = default_booking_config(kind)
        assert cfg.slot_minutes > 0
        assert len(cfg.default_hours) == 7
        assert cfg.pricing.unit_label is not None
        assert cfg.policies.cancellation_tiers
        assert cfg.setup.price_mode == FacilityPriceMode.FIXED


def test_default_booking_config_room_includes_block_categories() -> None:
    cfg = default_booking_config(FacilityBookingArchetype.ROOM)
    assert cfg.setup.room is not None
    assert cfg.setup.room.block_categories == list(DEFAULT_ROOM_BLOCK_CATEGORIES)


def test_normalize_setup_derives_price_mode_from_toggles() -> None:
    fixed = normalize_setup(FacilitySetup(fixed_enabled=True))
    assert fixed.price_mode == FacilityPriceMode.FIXED

    included = normalize_setup(
        FacilitySetup(membership_enabled=True, membership_mode=FacilityMembershipMode.INCLUDED)
    )
    assert included.price_mode == FacilityPriceMode.INCLUDED

    rule = normalize_setup(FacilitySetup(rule_based_enabled=True))
    assert rule.price_mode == FacilityPriceMode.RULE_BASED

    legacy = normalize_setup(FacilitySetup(price_mode=FacilityPriceMode.INCLUDED))
    assert legacy.membership_enabled is True
    assert legacy.membership_mode == FacilityMembershipMode.INCLUDED


def test_ensure_archetype_setup_fills_missing_blocks() -> None:
    empty = FacilitySetup()
    slot = ensure_archetype_setup(FacilityBookingArchetype.SLOT, empty)
    assert slot.slot is not None
    assert slot.slot.durations

    golf = ensure_archetype_setup(FacilityBookingArchetype.TEE_TIME, FacilitySetup())
    assert golf.golf is not None

    event = ensure_archetype_setup(FacilityBookingArchetype.DURATION, FacilitySetup())
    assert event.event is not None

    room = ensure_archetype_setup(FacilityBookingArchetype.ROOM, FacilitySetup())
    assert room.room is not None

    existing = FacilitySetup(slot=SlotSetup(durations=[30], max_players=2))
    unchanged = ensure_archetype_setup(FacilityBookingArchetype.SLOT, existing)
    assert unchanged.slot.durations == [30]
