"""Serialization helpers for project setup DB rows -> JSON-friendly dicts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def serialize_value(value: Any) -> Any:
    """Coerce a single DB value to a JSON-serializable primitive."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(val) for key, val in value.items()}
    return value


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize an entire DB row dict for API responses."""
    return {key: serialize_value(val) for key, val in row.items()}
