"""Unit tests for the Facilities template parser."""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityRateUnit,
    FacilityStatus,
    FacilityType,
    ParkingUserType,
    ParkingVehicleCategory,
)
from apps.user_service.app.services.project_workbook_parser import (
    parse_facilities_template,
)

HEADERS = [
    "Facility Name *",
    "Facility Type *",
    "Sport / Sub-type",
    "Tower / Block",
    "Floor",
    "Indoor / Outdoor",
    "Area (sq ft)",
    "Capacity (persons)",
    "Status *",
    "Location Notes",
    "No. of Slots",
    "Parking For",
    "Vehicle Type",
    "Can Residents Book? *",
    "Booking Type",
    "Slot Length (min)",
    "Opens",
    "Closes",
    "Resident Rate (₹)",
    "Guest Rate (₹)",
    "Rate Per",
    "GST %",
    "Deposit (₹)",
    "Max People per Booking",
    "Book Up To (days ahead)",
    "Needs Approval?",
    "Free Cancellation Until (hrs before)",
    "Late Cancellation Charge %",
    "Courts / Rooms Inside",
    "Other Rules / Notes",
]

_FIELDS = [
    "name",
    "facility_type",
    "subtype",
    "tower",
    "floor",
    "indoor_outdoor",
    "area",
    "capacity",
    "status",
    "location_notes",
    "parking_slots",
    "parking_for",
    "vehicle_type",
    "bookable",
    "booking_type",
    "slot_minutes",
    "opens",
    "closes",
    "resident_rate",
    "guest_rate",
    "rate_per",
    "tax_percent",
    "deposit",
    "max_people",
    "advance_days",
    "needs_approval",
    "free_cancel_hours",
    "late_cancel_percent",
    "units",
    "notes",
]


def cells(**overrides: object) -> list[object | None]:
    """Build one Facilities data row. Unset columns stay blank."""
    base: dict[str, object] = {
        "name": "Clubhouse",
        "facility_type": "Clubhouse",
        "indoor_outdoor": "Indoor",
        "status": "Active",
        "bookable": "No",
    }
    base.update(overrides)
    return [base.get(field) for field in _FIELDS]


