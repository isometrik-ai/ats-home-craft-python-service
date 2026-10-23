"""Pure recurrence scheduling for notice series (project/org local time)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

NoticeRecurrenceFrequency = Literal["weekly", "monthly"]
NoticeSeriesEndType = Literal["on_date", "after_count"]

ISO_MAX_DAYS_AHEAD = 366 * 5


def _localize(org_tz: ZoneInfo, day: date, tod: time) -> datetime:
    """Combine date and wall time in org timezone."""
    return datetime(day.year, day.month, day.day, tod.hour, tod.minute, tod.second, tzinfo=org_tz)


def _to_utc(dt: datetime) -> datetime:
    """Normalize to UTC."""
    return dt.astimezone(timezone.utc)


def _monthly_candidate_day(year: int, month: int, monthly_day: int) -> date | None:
    """Return calendar day for monthly send; None if day invalid for month (29–31 skip)."""
    if monthly_day == 0:
        last = calendar.monthrange(year, month)[1]
        return date(year, month, last)
    if monthly_day > calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, monthly_day)


def compute_first_send_at(
    *,
    starts_on: date,
    time_of_day: time,
    frequency: NoticeRecurrenceFrequency,
    weekly_days: frozenset[int] | None,
    monthly_day: int,
    org_tz: ZoneInfo,
) -> datetime:
    """Earliest send on or after starts_on matching the pattern (UTC)."""
    if frequency == "weekly":
        if not weekly_days:
            raise ValueError("weekly_days required for weekly frequency")
        cursor = starts_on
        for _ in range(ISO_MAX_DAYS_AHEAD):
            if cursor.isoweekday() in weekly_days:
                candidate = _localize(org_tz, cursor, time_of_day)
                if candidate.date() >= starts_on:
                    return _to_utc(candidate)
            cursor += timedelta(days=1)
        raise ValueError("no weekly send found within search horizon")

    # monthly
    year, month = starts_on.year, starts_on.month
    for _ in range(ISO_MAX_DAYS_AHEAD // 28 + 2):
        candidate_date = _monthly_candidate_day(year, month, monthly_day)
        if candidate_date is not None and candidate_date >= starts_on:
            return _to_utc(_localize(org_tz, candidate_date, time_of_day))
        month += 1
        if month > 12:
            month = 1
            year += 1
    raise ValueError("no monthly send found within search horizon")


def compute_next_send_after(
    *,
    after_planned_at: datetime,
    time_of_day: time,
    frequency: NoticeRecurrenceFrequency,
    weekly_days: frozenset[int] | None,
    monthly_day: int,
    org_tz: ZoneInfo,
) -> datetime:
    """Next send strictly after after_planned_at (UTC)."""
    local_after = after_planned_at.astimezone(org_tz)
    start_day = local_after.date() + timedelta(days=1)

    if frequency == "weekly":
        if not weekly_days:
            raise ValueError("weekly_days required for weekly frequency")
        cursor = start_day
        for _ in range(ISO_MAX_DAYS_AHEAD):
            if cursor.isoweekday() in weekly_days:
                return _to_utc(_localize(org_tz, cursor, time_of_day))
            cursor += timedelta(days=1)
        raise ValueError("no next weekly send found")

    year, month = start_day.year, start_day.month
    for _ in range(ISO_MAX_DAYS_AHEAD // 28 + 2):
        candidate_date = _monthly_candidate_day(year, month, monthly_day)
        if candidate_date is not None and candidate_date >= start_day:
            return _to_utc(_localize(org_tz, candidate_date, time_of_day))
        month += 1
        if month > 12:
            month = 1
            year += 1
    raise ValueError("no next monthly send found")


def preview_sends(
    *,
    starts_on: date,
    time_of_day: time,
    frequency: NoticeRecurrenceFrequency,
    weekly_days: frozenset[int] | None,
    monthly_day: int,
    end_type: NoticeSeriesEndType,
    end_on: date | None,
    end_after_sends: int | None,
    org_tz: ZoneInfo,
    limit: int = 4,
) -> list[datetime]:
    """Return up to limit upcoming send times in UTC."""
    sends: list[datetime] = []
    first = compute_first_send_at(
        starts_on=starts_on,
        time_of_day=time_of_day,
        frequency=frequency,
        weekly_days=weekly_days,
        monthly_day=monthly_day,
        org_tz=org_tz,
    )
    current = first
    while len(sends) < limit:
        if should_end_before_send(
            end_type=end_type,
            end_on=end_on,
            end_after_sends=end_after_sends,
            sends_completed=len(sends),
            candidate_planned_at=current,
            org_tz=org_tz,
        ):
            break
        sends.append(current)
        if end_type == "after_count" and end_after_sends is not None:
            if len(sends) >= end_after_sends:
                break
        try:
            current = compute_next_send_after(
                after_planned_at=current,
                time_of_day=time_of_day,
                frequency=frequency,
                weekly_days=weekly_days,
                monthly_day=monthly_day,
                org_tz=org_tz,
            )
        except ValueError:
            break
    return sends


def should_end_before_send(
    *,
    end_type: NoticeSeriesEndType,
    end_on: date | None,
    end_after_sends: int | None,
    sends_completed: int,
    candidate_planned_at: datetime,
    org_tz: ZoneInfo,
) -> bool:
    """True if no send should occur at candidate_planned_at."""
    if end_type == "after_count":
        if end_after_sends is not None and sends_completed >= end_after_sends:
            return True
        return False
    if end_on is None:
        return False
    local_date = candidate_planned_at.astimezone(org_tz).date()
    return local_date > end_on


def series_has_more_sends(
    *,
    end_type: NoticeSeriesEndType,
    end_on: date | None,
    end_after_sends: int | None,
    sends_completed: int,
    next_send_at: datetime | None,
    org_tz: ZoneInfo,
) -> bool:
    """False when recurrence should stop after sends_completed deliveries."""
    if next_send_at is None:
        return False
    if end_type == "after_count" and end_after_sends is not None:
        return sends_completed < end_after_sends
    if end_type == "on_date" and end_on is not None:
        return not should_end_before_send(
            end_type=end_type,
            end_on=end_on,
            end_after_sends=None,
            sends_completed=sends_completed,
            candidate_planned_at=next_send_at,
            org_tz=org_tz,
        )
    return True


def _format_local_time_label(time_of_day: time, org_tz: ZoneInfo) -> str:
    """Format wall-clock send time with organization timezone abbreviation."""
    hour = time_of_day.hour % 12 or 12
    minute = time_of_day.minute
    am_pm = "am" if time_of_day.hour < 12 else "pm"
    if minute:
        clock = f"{hour}:{minute:02d} {am_pm}"
    else:
        clock = f"{hour} {am_pm}"
    tz_label = datetime.now(org_tz).tzname()
    if tz_label:
        return f"{clock} {tz_label}"
    return clock


def format_repeats_label(
    *,
    frequency: NoticeRecurrenceFrequency,
    weekly_days: list[int] | None,
    monthly_day: int,
    time_of_day: time,
    org_tz: ZoneInfo,
) -> str:
    """Human-readable recurrence line for admin UI."""
    time_str = _format_local_time_label(time_of_day, org_tz)

    day_names = {
        1: "Monday",
        2: "Tuesday",
        3: "Wednesday",
        4: "Thursday",
        5: "Friday",
        6: "Saturday",
        7: "Sunday",
    }
    if frequency == "weekly" and weekly_days:
        ordered = sorted(weekly_days)
        names = [day_names[d] for d in ordered]
        if len(names) == 1:
            day_part = f"Every {names[0]}"
        else:
            day_part = "Every " + ", ".join(names[:-1]) + f" and {names[-1]}"
        return f"{day_part} at {time_str}"

    if monthly_day == 0:
        day_part = "The last day of every month"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(
            monthly_day % 10 if monthly_day % 100 not in (11, 12, 13) else 0, "th"
        )
        day_part = f"The {monthly_day}{suffix} of every month"
    return f"{day_part} at {time_str}"


def parse_org_timezone(tz_name: str) -> ZoneInfo:
    """Load IANA timezone or raise ValueError."""
    try:
        return ZoneInfo(tz_name.strip() or "UTC")
    except Exception as exc:
        raise ValueError(f"invalid timezone: {tz_name}") from exc
