"""Calendar rules for sending the same notice again."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from apps.user_service.app.schemas.enums import (
    NOTICE_REPEAT_MAX_COUNT,
    NOTICE_REPEAT_MAX_DAYS,
    NoticeRepeatFrequency,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

WEEKDAY_NAMES: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
PREVIEW_LIMIT = 4


@dataclass(frozen=True)
class NoticeRepeatRule:
    """Normalized repeat rule stored on one notice."""

    frequency: str
    weekdays: tuple[int, ...]
    day_of_month: int | None
    last_day_of_month: bool
    time_of_day: time
    starts_on: date
    ends_on: date | None
    end_after_count: int | None


@dataclass(frozen=True)
class RepeatActivation:
    """Status and timestamps when a repeat rule is turned on."""

    status: str
    publish_at: datetime | None
    published_at: datetime | None
    occurrences_sent: int
    next_publish_at: datetime | None
    fired_at: datetime | None


def empty_repeat_columns() -> dict[str, Any]:
    """Column values for a notice that does not repeat."""
    return {
        "repeat_frequency": None,
        "repeat_weekdays": None,
        "repeat_day_of_month": None,
        "repeat_last_day": False,
        "repeat_time": None,
        "repeat_starts_on": None,
        "repeat_ends_on": None,
        "repeat_end_after_count": None,
        "repeat_occurrences_sent": 0,
        "next_publish_at": None,
        "repeat_cancelled_at": None,
    }


def repeat_columns(
    rule: NoticeRepeatRule,
    *,
    occurrences_sent: int,
    next_publish_at: datetime | None,
    cancelled_at: datetime | None = None,
) -> dict[str, Any]:
    """Map a rule onto notices columns."""
    return {
        "repeat_frequency": rule.frequency,
        "repeat_weekdays": list(rule.weekdays) or None,
        "repeat_day_of_month": rule.day_of_month,
        "repeat_last_day": rule.last_day_of_month,
        "repeat_time": rule.time_of_day,
        "repeat_starts_on": rule.starts_on,
        "repeat_ends_on": rule.ends_on,
        "repeat_end_after_count": rule.end_after_count,
        "repeat_occurrences_sent": occurrences_sent,
        "next_publish_at": next_publish_at,
        "repeat_cancelled_at": cancelled_at,
    }


def parse_repeat_input(payload: Any) -> NoticeRepeatRule:
    """Validate API repeat input and return a normalized rule."""
    frequency = payload.frequency.value
    weekdays: tuple[int, ...] = ()
    day_of_month = payload.day_of_month
    last_day = bool(payload.last_day_of_month)
    if frequency == NoticeRepeatFrequency.WEEKLY.value:
        weekdays = _parse_weekdays(payload.weekdays)
        day_of_month = None
        last_day = False
    elif day_of_month is not None and last_day:
        raise ValidationException(
            message_key="notices.errors.repeat_day_conflict",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    elif day_of_month is None and not last_day:
        raise ValidationException(
            message_key="notices.errors.repeat_day_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    ends_on = payload.ends_on
    end_after = payload.end_after_count
    if (ends_on is None) == (end_after is None):
        raise ValidationException(
            message_key="notices.errors.repeat_end_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    if end_after is not None and not 1 <= end_after <= NOTICE_REPEAT_MAX_COUNT:
        raise ValidationException(
            message_key="notices.errors.repeat_count_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"max": NOTICE_REPEAT_MAX_COUNT},
        )
    if ends_on is not None and ends_on < payload.starts_on:
        raise ValidationException(
            message_key="notices.errors.repeat_end_before_start",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    if ends_on is not None and ends_on > payload.starts_on + timedelta(days=NOTICE_REPEAT_MAX_DAYS):
        raise ValidationException(
            message_key="notices.errors.repeat_too_long",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    try:
        time_of_day = _parse_time(payload.time_of_day)
    except ValueError as exc:
        raise ValidationException(
            message_key="notices.errors.repeat_time_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        ) from exc

    return NoticeRepeatRule(
        frequency=frequency,
        weekdays=weekdays,
        day_of_month=day_of_month,
        last_day_of_month=last_day,
        time_of_day=time_of_day,
        starts_on=payload.starts_on,
        ends_on=ends_on,
        end_after_count=end_after,
    )


def rule_from_row(row: dict[str, Any]) -> NoticeRepeatRule | None:
    """Rebuild a rule from a notices row. Missing rules return None."""
    frequency = row.get("repeat_frequency")
    if not frequency:
        return None
    weekdays_raw = row.get("repeat_weekdays") or []
    weekdays = tuple(sorted({int(day) for day in weekdays_raw}))
    time_of_day = row.get("repeat_time")
    if isinstance(time_of_day, datetime):
        time_of_day = time_of_day.time()
    starts_on = _as_date(row.get("repeat_starts_on"))
    ends_on = _as_date(row.get("repeat_ends_on"))
    if time_of_day is None or starts_on is None:
        return None
    return NoticeRepeatRule(
        frequency=str(frequency),
        weekdays=weekdays,
        day_of_month=int(row["repeat_day_of_month"]) if row.get("repeat_day_of_month") else None,
        last_day_of_month=bool(row.get("repeat_last_day")),
        time_of_day=time_of_day,
        starts_on=starts_on,
        ends_on=ends_on,
        end_after_count=(
            int(row["repeat_end_after_count"]) if row.get("repeat_end_after_count") else None
        ),
    )


def occurrence_datetimes(rule: NoticeRepeatRule) -> list[datetime]:
    """Return every send instant for the rule, in UTC, oldest first."""
    horizon = rule.starts_on + timedelta(days=NOTICE_REPEAT_MAX_DAYS)
    end_date = min(rule.ends_on or horizon, horizon)
    if rule.frequency == NoticeRepeatFrequency.WEEKLY.value:
        return _weekly_occurrences(rule, end_date)
    return _monthly_occurrences(rule, end_date)


def _weekly_occurrences(rule: NoticeRepeatRule, end_date: date) -> list[datetime]:
    """UTC send instants for a weekly rule through `end_date`."""
    found: list[datetime] = []
    day = rule.starts_on
    while day <= end_date:
        if day.isoweekday() in rule.weekdays and _record_send(found, rule, day):
            break
        day += timedelta(days=1)
    return found


def _monthly_occurrences(rule: NoticeRepeatRule, end_date: date) -> list[datetime]:
    """UTC send instants for a monthly rule through `end_date`."""
    found: list[datetime] = []
    year, month = rule.starts_on.year, rule.starts_on.month
    while _month_in_range(rule, year, month, end_date):
        day = _monthly_day(year, month, rule)
        if rule.starts_on <= day <= end_date and _record_send(found, rule, day):
            break
        year, month = _next_month(year, month)
    return found


def _month_in_range(rule: NoticeRepeatRule, year: int, month: int, end_date: date) -> bool:
    """Return whether this calendar month can still contain a send."""
    month_start = date(year, month, 1)
    if month_start > end_date + timedelta(days=31):
        return False
    return (month_start - rule.starts_on).days <= NOTICE_REPEAT_MAX_DAYS


def _next_month(year: int, month: int) -> tuple[int, int]:
    """Return the year and month after `month`."""
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _record_send(found: list[datetime], rule: NoticeRepeatRule, day: date) -> bool:
    """Append the UTC slot for `day`. Return True when the send count is full."""
    found.append(_at_utc(day, rule.time_of_day))
    return rule.end_after_count is not None and len(found) >= rule.end_after_count


def first_occurrence(rule: NoticeRepeatRule) -> datetime | None:
    """Return the first send instant, or None when the rule has no sends."""
    items = occurrence_datetimes(rule)
    return items[0] if items else None


def next_after(rule: NoticeRepeatRule, after: datetime) -> datetime | None:
    """Return the next send strictly after `after`."""
    after_utc = _as_utc(after)
    for item in occurrence_datetimes(rule):
        if item > after_utc:
            return item
    return None


def latest_due(rule: NoticeRepeatRule, due_at: datetime, now: datetime) -> datetime:
    """Collapse overdue slots to the latest one that is already due."""
    latest = _as_utc(due_at)
    now_utc = _as_utc(now)
    nxt = next_after(rule, latest)
    while nxt is not None and nxt <= now_utc:
        latest = nxt
        nxt = next_after(rule, latest)
    return latest


def activate_repeat(rule: NoticeRepeatRule, now: datetime) -> RepeatActivation:
    """Decide live vs scheduled and the following send when a rule is published."""
    from apps.user_service.app.schemas.enums import NoticeStatus

    first = first_occurrence(rule)
    if first is None:
        raise ValidationException(
            message_key="notices.errors.repeat_has_no_sends",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    now_utc = _as_utc(now)
    if first <= now_utc:
        fired = latest_due(rule, first, now_utc)
        return RepeatActivation(
            status=NoticeStatus.LIVE.value,
            publish_at=fired,
            published_at=now_utc,
            occurrences_sent=1,
            next_publish_at=next_after(rule, fired),
            fired_at=fired,
        )
    return RepeatActivation(
        status=NoticeStatus.SCHEDULED.value,
        publish_at=first,
        published_at=None,
        occurrences_sent=0,
        next_publish_at=first,
        fired_at=None,
    )


def summary_text(rule: NoticeRepeatRule) -> str:
    """Human label such as 'Every Monday at 9:00 am UTC'."""
    clock = f"{_time_label(rule.time_of_day)} UTC"
    if rule.frequency == NoticeRepeatFrequency.WEEKLY.value:
        names = [WEEKDAY_NAMES[day - 1].capitalize() for day in rule.weekdays]
        if len(names) == 1:
            days = names[0]
        elif len(names) == 2:
            days = f"{names[0]} and {names[1]}"
        else:
            days = ", ".join(names[:-1]) + f", and {names[-1]}"
        return f"Every {days} at {clock}"
    if rule.last_day_of_month:
        return f"The last day of every month at {clock}"
    return f"The {_ordinal(rule.day_of_month or 1)} of every month at {clock}"


def upcoming_sends(
    rule: NoticeRepeatRule,
    *,
    start_at: datetime | None,
    limit: int = PREVIEW_LIMIT,
) -> list[datetime]:
    """Return the next send instants, including `start_at` when it is a slot."""
    items = occurrence_datetimes(rule)
    if start_at is None:
        return items[:limit]
    start_utc = _as_utc(start_at)
    return [item for item in items if item >= start_utc][:limit]


def advance_due_send(
    rule: NoticeRepeatRule,
    due_at: datetime,
    now: datetime,
) -> tuple[datetime, datetime | None]:
    """Return the occurrence to send now and the following one."""
    fired = latest_due(rule, due_at, now)
    return fired, next_after(rule, fired)


def _parse_weekdays(raw: list[str] | None) -> tuple[int, ...]:
    """Map weekday names to ISO numbers, Monday = 1."""
    if not raw:
        raise ValidationException(
            message_key="notices.errors.repeat_weekdays_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    parsed: set[int] = set()
    for item in raw:
        name = str(item).strip().lower()
        if name not in WEEKDAY_NAMES:
            raise ValidationException(
                message_key="notices.errors.repeat_weekday_invalid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"weekday": str(item)},
            )
        parsed.add(WEEKDAY_NAMES.index(name) + 1)
    return tuple(sorted(parsed))


def _parse_time(value: str) -> time:
    """Parse a UTC clock such as 09:00 or 9:00 AM."""
    text = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            parsed = datetime.strptime(text.upper() if "%p" in fmt else text, fmt)
            return time(parsed.hour, parsed.minute)
        except ValueError:
            continue
    raise ValueError(value)


def _monthly_day(year: int, month: int, rule: NoticeRepeatRule) -> date:
    """Return the calendar day this monthly rule uses in `year`/`month`."""
    if rule.last_day_of_month:
        if month == 12:
            return date(year + 1, 1, 1) - timedelta(days=1)
        return date(year, month + 1, 1) - timedelta(days=1)
    return date(year, month, int(rule.day_of_month))


def _at_utc(day: date, clock: time) -> datetime:
    """Combine a calendar day and a UTC clock."""
    return datetime.combine(day, clock, tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Return `value` as an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_date(value: Any) -> date | None:
    """Coerce a database date or datetime to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _ordinal(day_number: int) -> str:
    """Return a day number with an English ordinal suffix."""
    if 10 <= day_number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_number % 10, "th")
    return f"{day_number}{suffix}"


def _time_label(clock: time) -> str:
    """Format a clock as 9:00 am."""
    hour = clock.hour % 12 or 12
    suffix = "am" if clock.hour < 12 else "pm"
    return f"{hour}:{clock.minute:02d} {suffix}"
