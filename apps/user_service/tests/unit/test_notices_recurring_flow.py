"""Success and failure paths for recurring notice board flow."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import (
    NoticePublishMode,
    NoticeRecipientGroup,
    NoticeRecurrenceEndType,
    NoticeRecurrenceFrequency,
    NoticeSeriesAction,
    NoticeSeriesStatus,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    NoticeRecurrencePreviewRequest,
    NoticeRepeatInput,
    UpdateNoticeRequest,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.notice_recurrence import (
    RecurrenceRule,
    exceeds_after_count,
    preview_recurrence,
)
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
NOTICE_ID = "44444444-4444-4444-4444-444444444444"
INSTANCE_ID = "55555555-5555-5555-5555-555555555555"


def _service() -> NoticesService:
    svc = NoticesService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="owner@example.com",
            organization_id="org-1",
        ),
    )
    svc.repo = MagicMock()
    svc.repo.list_notices = AsyncMock(return_value=([], 0))
    svc.repo.list_attachments_for_notices = AsyncMock(return_value={})
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=[])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.recipient_service.filter_valid_project_contact_ids = AsyncMock(return_value=[])
    return svc


def _weekly_repeat(**kwargs) -> NoticeRepeatInput:
    base = {
        "frequency": NoticeRecurrenceFrequency.WEEKLY,
        "weekly_days": [1, 3, 5],
        "time_of_day": time(9, 0),
        "starts_on": date(2026, 10, 22),
        "end_type": NoticeRecurrenceEndType.NEVER,
    }
    base.update(kwargs)
    return NoticeRepeatInput(**base)


def _notice_row(**overrides) -> dict:
    created_at = datetime.now(timezone.utc)
    base = {
        "id": NOTICE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "display_code": "NTC-1",
        "status": NoticeStatus.DRAFT.value,
        "title": "Pool closure",
        "description": "Closed tomorrow",
        "category": "maintenance",
        "scope_type": "whole_society",
        "publish_at": None,
        "published_at": None,
        "deleted_at": None,
        "pinned": False,
        "pin_slot_index": None,
        "view_count": 0,
        "like_count": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "is_recurring_template": True,
        "series_status": NoticeSeriesStatus.ACTIVE.value,
        "recurrence_timezone": "Asia/Kolkata",
        "recurrence_frequency": NoticeRecurrenceFrequency.WEEKLY.value,
        "recurrence_weekly_days": [1, 3, 5],
        "recurrence_time_of_day": time(9, 0),
        "recurrence_starts_on": date(2026, 10, 22),
        "recurrence_end_type": NoticeRecurrenceEndType.NEVER.value,
        "occurrences_sent": 0,
        "next_occurrence_at": datetime(2020, 1, 1, 3, 30, tzinfo=timezone.utc),
        "series_pin_to_banner": False,
    }
    base.update(overrides)
    return base


# --- Recurrence calculator (success / failure) ---


def test_after_count_end_rule_blocks_third_materialize():
    """Job end rule: no live row once occurrences_sent reaches max_occurrences."""
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.WEEKLY,
        weekly_days=(1,),
        monthly_day=None,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 20),
        end_type=NoticeRecurrenceEndType.AFTER_COUNT,
        end_date=None,
        max_occurrences=2,
    )
    assert exceeds_after_count(rule, occurrences_sent=2) is True
    assert exceeds_after_count(rule, occurrences_sent=1) is False
    preview = preview_recurrence(rule, timezone="Asia/Kolkata", count=4)
    assert len(preview.upcoming_sends) == 2


def test_preview_respects_after_count_max_occurrences():
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.WEEKLY,
        weekly_days=(1, 3, 5),
        monthly_day=None,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 22),
        end_type=NoticeRecurrenceEndType.AFTER_COUNT,
        end_date=None,
        max_occurrences=1,
    )
    preview = preview_recurrence(rule, timezone="Asia/Kolkata", count=4)
    assert len(preview.upcoming_sends) == 1


def test_weekly_days_schema_rejects_invalid_iso_day():
    with pytest.raises(ValidationError):
        NoticeRepeatInput(
            frequency=NoticeRecurrenceFrequency.WEEKLY,
            weekly_days=[0, 8],
            time_of_day=time(9, 0),
            starts_on=date(2026, 10, 22),
        )


def test_recurrence_preview_schema_rejects_invalid_timezone():
    with pytest.raises(ValidationError):
        NoticeRecurrencePreviewRequest(
            repeat=_weekly_repeat(),
            timezone="not-a-zone",
        )


def test_exceeds_after_count_when_series_complete():
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.MONTHLY,
        weekly_days=None,
        monthly_day=1,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 1, 1),
        end_type=NoticeRecurrenceEndType.AFTER_COUNT,
        end_date=None,
        max_occurrences=12,
    )
    assert exceeds_after_count(rule, occurrences_sent=12) is True
    assert exceeds_after_count(rule, occurrences_sent=11) is False


# --- Service validation failures ---


def test_validate_weekly_repeat_requires_weekdays():
    svc = _service()
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.WEEKLY,
        weekly_days=None,
        monthly_day=None,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 22),
        end_type=NoticeRecurrenceEndType.NEVER,
        end_date=None,
        max_occurrences=None,
    )
    with pytest.raises(ValidationException) as exc_info:
        svc._validate_repeat_rule(rule)
    assert exc_info.value.message_key == "notices.errors.weekly_days_required"


def test_validate_monthly_requires_day_or_last_day():
    svc = _service()
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.MONTHLY,
        weekly_days=None,
        monthly_day=None,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 22),
        end_type=NoticeRecurrenceEndType.NEVER,
        end_date=None,
        max_occurrences=None,
    )
    with pytest.raises(ValidationException) as exc_info:
        svc._validate_repeat_rule(rule)
    assert exc_info.value.message_key == "notices.errors.invalid_monthly_day"


def test_validate_end_date_before_first_send_fails():
    svc = _service()
    rule = RecurrenceRule(
        frequency=NoticeRecurrenceFrequency.WEEKLY,
        weekly_days=(5,),
        monthly_day=None,
        monthly_use_last_day=False,
        time_of_day=time(9, 0),
        starts_on=date(2026, 10, 22),
        end_type=NoticeRecurrenceEndType.ON_DATE,
        end_date=date(2026, 10, 20),
        max_occurrences=None,
    )
    with pytest.raises(ValidationException) as exc_info:
        svc._validate_repeat_rule(rule)
    assert exc_info.value.message_key == "notices.errors.invalid_end_date"


@pytest.mark.asyncio
async def test_create_recurring_series_start_without_recipients_fails():
    svc = _service()
    body = CreateNoticeRequest(
        title="Weekly update",
        description="Body",
        publish_mode=NoticePublishMode.SERIES_START,
        repeat=_weekly_repeat(),
    )
    with pytest.raises(ValidationException) as exc_info:
        await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert exc_info.value.message_key == "notices.errors.recipients_required"


@pytest.mark.asyncio
async def test_create_recurring_with_schedule_mode_fails():
    svc = _service()
    body = CreateNoticeRequest(
        title="Weekly update",
        description="Body",
        recipient_groups=[NoticeRecipientGroup.OWNER],
        publish_mode=NoticePublishMode.SCHEDULE,
        publish_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc),
        repeat=_weekly_repeat(),
    )
    with pytest.raises(ValidationException) as exc_info:
        await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert exc_info.value.message_key == "notices.errors.repeat_with_schedule"


# --- Service success paths ---


@pytest.mark.asyncio
async def test_create_recurring_series_start_sets_active_and_next_occurrence():
    svc = _service()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=10)
    template_row = _notice_row(series_status=NoticeSeriesStatus.ACTIVE.value)
    svc.repo.insert_recurring_template = AsyncMock(return_value=template_row)
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=template_row)
    svc._materialize_recurring_template = AsyncMock(return_value=None)

    body = CreateNoticeRequest(
        title="Weekly update",
        description="Body",
        recipient_groups=[NoticeRecipientGroup.OWNER],
        publish_mode=NoticePublishMode.SERIES_START,
        repeat=_weekly_repeat(),
    )
    await svc.create_notice(project_id=PROJECT_ID, body=body)

    kwargs = svc.repo.insert_recurring_template.await_args.kwargs
    assert kwargs["series_status"] == NoticeSeriesStatus.ACTIVE.value
    assert kwargs["next_occurrence_at"] is not None


@pytest.mark.asyncio
async def test_update_recurring_cancel_clears_next_occurrence():
    svc = _service()
    active = _notice_row()
    cancelled = {**active, "series_status": NoticeSeriesStatus.CANCELLED.value}
    svc.repo.fetch_notice_by_id = AsyncMock(side_effect=[active, cancelled, cancelled])
    svc.repo.update_notice_fields = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])

    await svc.update_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=UpdateNoticeRequest(series_action=NoticeSeriesAction.CANCEL),
    )
    fields = svc.repo.update_notice_fields.await_args.kwargs["fields"]
    assert fields["series_status"] == NoticeSeriesStatus.CANCELLED.value
    assert fields["next_occurrence_at"] is None


@pytest.mark.asyncio
async def test_update_recurring_pause_from_draft_fails():
    svc = _service()
    draft = _notice_row(series_status=NoticeSeriesStatus.DRAFT.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=draft)

    with pytest.raises(ConflictException) as exc_info:
        await svc.update_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=UpdateNoticeRequest(series_action=NoticeSeriesAction.PAUSE),
        )
    assert exc_info.value.message_key == "notices.errors.series_not_editable"


@pytest.mark.asyncio
async def test_duplicate_active_recurring_template_fails():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_row(series_status=NoticeSeriesStatus.ACTIVE.value)
    )

    with pytest.raises(ConflictException) as exc_info:
        await svc.duplicate_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert exc_info.value.message_key == "notices.errors.series_not_editable"


@pytest.mark.asyncio
async def test_duplicate_draft_recurring_template_allowed():
    svc = _service()
    source = _notice_row(series_status=NoticeSeriesStatus.DRAFT.value, status="draft")
    copy_row = {
        **source,
        "id": INSTANCE_ID,
        "display_code": "NTC-11",
        "is_recurring_template": False,
    }
    svc.repo.fetch_notice_by_id = AsyncMock(side_effect=[source, copy_row])
    svc.repo.allocate_sequence_number = AsyncMock(return_value=11)
    svc.repo.insert_notice = AsyncMock(return_value={"id": INSTANCE_ID})
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])

    detail = await svc.duplicate_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.id == INSTANCE_ID
    svc.repo.insert_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_materialize_recurring_template_creates_live_instance():
    svc = _service()
    template = _notice_row()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=42)
    svc.repo.insert_notice = AsyncMock(return_value={"id": INSTANCE_ID})
    svc.repo.copy_notice_children = AsyncMock()
    svc.repo.update_notice_fields = AsyncMock()
    svc._dispatch_publish_push = AsyncMock()

    instance_id = await svc._materialize_recurring_template(template)

    assert instance_id == INSTANCE_ID
    insert_kwargs = svc.repo.insert_notice.await_args.kwargs
    assert insert_kwargs["status"] == NoticeStatus.LIVE.value
    assert insert_kwargs["recurring_source_notice_id"] == NOTICE_ID
    assert insert_kwargs["series_occurrence_index"] == 1
    svc.repo.copy_notice_children.assert_awaited_once()
    svc._dispatch_publish_push.assert_awaited_once()


@pytest.mark.asyncio
async def test_materialize_skips_when_occurrence_cap_reached():
    svc = _service()
    template = _notice_row(
        recurrence_end_type=NoticeRecurrenceEndType.AFTER_COUNT.value,
        recurrence_max_occurrences=3,
        occurrences_sent=3,
    )
    svc.repo.update_notice_fields = AsyncMock()

    instance_id = await svc._materialize_recurring_template(template)

    assert instance_id is None
    fields = svc.repo.update_notice_fields.await_args.kwargs["fields"]
    assert fields["series_status"] == NoticeSeriesStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_materialize_skips_paused_template():
    svc = _service()
    template = _notice_row(series_status=NoticeSeriesStatus.PAUSED.value)

    assert await svc._materialize_recurring_template(template) is None
    svc.repo.insert_notice.assert_not_called()


@pytest.mark.asyncio
async def test_preview_recurrence_endpoint_shape():
    svc = _service()
    result = await svc.preview_recurrence(
        body=NoticeRecurrencePreviewRequest(repeat=_weekly_repeat()),
    )
    assert result.first_send_at is not None
    assert len(result.upcoming_sends) == 4
