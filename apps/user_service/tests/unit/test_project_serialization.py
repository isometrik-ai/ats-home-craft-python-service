"""Unit tests for project row serialization helpers."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from apps.user_service.app.utils.project_serialization import (
    serialize_facility_row,
    serialize_row,
    serialize_value,
)


class _ParkingCategory(Enum):
    TWO_WHEELER = "two_wheeler"


def test_serialize_value_handles_primitives_and_collections():
    uid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    when = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
    payload = {
        "id": uid,
        "amount": Decimal("12.50"),
        "created_at": when,
        "effective_from": date(2026, 3, 1),
        "tags": ("a", "b"),
        "meta": {"nested": [1, Decimal("2.5")]},
        "plain": "keep-me",
    }

    out = serialize_value(payload)

    assert out["id"] == str(uid)
    assert out["amount"] == 12.5
    assert out["created_at"] == when.isoformat()
    assert out["effective_from"] == "2026-03-01"
    assert out["tags"] == ["a", "b"]
    assert out["meta"] == {"nested": [1, 2.5]}
    assert out["plain"] == "keep-me"


def test_serialize_value_parses_json_strings():
    assert serialize_value('{"a": 1}') == {"a": 1}
    assert serialize_value("[1, 2]") == [1, 2]
    assert serialize_value("not-json") == "not-json"
    assert serialize_value("{bad json") == "{bad json"


def test_serialize_value_none_and_unknown_types():
    assert serialize_value(None) is None

    class Custom:
        def __repr__(self) -> str:
            return "Custom()"

    assert serialize_value(Custom())  # falls through to return value


def test_serialize_row():
    row = {"id": uuid.uuid4(), "name": "Tower A"}
    out = serialize_row(row)
    assert out["name"] == "Tower A"
    assert isinstance(out["id"], str)


def test_serialize_facility_row_includes_null_parking_vehicle_category():
    row = {
        "id": "facility-1",
        "name": "Clubhouse",
        "facility_type": "recreation",
        "parking_vehicle_category": None,
        "parking_user_type": None,
    }

    result = serialize_facility_row(row)

    assert result["parking_vehicle_category"] is None
    assert result["parking_user_type"] is None


def test_serialize_facility_row_normalizes_parking_vehicle_category():
    row = {
        "id": "facility-1",
        "name": "Visitor Parking",
        "facility_type": "parking",
        "parking_vehicle_category": "two_wheeler",
        "parking_user_type": "residents",
    }

    result = serialize_facility_row(row)

    assert result["parking_vehicle_category"] == "two_wheeler"
    assert result["parking_user_type"] == "residents"


def test_serialize_facility_row_enum_values():
    row = {
        "id": "facility-1",
        "name": "Basement Parking",
        "facility_type": "parking",
        "parking_vehicle_category": _ParkingCategory.TWO_WHEELER,
        "parking_user_type": "visitors",
    }

    result = serialize_facility_row(row)

    assert result["parking_vehicle_category"] == "two_wheeler"
    assert result["parking_user_type"] == "visitors"
