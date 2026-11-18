"""Read-time expiry for walk-in requests that stay awaiting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from apps.user_service.app.schemas.enums import WalkInStatus, WalkInVisitUnitStatus

# A delivery or guest waiting at the gate is not expected to stay past one day.
WALK_IN_AWAITING_TTL_HOURS = 24
WALK_IN_EXPIRED_STATUS = "expired"


def awaiting_expiry_interval_sql() -> str:
    """Postgres interval matching WALK_IN_AWAITING_TTL_HOURS."""
    return f"INTERVAL '{int(WALK_IN_AWAITING_TTL_HOURS)} hours'"


def _as_utc(value: Any) -> datetime | None:
    """Parse a timestamp from a DB value or ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def walk_in_awaiting_is_expired(
    requested_at: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True once an awaiting request is at least 24 hours old."""
    requested = _as_utc(requested_at)
    if requested is None:
        return False
    current = _as_utc(now) or datetime.now(timezone.utc)
    return requested + timedelta(hours=WALK_IN_AWAITING_TTL_HOURS) <= current


def effective_walk_in_header_status(
    status: str | None,
    requested_at: Any,
    *,
    now: datetime | None = None,
) -> str:
    """Header status shown to clients. Awaiting becomes expired after the TTL."""
    raw = str(status or "")
    if raw == WalkInStatus.AWAITING.value and walk_in_awaiting_is_expired(requested_at, now=now):
        return WALK_IN_EXPIRED_STATUS
    return raw


def effective_walk_in_visit_unit_status(
    status: str | None,
    requested_at: Any,
    *,
    now: datetime | None = None,
) -> str:
    """Per-flat status shown to clients. An untouched flat expires with the request."""
    raw = str(status or "")
    if raw == WalkInVisitUnitStatus.AWAITING.value and walk_in_awaiting_is_expired(
        requested_at,
        now=now,
    ):
        return WALK_IN_EXPIRED_STATUS
    return raw
