"""Parking slot label generation from facility numbering settings."""

from __future__ import annotations

import re

from apps.user_service.app.schemas.enums import UnitNumberingPattern

_FLOOR_DIGITS_RE = re.compile(r"\d+")


def _parse_floor_number(floor_level: str | None) -> int | None:
    """Extract a numeric floor index from a floor label when possible."""
    if not floor_level:
        return None
    text = floor_level.strip()
    if text.isdigit():
        return int(text)
    upper = text.upper()
    if upper.startswith("G") and not upper.startswith("GAR"):
        match = _FLOOR_DIGITS_RE.search(text)
        return int(match.group()) if match else 0
    if re.match(r"^[A-Za-z]", text):
        return None
    match = _FLOOR_DIGITS_RE.search(text)
    return int(match.group()) if match else None


def format_parking_slot_code(
    *,
    slot_number: int,
    numbering_pattern: str,
    custom_prefix: str | None = None,
    floor_level: str | None = None,
    sequence_index: int,
    starting_slots_number: int = 1,
) -> str:
    """Build the display label for one parking slot."""
    pattern = (numbering_pattern or UnitNumberingPattern.FLOOR_UNIT.value).strip().lower()

    if pattern == UnitNumberingPattern.CUSTOM.value:
        prefix = (custom_prefix or "").strip()
        if not prefix:
            return str(slot_number)
        separator = "" if prefix.endswith("-") else "-"
        return f"{prefix}{separator}{starting_slots_number + sequence_index}"

    if pattern == UnitNumberingPattern.SEQUENTIAL.value:
        return str(slot_number)

    floor_number = _parse_floor_number(floor_level)
    unit_index = starting_slots_number + sequence_index
    if floor_number is not None:
        return str(floor_number * 100 + unit_index)

    floor_label = (floor_level or "").strip()
    if floor_label:
        return f"{floor_label}-{unit_index:02d}"

    return str(slot_number)


def build_parking_slot_pairs(
    *,
    slot_count: int,
    starting_slots_number: int = 1,
    numbering_pattern: str = UnitNumberingPattern.FLOOR_UNIT.value,
    custom_prefix: str | None = None,
    floor_level: str | None = None,
) -> list[tuple[int, str]]:
    """Return (slot_number, slot_code) pairs for bulk slot provisioning."""
    if slot_count <= 0:
        return []

    pairs: list[tuple[int, str]] = []
    for index in range(slot_count):
        slot_number = starting_slots_number + index
        slot_code = format_parking_slot_code(
            slot_number=slot_number,
            numbering_pattern=numbering_pattern,
            custom_prefix=custom_prefix,
            floor_level=floor_level,
            sequence_index=index,
            starting_slots_number=starting_slots_number,
        )
        pairs.append((slot_number, slot_code))
    return pairs
