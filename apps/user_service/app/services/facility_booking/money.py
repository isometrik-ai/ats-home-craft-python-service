"""Rounding, formatting and calendar helpers shared by the booking engines."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

DEFAULT_CURRENCY_SYMBOL = "₹"
MAX_DATE_SPAN_DAYS = 400


def ts_round(value: float) -> int:
    """Round half up (towards +infinity), matching JavaScript ``Math.round``."""
    return math.floor(value + 0.5)


def _indian_group(amount: int) -> str:
    """Format an integer with Indian-style thousands grouping (e.g. ``12,34,567``)."""
    digits = str(abs(amount))
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def fmt_money(amount: int, symbol: str = DEFAULT_CURRENCY_SYMBOL) -> str:
    """Format a signed whole-currency amount with the given symbol."""
    return f"{'-' if amount < 0 else ''}{symbol}{_indian_group(amount)}"


def fmt_hours(minutes: int) -> str:
    """Human-readable duration label, e.g. ``2 hrs`` or ``1h 30m``."""
    hours, mins = divmod(int(minutes), 60)
    if hours == 0:
        return f"{mins} min"
    if mins == 0:
        return "1 hr" if hours == 1 else f"{hours} hrs"
    return f"{hours}h {mins}m"


def fmt_clock(minutes: int) -> str:
    """Compact 12-hour clock label, e.g. ``6am`` or ``7:30pm``."""
    hours, mins = divmod(int(minutes), 60)
    suffix = "pm" if hours >= 12 else "am"
    h12 = hours % 12 or 12
    return f"{h12}{suffix}" if mins == 0 else f"{h12}:{mins:02d}{suffix}"


def js_dow(day: date) -> int:
    """Weekday index with Sunday = 0 .. Saturday = 6."""
    return (day.weekday() + 1) % 7


def each_date(start: date, end: date) -> list[date]:
    """Inclusive list of calendar dates from ``start`` through ``end``."""
    out: list[date] = []
    cur = start
    while cur <= end and len(out) < MAX_DATE_SPAN_DAYS:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def local_start(day: date, start_min: int) -> datetime:
    """Naive project-local datetime for a date + minute offset."""
    return datetime(day.year, day.month, day.day) + timedelta(minutes=start_min)
