"""Unit tests for notice recurrence calculator."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

from apps.user_service.app.schemas.enums import (
    NoticeRecurrenceEndType,
    NoticeRecurrenceFrequency,
)
from apps.user_service.app.utils.notice_recurrence import (
    RecurrenceRule,
    first_occurrence,
    next_occurrence_after,
    preview_recurrence,
)

TZ = "Asia/Kolkata"


def _weekly_rule(**kwargs) -> RecurrenceRule:
    defaults = {
        "frequency": NoticeRecurrenceFrequency.WEEKLY,
        "weekly_days": (1, 3, 5),
        "monthly_day": None,
        "monthly_use_last_day": False,
        "time_of_day": time(9, 0),
        "starts_on": date(2026, 10, 21),
        "end_type": NoticeRecurrenceEndType.NEVER,
        "end_date": None,
        "max_occurrences": None,
    }
    defaults.update(kwargs)
    return RecurrenceRule(**defaults)


def test_first_send_weekly_mon_wed_fri_from_tuesday_anchor():
    """Flow example: starts_on Thu 22 Oct 2026 → first Fri 23 Oct 2026 09:00 IST."""
    rule = _weekly_rule(weekly_days=(1, 3, 5), starts_on=date(2026, 10, 22))
    first = first_occurrence(rule, timezone=TZ)
    local = first.astimezone(ZoneInfo(TZ))
    assert local.date() == date(2026, 10, 23)
    assert local.hour == 9
    assert local.minute == 0


def test_next_weekly_occurrence():
    rule = _weekly_rule(weekly_days=(1, 3, 5), starts_on=date(2026, 10, 22))
    first = first_occurrence(rule, timezone=TZ)
    second = next_occurrence_after(rule, after=first, timezone=TZ)
    assert second is not None
    local = second.astimezone(ZoneInfo(TZ))
    assert local.date() == date(2026, 10, 26)


def test_monthly_first_on_first_after_mid_october():
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.MONTHLY,
        weekly_days=None,
        monthly_day=1,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 21),
        end_type=NoticeRecurrenceEndType.AFTER_COUNT,
        end_date=None,
        max_occurrences=12,
    )
    first = first_occurrence(rule, timezone=TZ)
    local = first.astimezone(ZoneInfo(TZ))
    assert local.date() == date(2026, 11, 1)


def test_monthly_skips_february_when_day_31():
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.MONTHLY,
        weekly_days=None,
        monthly_day=31,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 1, 1),
        end_type=NoticeRecurrenceEndType.NEVER,
        end_date=None,
        max_occurrences=None,
    )
    first = first_occurrence(rule, timezone=TZ)
    assert first.astimezone(ZoneInfo(TZ)).date() == date(2026, 1, 31)
    second = next_occurrence_after(rule, after=first, timezone=TZ)
    assert second is not None
    assert second.astimezone(ZoneInfo(TZ)).date() == date(2026, 3, 31)


def test_preview_includes_first_and_upcoming():
    rule = _weekly_rule()
    preview = preview_recurrence(rule, timezone=TZ, count=4)
    assert len(preview.upcoming_sends) == 4
    assert "Monday" in preview.recurrence_summary or "Friday" in preview.recurrence_summary
