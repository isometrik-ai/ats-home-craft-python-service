"""Unit tests for notice recurrence calculator."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from apps.user_service.app.services.notice_recurring import (
    compute_first_send_at,
    preview_sends,
    should_end_before_send,
)

IST = ZoneInfo("Asia/Kolkata")


def test_weekly_mon_only_first_send_after_wed_start():
    """starts 21 Oct 2026 (Wed), Mon only → Mon 26 Oct 2026 9:00 am IST."""
    first = compute_first_send_at(
        starts_on=date(2026, 10, 21),
        time_of_day=time(9, 0),
        frequency="weekly",
        weekly_days=frozenset({1}),
        monthly_day=1,
        org_tz=IST,
    )
    local = first.astimezone(IST)
    assert local.date() == date(2026, 10, 26)
    assert local.hour == 9


def test_weekly_multi_day_first_send_thu():
    """Mon–Thu + Sat from 21 Oct (Wed) → first Thu 22 Oct when Wed not selected."""
    first = compute_first_send_at(
        starts_on=date(2026, 10, 21),
        time_of_day=time(9, 0),
        frequency="weekly",
        weekly_days=frozenset({1, 2, 4, 6}),
        monthly_day=1,
        org_tz=IST,
    )
    local = first.astimezone(IST)
    assert local.date() == date(2026, 10, 22)


def test_monthly_first_on_first_after_mid_month_start():
    """1st of month, starts 21 Oct → 1 Nov."""
    first = compute_first_send_at(
        starts_on=date(2026, 10, 21),
        time_of_day=time(9, 0),
        frequency="monthly",
        weekly_days=None,
        monthly_day=1,
        org_tz=IST,
    )
    local = first.astimezone(IST)
    assert local.date() == date(2026, 11, 1)


def test_preview_respects_after_count():
    sends = preview_sends(
        starts_on=date(2026, 10, 26),
        time_of_day=time(8, 0),
        frequency="weekly",
        weekly_days=frozenset({1, 4}),
        monthly_day=1,
        end_type="after_count",
        end_on=None,
        end_after_sends=3,
        org_tz=IST,
        limit=10,
    )
    assert len(sends) == 3


def test_end_on_date_blocks_late_send():
    candidate = compute_first_send_at(
        starts_on=date(2026, 11, 1),
        time_of_day=time(9, 0),
        frequency="monthly",
        weekly_days=None,
        monthly_day=1,
        org_tz=IST,
    )
    assert should_end_before_send(
        end_type="on_date",
        end_on=date(2026, 10, 15),
        end_after_sends=None,
        sends_completed=0,
        candidate_planned_at=candidate,
        org_tz=IST,
    )


def test_weekly_requires_days():
    with pytest.raises(ValueError, match="weekly_days"):
        compute_first_send_at(
            starts_on=date(2026, 10, 21),
            time_of_day=time(9, 0),
            frequency="weekly",
            weekly_days=frozenset(),
            monthly_day=1,
            org_tz=IST,
        )
