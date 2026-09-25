"""Shared validation helpers for project setup payloads."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.schemas.enums import (
    NON_BOOKABLE_FACILITY_TYPES,
    FacilityBookingArchetype,
    FacilityLocationType,
    FacilityType,
    ParkingFacilitySubtype,
    ParkingUserType,
    ParkingVehicleCategory,
    UnitNumberingPattern,
)
from apps.user_service.app.services.facility_booking.defaults import suggested_archetype
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_PARKING_FACILITY_SUBTYPE_ALIASES: dict[str, str] = {
    "covered": ParkingFacilitySubtype.COVERED.value,
    "open": ParkingFacilitySubtype.OPEN.value,
    "basement": ParkingFacilitySubtype.BASEMENT.value,
    "stilt": ParkingFacilitySubtype.STILT.value,
    "podium": ParkingFacilitySubtype.PODIUM.value,
    "ev_charging": ParkingFacilitySubtype.EV_CHARGING.value,
    "ev": ParkingFacilitySubtype.EV_CHARGING.value,
}


def normalize_facility_type(facility_type: str | FacilityType | None) -> str:
    """Normalize facility type for conditional validation."""
    if isinstance(facility_type, FacilityType):
        return facility_type.value
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


def normalize_parking_facility_subtype(
    value: str | None,
    *,
    required: bool = False,
) -> str | None:
    """Normalize parking facility subtype labels to canonical enum values."""
    if value is None or not str(value).strip():
        if required:
            raise ValidationException(
                message_key="project_setup.errors.facility_parking_subtype_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return None

    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    normalized = _PARKING_FACILITY_SUBTYPE_ALIASES.get(key)
    if not normalized:
        raise ValidationException(
            message_key="project_setup.errors.facility_parking_subtype_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return normalized


def _has_parking_numbering_fields(data: dict[str, Any]) -> bool:
    """Return True when parking numbering fields are explicitly set."""
    return any(
        data.get(field) is not None
        for field in ("numbering_pattern", "starting_slots_number", "custom_prefix")
    )


def validate_facility_payload(
    data: dict[str, Any],
    *,
    tower_has_wings: bool | None = None,
) -> None:
    """Validate conditional facility fields based on type and location."""
    # pylint: disable=too-complex
    facility_type = normalize_facility_type(data.get("facility_type"))
    validate_booking_flags(data)
    location_type = data.get("location_type")
    if isinstance(location_type, FacilityLocationType):
        location_type = location_type.value

    if (
        location_type == FacilityLocationType.IN_TOWER.value
        and not data.get("wing")
        and tower_has_wings is not False
    ):
        raise ValidationException(
            message_key="project_setup.errors.facility_wing_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    if facility_type == FacilityType.EVENTS.value:
        capacity = data.get("capacity_persons")
        if capacity is None or int(capacity) <= 0:
            raise ValidationException(
                message_key="project_setup.errors.facility_capacity_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    if facility_type == FacilityType.PARKING.value:
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
        parking_vehicle_category = data.get("parking_vehicle_category")
        if isinstance(parking_vehicle_category, ParkingVehicleCategory):
            parking_vehicle_category = parking_vehicle_category.value
        if not parking_vehicle_category:
            raise ValidationException(
                message_key="project_setup.errors.facility_parking_vehicle_category_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        starting_slots_number = data.get("starting_slots_number")
        if starting_slots_number is not None and int(starting_slots_number) < 1:
            raise ValidationException(
                message_key="project_setup.errors.facility_starting_slot_number_invalid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        numbering_pattern = data.get("numbering_pattern")
        if isinstance(numbering_pattern, UnitNumberingPattern):
            numbering_pattern = numbering_pattern.value
        validate_tower_numbering(
            numbering_pattern=str(numbering_pattern or UnitNumberingPattern.FLOOR_UNIT.value),
            custom_prefix=data.get("custom_prefix"),
        )
        data["facility_subtype"] = normalize_parking_facility_subtype(
            data.get("facility_subtype"),
            required=True,
        )
        return

    if _has_parking_numbering_fields(data):
        raise ValidationException(
            message_key="project_setup.errors.facility_parking_numbering_not_applicable",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )


def validate_booking_flags(data: dict[str, Any]) -> None:
    """Bookable facilities need an archetype and cannot be parking/utility."""
    is_bookable = bool(data.get("is_bookable"))
    facility_type = normalize_facility_type(data.get("facility_type"))
    archetype = data.get("booking_archetype")
    if isinstance(archetype, FacilityBookingArchetype):
        archetype = archetype.value
        data["booking_archetype"] = archetype
    if not is_bookable:
        return
    if facility_type in NON_BOOKABLE_FACILITY_TYPES:
        raise ValidationException(
            message_key="project_setup.errors.facility_not_bookable_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    if not archetype:
        data["booking_archetype"] = suggested_archetype(facility_type).value
