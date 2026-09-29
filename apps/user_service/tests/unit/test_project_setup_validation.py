"""Unit tests for project setup conditional validation."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityLocationType,
    FacilityType,
)
from apps.user_service.app.services.project_setup_validation import (
    normalize_facility_type,
    normalize_parking_facility_subtype,
    validate_booking_flags,
    validate_facility_payload,
    validate_tower_numbering,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_validate_tower_custom_prefix_required():
    """Custom numbering pattern requires custom_prefix."""
    with pytest.raises(ValidationException):
        validate_tower_numbering(numbering_pattern="custom", custom_prefix=None)


def test_validate_facility_wing_for_in_tower():
    """Indoor tower facilities require wing when the tower has wings."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "sports",
                "location_type": FacilityLocationType.IN_TOWER.value,
            },
            tower_has_wings=True,
        )


def test_validate_facility_wing_optional_when_tower_has_no_wings():
    """Wing is optional for in_tower facilities on wingless towers."""
    validate_facility_payload(
        {
            "facility_type": "sports",
            "location_type": FacilityLocationType.IN_TOWER.value,
            "tower_id": "tower-1",
        },
        tower_has_wings=False,
    )


def test_validate_facility_events_capacity():
    """Event facilities require capacity_persons."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "events",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
            }
        )


def test_validate_facility_parking_fields():
    """Parking facilities require slots and parking_user_type."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 10,
            }
        )


def test_validate_facility_parking_numbering_defaults():
    """Parking facilities accept default numbering pattern and starting slot."""
    validate_facility_payload(
        {
            "facility_type": "parking",
            "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
            "parking_slots": 10,
            "parking_user_type": "visitors",
            "parking_vehicle_category": "four_wheeler",
            "facility_subtype": "open",
            "numbering_pattern": "floor_unit",
            "starting_slots_number": 1,
        }
    )


def test_validate_facility_parking_custom_prefix_required():
    """Custom parking numbering requires custom_prefix."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 10,
                "parking_user_type": "visitors",
                "parking_vehicle_category": "four_wheeler",
                "facility_subtype": "open",
                "numbering_pattern": "custom",
            }
        )


def test_validate_facility_parking_vehicle_category_required():
    """Parking facilities require parking_vehicle_category."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 10,
                "parking_user_type": "visitors",
            }
        )


def test_validate_facility_parking_subtype_required():
    """Parking facilities require a valid facility_subtype."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 10,
                "parking_user_type": "visitors",
                "parking_vehicle_category": "four_wheeler",
            }
        )


def test_validate_facility_parking_subtype_normalizes_ui_label():
    """Parking subtype labels from UI are normalized to canonical values."""
    data = {
        "facility_type": "parking",
        "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
        "parking_slots": 10,
        "parking_user_type": "visitors",
        "parking_vehicle_category": "four_wheeler",
        "facility_subtype": "EV Charging",
    }
    validate_facility_payload(data)
    assert data["facility_subtype"] == "ev_charging"


def test_validate_facility_numbering_not_applicable_for_non_parking():
    """Non-parking facilities reject numbering fields."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "sports",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "numbering_pattern": "sequential",
            }
        )


def test_validate_facility_parking_cannot_be_bookable():
    """Parking facilities cannot be marked bookable."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 10,
                "parking_user_type": "visitors",
                "parking_vehicle_category": "four_wheeler",
                "facility_subtype": "open",
                "is_bookable": True,
                "booking_archetype": "slot",
            }
        )


def test_validate_facility_bookable_defaults_archetype():
    """Bookable facilities without an archetype get a suggested default."""
    data = {
        "facility_type": "sports",
        "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
        "is_bookable": True,
    }
    validate_facility_payload(data)
    assert data["booking_archetype"] == "slot"


def test_normalize_facility_type_accepts_enum():
    """FacilityType enum values normalize to their string value."""
    assert normalize_facility_type(FacilityType.SPORTS) == "sports"


def test_normalize_parking_subtype_invalid_raises():
    """Unknown parking subtype labels are rejected."""
    with pytest.raises(ValidationException):
        normalize_parking_facility_subtype("not-a-real-subtype", required=True)


def test_validate_events_capacity_must_be_positive():
    """Event facilities reject zero capacity."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "events",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "capacity_persons": 0,
            }
        )


def test_validate_parking_slots_must_be_positive():
    """Parking facilities reject zero slots."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 0,
                "parking_user_type": "visitors",
                "parking_vehicle_category": "four_wheeler",
                "facility_subtype": "open",
            }
        )


def test_validate_parking_starting_slot_invalid():
    """Starting slot number must be at least 1 when provided."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "parking",
                "location_type": FacilityLocationType.OUTDOOR_STANDALONE.value,
                "parking_slots": 5,
                "parking_user_type": "visitors",
                "parking_vehicle_category": "four_wheeler",
                "facility_subtype": "open",
                "starting_slots_number": 0,
            }
        )


def test_validate_booking_flags_coerces_archetype_enum():
    """Bookable payload stores archetype enum as string."""
    data = {
        "facility_type": "sports",
        "is_bookable": True,
        "booking_archetype": FacilityBookingArchetype.ROOM,
    }
    validate_booking_flags(data)
    assert data["booking_archetype"] == "room"


def test_validate_facility_location_type_enum_wing_check():
    """FacilityLocationType enum is accepted for in_tower wing validation."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "sports",
                "location_type": FacilityLocationType.IN_TOWER,
            },
            tower_has_wings=True,
        )
