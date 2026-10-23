"""End-to-end style unit tests for recurring notice create → deliver → stop."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.publish_scheduled_notices import (
    process_recurring_notices,
)
from apps.user_service.app.schemas.enums import (
    NoticeCategory,
    NoticePublishMode,
    NoticeRecurrenceFrequency,
    NoticeSeriesEndType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    NoticeRepeatInput,
    UpdateNoticeRequest,
)
from apps.user_service.app.services import notice_recurring_support as recurring
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.tests.unit.test_notices_service import (
    NOTICE_ID,
    PROJECT_ID,
    _notice_detail_row,
    _service,
    _stub_detail_repo,
)

ORG_ID = "org-1"
PLANNED_UTC = datetime(2026, 10, 27, 3, 30, tzinfo=timezone.utc)


def _recurring_row(**overrides) -> dict:
    base = _notice_detail_row(
        status=NoticeStatus.SCHEDULED.value,
        series_frequency="weekly",
        series_monthly_day=1,
        series_starts_on=date(2026, 10, 27),
        series_time_of_day=time(9, 0),
        series_end_type="after_count",
        series_end_on=None,
        series_end_after_sends=4,
        series_sends_completed=0,
        series_next_send_at=PLANNED_UTC,
        series_repeat_ended_at=None,
        series_last_sent_at=None,
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_start_series_persists_series_and_next_send():
    svc = _service()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=10)
    scheduled_row = _recurring_row()
    svc.repo.insert_notice = AsyncMock(return_value=scheduled_row)
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.fetch_project_timezone = AsyncMock(return_value="Asia/Kolkata")
    svc.repo.notice_has_series = AsyncMock(return_value=False)
    svc.repo.insert_notice_series = AsyncMock()
    svc.repo.replace_series_weekly_days = AsyncMock()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=scheduled_row)
    svc.repo.list_series_occurrences = AsyncMock(return_value=[])
    svc.repo.list_series_weekly_days = AsyncMock(return_value=[1, 3])
    _stub_detail_repo(svc)

    body = CreateNoticeRequest(
        title="Water shutdown",
        description="Sector B",
        category=NoticeCategory.MAINTENANCE,
        recipient_groups=["Owner"],
        publish_mode=NoticePublishMode.START_SERIES,
        repeat=NoticeRepeatInput(
            enabled=True,
            frequency=NoticeRecurrenceFrequency.WEEKLY,
            weekly_days=[1, 3],
            starts_on=date(2026, 10, 27),
            time_of_day=time(9, 0),
            end_type=NoticeSeriesEndType.AFTER_COUNT,
            end_after_sends=4,
        ),
    )
    detail = await svc.create_notice(project_id=PROJECT_ID, body=body)

    svc.repo.insert_notice_series.assert_awaited_once()
    series_kwargs = svc.repo.insert_notice_series.await_args.kwargs
    assert series_kwargs["frequency"] == "weekly"
    assert series_kwargs["next_send_at"] is not None
    svc.repo.replace_series_weekly_days.assert_awaited_once_with(
        organization_id=ORG_ID,
        notice_id=NOTICE_ID,
        weekly_days=[1, 3],
    )
    assert detail.is_recurring is True


@pytest.mark.asyncio
async def test_process_recurring_delivers_final_send_and_ends_series():
    notice_id = NOTICE_ID
    job_row = {
        "id": notice_id,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "title": "Water shutdown",
        "scope_type": "whole_society",
        "series_frequency": "weekly",
        "series_monthly_day": 1,
        "series_starts_on": date(2026, 10, 27),
        "series_time_of_day": time(9, 0),
        "series_end_type": "after_count",
        "series_end_on": None,
        "series_end_after_sends": 1,
        "series_sends_completed": 0,
        "series_next_send_at": PLANNED_UTC,
        "series_repeat_ended_at": None,
    }
    repo = MagicMock()
    repo.list_due_recurring_notice_ids = AsyncMock(return_value=[notice_id])
    repo.fetch_notice_by_id_only = AsyncMock(return_value=job_row)
    repo.deliver_due_recurring_notice = AsyncMock(return_value=True)
    repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    repo.list_contact_recipient_ids = AsyncMock(return_value=[])
    repo.list_towers_for_notice = AsyncMock(return_value=[])

    push = MagicMock()
    push.send_to_user = AsyncMock()
    recipients = MagicMock()
    recipients.resolve_recipient_user_ids = AsyncMock(return_value=["user-a"])
    recipients.resolve_user_ids_for_contact_ids = AsyncMock(return_value=[])

    with (
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository",
            return_value=repo,
        ),
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.PushNotificationDispatcher",
            return_value=push,
        ),
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticeRecipientResolutionService",
            return_value=recipients,
        ),
    ):
        delivered = await process_recurring_notices(
            MagicMock(),
            organization_id=ORG_ID,
        )

    assert delivered == [notice_id]
    repo.deliver_due_recurring_notice.assert_awaited_once_with(
        organization_id=ORG_ID,
        notice_id=notice_id,
        project_id=PROJECT_ID,
    )
    push.send_to_user.assert_awaited()


@pytest.mark.asyncio
async def test_update_end_series_stops_recurrence():
    svc = _service()
    live = _recurring_row(status=NoticeStatus.LIVE.value)
    ended = _recurring_row(
        status=NoticeStatus.LIVE.value,
        series_repeat_ended_at=datetime.now(timezone.utc),
        series_next_send_at=None,
    )
    svc.repo.fetch_notice_by_id = AsyncMock(side_effect=[live, ended])
    svc.repo.end_notice_series = AsyncMock()
    svc.repo.list_series_occurrences = AsyncMock(return_value=[])
    svc.repo.list_series_weekly_days = AsyncMock(return_value=[1, 3])
    svc.repo.fetch_project_timezone = AsyncMock(return_value="Asia/Kolkata")
    _stub_detail_repo(svc)

    await svc.update_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=UpdateNoticeRequest(publish_mode=NoticePublishMode.END_SERIES),
    )
    svc.repo.end_notice_series.assert_awaited_once_with(
        organization_id=ORG_ID,
        notice_id=NOTICE_ID,
    )


@pytest.mark.asyncio
async def test_delete_active_recurring_ends_series_then_soft_deletes():
    svc = _service()
    live = _recurring_row(status=NoticeStatus.LIVE.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=live)
    svc.repo.end_notice_series = AsyncMock()
    svc.repo.soft_delete_notice = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.DELETED.value)
    )

    await svc.delete_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)

    svc.repo.end_notice_series.assert_awaited_once()
    svc.repo.soft_delete_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_compute_next_after_delivery_mid_series_has_next_slot():
    repo = MagicMock()
    repo.fetch_project_timezone = AsyncMock(return_value="Asia/Kolkata")
    repo.list_series_weekly_days = AsyncMock(return_value=[1, 3])
    row = _recurring_row(series_sends_completed=1)

    next_at, ended = await recurring.compute_next_after_delivery(
        repo,
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        row=row,
        after_planned_at=PLANNED_UTC,
        sends_completed=2,
    )
    assert ended is False
    assert next_at is not None
    assert next_at > PLANNED_UTC


def test_live_recurring_notice_is_editable():
    row = {"series_frequency": "weekly", "series_repeat_ended_at": None}
    NoticesService._assert_editable(
        None,
        NoticeStatus.LIVE.value,
        row,
    )
