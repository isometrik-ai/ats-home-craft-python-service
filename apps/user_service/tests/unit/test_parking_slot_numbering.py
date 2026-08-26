"""Unit tests for parking slot label generation."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import UnitNumberingPattern
from apps.user_service.app.utils.parking_slot_numbering import (
    build_parking_slot_pairs,
    format_parking_slot_code,
)


def test_custom_prefix_builds_prefixed_codes():
    pairs = build_parking_slot_pairs(
        slot_count=3,
        starting_slots_number=1,
        numbering_pattern=UnitNumberingPattern.CUSTOM.value,
        custom_prefix="SLT-A",
    )

    assert pairs == [(1, "SLT-A-1"), (2, "SLT-A-2"), (3, "SLT-A-3")]


def test_custom_prefix_with_trailing_hyphen():
    code = format_parking_slot_code(
        slot_number=5,
        numbering_pattern=UnitNumberingPattern.CUSTOM.value,
        custom_prefix="SLT-A-",
        sequence_index=4,
        starting_slots_number=1,
    )

    assert code == "SLT-A-5"


def test_sequential_uses_plain_slot_numbers():
    pairs = build_parking_slot_pairs(
        slot_count=2,
        starting_slots_number=100,
        numbering_pattern=UnitNumberingPattern.SEQUENTIAL.value,
    )

    assert pairs == [(100, "100"), (101, "101")]


def test_floor_unit_with_numeric_floor_level():
    pairs = build_parking_slot_pairs(
        slot_count=2,
        starting_slots_number=1,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT.value,
        floor_level="1",
    )

    assert pairs == [(1, "101"), (2, "102")]


def test_floor_unit_with_text_floor_level():
    pairs = build_parking_slot_pairs(
        slot_count=2,
        starting_slots_number=1,
        numbering_pattern=UnitNumberingPattern.FLOOR_UNIT.value,
        floor_level="B2",
    )

    assert pairs == [(1, "B2-01"), (2, "B2-02")]
