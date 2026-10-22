"""Recurrence slot calculation for recurring notice templates."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.user_service.app.schemas.enums import (
    NoticeRecurrenceEndType,
    NoticeRecurrenceFrequency,
)

ISO_MONDAY = 1
ISO_SUNDAY = 7


@dataclass(frozen=True)
class RecurrenceRule:
    """Normalized recurrence rule for calculator functions."""

    frequency: NoticeRecurrenceFrequency
    weekly_days: tuple[int, ...] | None
    monthly_day: int | None
    monthly_use_last_day: bool
    time_of_day: time
    starts_on: date
    end_type: NoticeRecurrenceEndType
    end_date: date | None
    max_occurrences: int | None


@dataclass(frozen=True)
class RecurrencePreview:
    """Human-facing preview for create form."""

    recurrence_summary: str
    first_send_at: datetime
    upcoming_sends: list[datetime]


def _combine_local(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, t.microsecond, tzinfo=tz)


def _monthly_send_day(
    *, year: int, month: int, monthly_day: int | None, use_last_day: bool
) -> int | None:
    if use_last_day:
        return calendar.monthrange(year, month)[1]
    if monthly_day is None:
        return None
    last = calendar.monthrange(year, month)[1]
    if monthly_day > last:
        return None
    return monthly_day


def _weekly_candidates_from(anchor: date, weekly_days: tuple[int, ...]) -> date | None:
    days_set = set(weekly_days)
    for offset in range(0, 370):
        candidate = anchor + timedelta(days=offset)
        iso = candidate.isoweekday()
        if iso in days_set:
            return candidate
    return None


def _monthly_candidate_from(anchor: date, rule: RecurrenceRule) -> date | None:
    year, month = anchor.year, anchor.month
    for _ in range(0, 36):
        day = _monthly_send_day(
            year=year,
            month=month,
            monthly_day=rule.monthly_day,
            use_last_day=rule.monthly_use_last_day,
        )
        if day is not None:
            candidate = date(year, month, day)
            if candidate >= anchor:
                return candidate
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return None


def first_occurrence(rule: RecurrenceRule, *, timezone: str) -> datetime:
    """First send on or after starts_on at time_of_day in timezone."""
    tz = ZoneInfo(timezone)
    anchor = rule.starts_on
    if rule.frequency == NoticeRecurrenceFrequency.WEEKLY:
        if not rule.weekly_days:
            raise ValueError("weekly_days required")
        send_date = _weekly_candidates_from(anchor, rule.weekly_days)
        if send_date is None:
            raise ValueError("no weekly slot")
        return _combine_local(send_date, rule.time_of_day, tz)
    send_date = _monthly_candidate_from(anchor, rule)
    if send_date is None:
        raise ValueError("no monthly slot")
    return _combine_local(send_date, rule.time_of_day, tz)


def next_occurrence_after(
    rule: RecurrenceRule, *, after: datetime, timezone: str
) -> datetime | None:
    """Next slot strictly after `after` (local wall clock in timezone)."""
    tz = ZoneInfo(timezone)
    local_after = after.astimezone(tz)
    start_from = local_after.date() + timedelta(days=1)
    if rule.frequency == NoticeRecurrenceFrequency.WEEKLY:
        if not rule.weekly_days:
            return None
        send_date = _weekly_candidates_from(start_from, rule.weekly_days)
        if send_date is None:
            return None
        return _combine_local(send_date, rule.time_of_day, tz)
    send_date = _monthly_candidate_from(start_from, rule)
    if send_date is None:
        return None
    return _combine_local(send_date, rule.time_of_day, tz)


def _local_send_date(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


def exceeds_end_on_date(rule: RecurrenceRule, *, candidate: datetime, timezone: str) -> bool:
    if rule.end_type != NoticeRecurrenceEndType.ON_DATE or rule.end_date is None:
        return False
    tz = ZoneInfo(timezone)
    return _local_send_date(candidate, tz) > rule.end_date


def exceeds_after_count(rule: RecurrenceRule, *, occurrences_sent: int) -> bool:
    if rule.end_type != NoticeRecurrenceEndType.AFTER_COUNT or rule.max_occurrences is None:
        return False
    return occurrences_sent >= rule.max_occurrences


def preview_recurrence(
    rule: RecurrenceRule,
    *,
    timezone: str,
    count: int = 4,
) -> RecurrencePreview:
    """Build summary and next N send datetimes."""
    first = first_occurrence(rule, timezone=timezone)
    upcoming: list[datetime] = [first]
    cursor = first
    while len(upcoming) < count:
        if exceeds_after_count(rule, occurrences_sent=len(upcoming)):
            break
        nxt = next_occurrence_after(rule, after=cursor, timezone=timezone)
        if nxt is None:
            break
        if exceeds_end_on_date(rule, candidate=nxt, timezone=timezone):
            break
        upcoming.append(nxt)
        cursor = nxt

    summary = _format_summary(rule, first=first, timezone=timezone)
    return RecurrencePreview(
        recurrence_summary=summary,
        first_send_at=first,
        upcoming_sends=upcoming,
    )


def _format_summary(rule: RecurrenceRule, *, first: datetime, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    local_first = first.astimezone(tz)
    hour = local_first.hour % 12 or 12
    time_str = f"{hour}:{local_first.minute:02d} {local_first.strftime('%p').lower()}"
    if rule.frequency == NoticeRecurrenceFrequency.WEEKLY and rule.weekly_days:
        names = _weekday_names(rule.weekly_days)
        freq_part = f"Every {names} at {time_str}"
    elif rule.monthly_use_last_day:
        freq_part = f"The last day of every month at {time_str}"
    else:
        day = rule.monthly_day or 1
        freq_part = f"The {_ordinal(day)} of every month at {time_str}"

    end_part = ""
    if rule.end_type == NoticeRecurrenceEndType.NEVER:
        end_part = " — No end date."
    elif rule.end_type == NoticeRecurrenceEndType.ON_DATE and rule.end_date:
        end_part = f" — Ends on {rule.end_date.strftime('%d %b %Y')}."
    elif rule.end_type == NoticeRecurrenceEndType.AFTER_COUNT and rule.max_occurrences:
        end_part = f" — Ends after {rule.max_occurrences} sends."

    return f"{freq_part}. First send {local_first.strftime('%a %d %b %Y, %I:%M %p').replace(' 0', ' ')}{end_part}"


def _weekday_names(days: tuple[int, ...]) -> str:
    labels = {
        1: "Monday",
        2: "Tuesday",
        3: "Wednesday",
        4: "Thursday",
        5: "Friday",
        6: "Saturday",
        7: "Sunday",
    }
    ordered = [labels[d] for d in sorted(days)]
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        return f"{ordered[0]} and {ordered[1]}"
    return ", ".join(ordered[:-1]) + f" and {ordered[-1]}"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def rule_from_notice_row(row: dict) -> RecurrenceRule:
    """Build RecurrenceRule from a notices template row dict."""
    freq = row.get("recurrence_frequency")
    end_type = row.get("recurrence_end_type")
    weekly = row.get("recurrence_weekly_days")
    weekly_tuple: tuple[int, ...] | None = None
    if weekly:
        weekly_tuple = tuple(int(d) for d in weekly)

    tod = row.get("recurrence_time_of_day")
    if isinstance(tod, time):
        time_of_day = tod
    elif tod is not None:
        time_of_day = tod
    else:
        time_of_day = time(9, 0)

    starts = row.get("recurrence_starts_on")
    if not isinstance(starts, date):
        raise ValueError("recurrence_starts_on required on template")

    end_raw = end_type or NoticeRecurrenceEndType.NEVER.value
    return RecurrenceRule(
        frequency=NoticeRecurrenceFrequency(str(freq)),
        weekly_days=weekly_tuple,
        monthly_day=row.get("recurrence_monthly_day"),
        monthly_use_last_day=bool(row.get("recurrence_monthly_use_last_day")),
        time_of_day=time_of_day,
        starts_on=starts,
        end_type=NoticeRecurrenceEndType(str(end_raw)),
        end_date=row.get("recurrence_end_date"),
        max_occurrences=row.get("recurrence_max_occurrences"),
    )
