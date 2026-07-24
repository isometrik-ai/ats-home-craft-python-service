"""Unit tests for project row serialization helpers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from apps.user_service.app.utils.project_serialization import (
    serialize_row,
    serialize_value,
)


def test_serialize_value_handles_primitives() -> None:
    """Serialization should coerce UUID, Decimal, and date types."""
    uid = uuid.uuid4()
    assert serialize_value(uid) == str(uid)
    assert serialize_value(Decimal("12.5")) == 12.5
    assert serialize_value(date(2026, 7, 1)) == "2026-07-01"
    assert serialize_value(datetime(2026, 7, 1, 12, 0, 0)) == "2026-07-01T12:00:00"


def test_serialize_value_nested_structures() -> None:
    """Nested lists and dicts should serialize recursively."""
    payload = {
        "ids": [uuid.UUID("550e8400-e29b-41d4-a716-446655440000")],
        "meta": {"amount": Decimal("1.5")},
    }
    result = serialize_value(payload)
    assert result["ids"] == ["550e8400-e29b-41d4-a716-446655440000"]
    assert result["meta"]["amount"] == 1.5


def test_serialize_row() -> None:
    """Rows should serialize every column value."""
    row = {"id": uuid.uuid4(), "name": "Tower A", "count": 3}
    serialized = serialize_row(row)
    assert serialized["name"] == "Tower A"
    assert serialized["count"] == 3
    assert isinstance(serialized["id"], str)
