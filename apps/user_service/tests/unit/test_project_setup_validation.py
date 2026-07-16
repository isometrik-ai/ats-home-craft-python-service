"""Unit tests for project setup conditional validation."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.enums import FacilityLocationType
from apps.user_service.app.services.project_setup_validation import (
    validate_facility_payload,
    validate_tower_numbering,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_validate_tower_custom_prefix_required():
    """Custom numbering pattern requires custom_prefix."""
    with pytest.raises(ValidationException):
        validate_tower_numbering(numbering_pattern="custom", custom_prefix=None)


def test_validate_facility_wing_for_in_tower():
    """Indoor tower facilities require wing."""
    with pytest.raises(ValidationException):
        validate_facility_payload(
            {
                "facility_type": "sports",
                "location_type": FacilityLocationType.IN_TOWER.value,
            }
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
