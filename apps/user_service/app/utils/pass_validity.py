"""Calendar-day helpers for pass validity windows."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

PASS_VALIDITY_TIMEZONE = ZoneInfo("Asia/Kolkata")


def pass_validity_local_date(
    dt: datetime,
    *,
    tz: ZoneInfo = PASS_VALIDITY_TIMEZONE,
) -> date:
    """Return the calendar date of a pass timestamp in the gate timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def is_pass_expired_by_day(
    valid_until: datetime | None,
    *,
    now: datetime,
    tz: ZoneInfo = PASS_VALIDITY_TIMEZONE,
) -> bool:
    """True when today is after the pass valid_until calendar day."""
    if valid_until is None:
        return False
    today = pass_validity_local_date(now, tz=tz)
    return today > pass_validity_local_date(valid_until, tz=tz)


def is_pass_too_early_by_day(
    valid_from: datetime | None,
    *,
    now: datetime,
    tz: ZoneInfo = PASS_VALIDITY_TIMEZONE,
) -> bool:
    """True when today is before the pass valid_from calendar day."""
    if valid_from is None:
        return False
    today = pass_validity_local_date(now, tz=tz)
    return today < pass_validity_local_date(valid_from, tz=tz)
