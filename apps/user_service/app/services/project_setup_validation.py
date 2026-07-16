"""Shared validation helpers for project setup payloads."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.schemas.enums import (
    FacilityLocationType,
    ParkingUserType,
    UnitNumberingPattern,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


def normalize_facility_type(facility_type: str | None) -> str:
    """Normalize facility type for conditional validation."""
    return (facility_type or "").strip().lower()


def validate_tower_numbering(
    *,
    numbering_pattern: str,
    custom_prefix: str | None,
) -> None:
    """Require custom_prefix when numbering pattern is custom."""
    if numbering_pattern == UnitNumberingPattern.CUSTOM.value and not custom_prefix:
        raise ValidationException(
            message_key="project_setup.errors.custom_prefix_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )


def validate_facility_payload(data: dict[str, Any]) -> None:
    """Validate conditional facility fields based on type and location."""
    facility_type = normalize_facility_type(data.get("facility_type"))
    location_type = data.get("location_type")
    if isinstance(location_type, FacilityLocationType):
        location_type = location_type.value

    if location_type == FacilityLocationType.IN_TOWER.value and not data.get("wing"):
        raise ValidationException(
            message_key="project_setup.errors.facility_wing_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    if facility_type == "events":
        capacity = data.get("capacity_persons")
        if capacity is None or int(capacity) <= 0:
            raise ValidationException(
                message_key="project_setup.errors.facility_capacity_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    if facility_type == "parking":
        slots = data.get("parking_slots")
        if slots is None or int(slots) <= 0:
            raise ValidationException(
                message_key="project_setup.errors.facility_parking_slots_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        parking_user_type = data.get("parking_user_type")
        if isinstance(parking_user_type, ParkingUserType):
            parking_user_type = parking_user_type.value
        if not parking_user_type:
            raise ValidationException(
                message_key="project_setup.errors.facility_parking_user_type_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