def workbook_bytes(*data_rows: list[object | None], headers: list[str] | None = None) -> bytes:
    """Build an xlsx with the template header rows and the given data rows."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Facilities"
    sheet.append(["FACILITY DETAILS"])
    sheet.append(headers or HEADERS)
    sheet.append(["hint"])
    for data_row in data_rows:
        sheet.append(data_row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_clubhouse_row_ignores_parking_columns():
    """Parking cells on a non-parking facility are not part of the facility."""
    rows, errors, fatal = parse_facilities_template(
        workbook_bytes(
            cells(
                parking_slots=558,
                parking_for="Residents",
                vehicle_type="Four-wheeler",
                location_notes="Basement covered",
                capacity=200,
            )
        )
    )

    assert fatal is None
    assert errors == []
    row = rows[0]
    assert row["facility_type"] == FacilityType.RECREATION
    assert row["parking_slots"] is None
    assert row["parking_user_type"] is None
    assert row["capacity_persons"] == 200
    assert row["location_notes"] == "Basement covered"
    assert row["is_bookable"] is False
    assert row["indoor"] is True


def test_under_construction_maps_to_under_maintenance():
    """The template status Under Construction is stored as under maintenance."""
    rows, _, _ = parse_facilities_template(workbook_bytes(cells(status="Under Construction")))

    assert rows[0]["status"] == FacilityStatus.UNDER_MAINTENANCE


def test_parking_row_maps_slots_and_rejects_staff():
    """Parking rows require slots, audience, and vehicle type."""
    rows, _, _ = parse_facilities_template(
        workbook_bytes(
            cells(
                name="Basement",
                facility_type="Parking",
                subtype="Basement",
                parking_slots=40,
                parking_for="Visitors",
                vehicle_type="Both",
            )
        )
    )
    _, rejected, _ = parse_facilities_template(
        workbook_bytes(
            cells(
                name="Staff lot",
                facility_type="Parking",
                subtype="Open",
                parking_slots=10,
                parking_for="Staff",
                vehicle_type="Two-wheeler",
            )
        )
    )

    row = rows[0]
    assert row["facility_type"] == FacilityType.PARKING
    assert row["parking_slots"] == 40
    assert row["parking_user_type"] == ParkingUserType.VISITORS
    assert row["parking_vehicle_category"] == ParkingVehicleCategory.BOTH
    assert row["parking_subtype_hint"] == "Basement"
    assert rejected[0].code == "choice_invalid"


def test_bookable_hourly_court_maps_rates_hours_and_units():
    """Booking columns become an archetype, rates, hours, and court names."""
    rows, errors, fatal = parse_facilities_template(
        workbook_bytes(
            cells(
                name="Tennis Court 1",
                facility_type="Sports Court",
                subtype="Tennis",
                indoor_outdoor="Outdoor",
                bookable="Yes",
                booking_type="Hourly slots",
                slot_minutes=60,
                opens="6:00 AM",
                closes="10:00 PM",
                resident_rate=500,
                guest_rate=800,
                rate_per="Hour",
                tax_percent=18,
                deposit=0,
                max_people=4,
                advance_days=14,
                needs_approval="No",
                free_cancel_hours=24,
                late_cancel_percent=100,
                units="Court A, Court B",
            )
        )
    )

    assert fatal is None
    assert errors == []
    row = rows[0]
    assert row["booking_archetype"] == FacilityBookingArchetype.SLOT
    assert row["facility_subtype"] == "Tennis"
    assert row["slot_minutes"] == 60
    assert row["opens_min"] == 6 * 60
    assert row["closes_min"] == 22 * 60
    assert row["resident_rate"] == 500
    assert row["rate_unit"] == FacilityRateUnit.HOUR
    assert row["requires_approval"] is False
    assert row["unit_names"] == ["Court A", "Court B"]
    assert row["advance_booking_days"] == 14


def test_duplicate_name_is_a_row_error():
    """The second row with the same facility name is rejected."""
    rows, errors, _ = parse_facilities_template(
        workbook_bytes(cells(name="Clubhouse"), cells(name="clubhouse"))
    )

    assert len(rows) == 1
    assert errors[0].code == "duplicate_name"
    assert errors[0].row_number == 5


def test_party_hall_requires_capacity_and_guest_room_defaults_to_overnight():
    """Event halls need capacity. Overnight stay maps a guest room to the room archetype."""
    _, missing_capacity, _ = parse_facilities_template(
        workbook_bytes(cells(name="Hall", facility_type="Party / Event Hall", bookable="Yes"))
    )
    rows, _, _ = parse_facilities_template(
        workbook_bytes(
            cells(
                name="Room 101",
                facility_type="Guest Room",
                bookable="Yes",
                booking_type="Overnight stay",
            )
        )
    )

    assert missing_capacity[0].code == "capacity_required"
    assert rows[0]["facility_type"] == FacilityType.SERVICES
    assert rows[0]["facility_subtype"] == "guest_room"
    assert rows[0]["booking_archetype"] == FacilityBookingArchetype.ROOM


def test_renamed_header_rejects_the_workbook():
    """A renamed required column fails the file before any row is imported."""
    headers = list(HEADERS)
    headers[0] = "Amenity *"
    rows, errors, fatal = parse_facilities_template(workbook_bytes(cells(), headers=headers))

    assert fatal == "facilities_imports.errors.headers_invalid"
    assert rows == []
    assert errors == []


def test_missing_sheet_and_unreadable_bytes():
    """The Facilities sheet is required, and a non-xlsx file is rejected."""
    book = Workbook()
    book.active.title = "Instructions"
    buffer = BytesIO()
    book.save(buffer)
    rows, errors, fatal = parse_facilities_template(buffer.getvalue())

    assert fatal == "facilities_imports.errors.sheet_missing"
    assert rows == []
    assert errors == []
    with pytest.raises(ValueError, match="invalid_workbook"):
        parse_facilities_template(b"not-an-xlsx")
