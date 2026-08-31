"""Unit tests for project row serialization helpers."""

from __future__ import annotations

from apps.user_service.app.utils.project_serialization import serialize_facility_row


def test_serialize_facility_row_includes_null_parking_vehicle_category():
    row = {
        "id": "facility-1",
        "name": "Clubhouse",
        "facility_type": "recreation",
        "parking_vehicle_category": None,
    }

    result = serialize_facility_row(row)

    assert result["parking_vehicle_category"] is None


def test_serialize_facility_row_normalizes_parking_vehicle_category():
    row = {
        "id": "facility-1",
        "name": "Visitor Parking",
        "facility_type": "parking",
        "parking_vehicle_category": "two_wheeler",
    }

    result = serialize_facility_row(row)

    assert result["parking_vehicle_category"] == "two_wheeler"
