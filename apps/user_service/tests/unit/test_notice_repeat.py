"""Unit tests for notice repeat calendar rules."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    NoticePublishMode,
    NoticeRecipientGroup,
    NoticeRepeatFrequency,
    NoticeScopeType,
)
from apps.user_service.app.schemas.notices import CreateNoticeRequest, NoticeRepeatInput
from apps.user_service.app.services.notice_repeat import (
    activate_repeat,
    first_occurrence,
    next_after,
    occurrence_datetimes,
    parse_repeat_input,
    summary_text,
    upcoming_sends,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.utils.common_utils import UserContext


def _weekly() -> SimpleNamespace:
    return SimpleNamespace(
        frequency=NoticeRepeatFrequency.WEEKLY,
        weekdays=["monday"],
        day_of_month=None,
        last_day_of_month=False,
        time_of_day="09:00",
        starts_on=date(2026, 10, 21),
        ends_on=date(2027, 1, 21),
        end_after_count=None,
    )


def _monthly(*, end_after_count: int | None = None, ends_on: date | None = date(2027, 1, 21)):
    return SimpleNamespace(
        frequency=NoticeRepeatFrequency.MONTHLY,
        weekdays=None,
        day_of_month=1,
        last_day_of_month=False,
        time_of_day="09:00",
        starts_on=date(2026, 10, 21),
        ends_on=ends_on,
        end_after_count=end_after_count,
    )


def _utc_dates(moments: list[datetime]) -> list[date]:
    return [item.astimezone(timezone.utc).date() for item in moments]


def test_weekly_preview_matches_monday_schedule():
    rule = parse_repeat_input(_weekly())
    assert summary_text(rule) == "Every Monday at 9:00 am UTC"
    first = first_occurrence(rule)
    assert first == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    assert _utc_dates(upcoming_sends(rule, start_at=first)) == [
        date(2026, 10, 26),
        date(2026, 11, 2),
        date(2026, 11, 9),
        date(2026, 11, 16),
    ]


def test_monthly_preview_on_a_date_and_after_count():
    on_date = parse_repeat_input(_monthly())
    assert summary_text(on_date) == "The 1st of every month at 9:00 am UTC"
    assert _utc_dates(occurrence_datetimes(on_date)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]

    after_twelve = parse_repeat_input(_monthly(end_after_count=12, ends_on=None))
    upcoming = _utc_dates(upcoming_sends(after_twelve, start_at=None))
    assert upcoming == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]
    assert len(occurrence_datetimes(after_twelve)) == 12


def test_monthly_last_day_uses_short_month_end():
    rule = parse_repeat_input(
        SimpleNamespace(
            frequency=NoticeRepeatFrequency.MONTHLY,
            weekdays=None,
            day_of_month=None,
            last_day_of_month=True,
            time_of_day="09:00",
            starts_on=date(2027, 1, 1),
            ends_on=date(2027, 3, 31),
            end_after_count=None,
        )
    )
    assert summary_text(rule) == "The last day of every month at 9:00 am UTC"
    assert _utc_dates(occurrence_datetimes(rule)) == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]


def test_activate_future_rule_schedules_and_due_rule_sends_once():
    rule = parse_repeat_input(_weekly())
    before = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)
    scheduled = activate_repeat(rule, before)
    assert scheduled.status == "scheduled"
    assert scheduled.occurrences_sent == 0
    assert scheduled.next_publish_at == scheduled.publish_at
    assert scheduled.fired_at is None

    after = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)
    live = activate_repeat(rule, after)
    assert live.status == "live"
    assert live.occurrences_sent == 1
    assert live.fired_at is not None
    assert live.fired_at == datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    assert live.next_publish_at == datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    assert next_after(rule, live.fired_at) == live.next_publish_at


@pytest.mark.asyncio
async def test_create_repeating_notice_keeps_one_row():
    svc = NoticesService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="owner@example.com",
            organization_id="org-1",
        ),
    )
    created_at = datetime(2026, 10, 21, tzinfo=timezone.utc)
    row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "organization_id": "org-1",
        "project_id": "11111111-1111-1111-1111-111111111111",
        "display_code": "NTC-1",
        "status": "scheduled",
        "title": "Water supply",
        "description": "Shut off",
        "category": "maintenance",
        "scope_type": "whole_society",
        "publish_at": None,
        "published_at": None,
        "deleted_at": None,
        "pinned": False,
        "view_count": 0,
        "like_count": 0,
        "created_at": created_at,
        "updated_at": created_at,
    }
    svc.repo = MagicMock()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=1)
    svc.repo.insert_notice = AsyncMock(return_value=row)
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_units = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=[])
    svc.repo.list_unit_ids = AsyncMock(return_value=[])
    svc.repo.filter_project_unit_ids = AsyncMock(return_value=[])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=row)
    svc.recipient_service.filter_valid_project_contact_ids = AsyncMock(return_value=[])

    body = CreateNoticeRequest(
        title="Water supply",
        description="Shut off",
        publish_mode=NoticePublishMode.SCHEDULE,
        recipient_groups=[NoticeRecipientGroup.OWNER],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        repeat=NoticeRepeatInput(
            frequency=NoticeRepeatFrequency.WEEKLY,
            weekdays=["monday"],
            time_of_day="09:00",
            starts_on=date(2027, 1, 4),
            ends_on=date(2027, 2, 1),
        ),
    )
    await svc.create_notice(
        project_id="11111111-1111-1111-1111-111111111111",
        body=body,
    )
    kwargs = svc.repo.insert_notice.await_args.kwargs
    assert kwargs["status"] == "scheduled"
    assert kwargs["repeat"]["repeat_frequency"] == "weekly"
    assert kwargs["repeat"]["repeat_weekdays"] == [1]
    assert kwargs["publish_at"] == kwargs["repeat"]["next_publish_at"]
    assert kwargs["publish_at"] == datetime(2027, 1, 4, 9, 0, tzinfo=timezone.utc)
    assert kwargs["repeat"]["repeat_occurrences_sent"] == 0
