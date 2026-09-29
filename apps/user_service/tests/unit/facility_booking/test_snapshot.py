"""Unit tests for facility booking snapshot builders."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.schemas.enums import FacilityBookingArchetype
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.services.facility_booking.snapshot import (
    engine_reservation,
    snapshot_from_rows,
    week_bounds,
)

FACILITY_ID = "33333333-3333-3333-3333-333333333333"
RES_ID = "77777777-7777-7777-7777-777777777777"
HOST_CONTACT_ID = "88888888-8888-8888-8888-888888888888"


def _minimal_config() -> dict:
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    return {
        "facility_id": FACILITY_ID,
        "facility_name": "Club Pool",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": defaults.slot_minutes,
        "default_hours": [item.model_dump() for item in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "description": "Outdoor pool",
        "accepting_bookings": False,
    }


def test_snapshot_from_rows_builds_facility_snapshot() -> None:
    """Config and inventory rows assemble a FacilitySnapshot."""
    config = _minimal_config()
    inventory = {
        "facility_closures": [{"closed_on": date(2026, 1, 1), "reason": "New Year"}],
        "facility_booking_units": [
            {
                "id": "unit-1",
                "name": "Lane 1",
                "room_type": None,
                "features": ["heated"],
                "tower_id": None,
                "floor_id": None,
                "sort_order": 1,
                "active": True,
            },
            {
                "id": "unit-inactive",
                "name": "Closed lane",
                "active": False,
            },
        ],
        "facility_maintenance_windows": [
            {
                "id": "mw-1",
                "on_date": date(2026, 2, 1),
                "from_min": 600,
                "to_min": 720,
                "note": "Filter service",
            }
        ],
        "facility_schedule_periods": [
            {
                "id": "sched-1",
                "name": "Summer",
                "starts_on": date(2026, 6, 1),
                "ends_on": date(2026, 8, 31),
                "hours": config["default_hours"],
            }
        ],
        "facility_slot_blocks": [
            {
                "id": "block-1",
                "starts_on": date(2026, 3, 1),
                "ends_on": None,
                "from_min": 540,
                "to_min": 600,
                "reason": "Private event",
                "unit_id": "unit-1",
                "category": "Private event",
            }
        ],
    }

    snapshot = snapshot_from_rows(config, inventory)

    assert snapshot.id == FACILITY_ID
    assert snapshot.name == "Club Pool"
    assert snapshot.description == "Outdoor pool"
    assert snapshot.accepting_bookings is False
    assert len(snapshot.units) == 1
    assert snapshot.units[0].name == "Lane 1"
    assert snapshot.closures[date(2026, 1, 1)] == "New Year"
    assert snapshot.maintenance[0].note == "Filter service"
    assert snapshot.schedules[0].name == "Summer"
    assert snapshot.slot_blocks[0].unit_id == "unit-1"


def test_engine_reservation_maps_row_and_participants() -> None:
    """Reservation rows map to EngineReservation with optional participants."""
    row = {
        "id": RES_ID,
        "facility_id": FACILITY_ID,
        "unit_id": None,
        "local_date": date(2026, 4, 10),
        "end_local_date": date(2026, 4, 10),
        "start_min": 600,
        "end_min": 660,
        "host_contact_id": HOST_CONTACT_ID,
        "status": "confirmed",
        "host_name": "Alex Resident",
        "quote": {"total": 500},
    }
    participants = [
        {"kind": "guest", "name": "Guest One", "contact_id": None},
        {"kind": "member", "name": "Member", "contact_id": HOST_CONTACT_ID},
    ]

    reservation = engine_reservation(row, participants)

    assert reservation.id == RES_ID
    assert reservation.quote_total == 500
    assert reservation.host_name == "Alex Resident"
    assert len(reservation.participants) == 2
    assert reservation.participants[0].kind == "guest"


def test_engine_reservation_defaults_missing_quote() -> None:
    """Missing quote data defaults total to zero."""
    row = {
        "id": RES_ID,
        "facility_id": FACILITY_ID,
        "local_date": date(2026, 4, 10),
        "end_local_date": date(2026, 4, 10),
        "start_min": 0,
        "end_min": 60,
        "host_contact_id": HOST_CONTACT_ID,
        "status": "pending",
    }

    assert engine_reservation(row).quote_total == 0


@pytest.mark.parametrize(
    ("local_date", "expected_start", "expected_end"),
    [
        (date(2026, 4, 15), date(2026, 4, 13), date(2026, 4, 19)),
        (date(2026, 4, 13), date(2026, 4, 13), date(2026, 4, 19)),
    ],
)
def test_week_bounds_monday_through_sunday(
    local_date: date, expected_start: date, expected_end: date
) -> None:
    """Week bounds use Monday as the first day."""
    start, end = week_bounds(local_date)
    assert start == expected_start
    assert end == expected_end
