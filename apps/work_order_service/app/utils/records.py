"""Shared helpers for work_order_service."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def new_timeline_event(event: dict[str, Any]) -> dict[str, Any]:
    """Build a server-owned timeline event with id and timestamp."""
    return {
        "id": str(uuid4()),
        "at": datetime.now(timezone.utc).isoformat(),
        **event,
    }


def record_to_dict(row: Any) -> dict[str, Any]:
    """Convert asyncpg Record to JSON-serifiable dict."""
    if row is None:
        return {}
    out: dict[str, Any] = {}
    for key in row.keys():
        val = row[key]
        if isinstance(val, (datetime, date)):
            out[key] = val.isoformat()
        elif isinstance(val, UUID):
            out[key] = str(val)
        elif isinstance(val, (list, dict)):
            out[key] = val
        else:
            out[key] = val
    return out


def parse_json_field(value: Any) -> Any:
    """Parse JSON/JSONB field that may arrive as str."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value
