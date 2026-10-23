"""Unit tests for recurring notice delivery job."""

from __future__ import annotations

from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.publish_scheduled_notices import (
    process_recurring_notices,
)


@pytest.mark.asyncio
async def test_process_recurring_notices_empty():
    repo = MagicMock()
    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository",
        return_value=repo,
    ):
        repo.list_due_recurring_notice_ids = AsyncMock(return_value=[])
        result = await process_recurring_notices(MagicMock())
    assert result == []


@pytest.mark.asyncio
async def test_process_recurring_skips_when_deliver_fails():
    notice_id = "44444444-4444-4444-4444-444444444444"
    repo = MagicMock()
    repo.list_due_recurring_notice_ids = AsyncMock(return_value=[notice_id])
    repo.fetch_notice_by_id_only = AsyncMock(
        return_value={
            "organization_id": "org-1",
            "project_id": "11111111-1111-1111-1111-111111111111",
            "title": "T",
            "scope_type": "whole_society",
            "series_frequency": "weekly",
            "series_monthly_day": 1,
            "series_time_of_day": time(9, 0),
            "series_end_type": "after_count",
            "series_end_after_sends": 5,
            "series_sends_completed": 0,
            "series_next_send_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "series_repeat_ended_at": None,
        }
    )
    repo.list_series_weekly_days = AsyncMock(return_value=[1])
    repo.deliver_recurring_notice = AsyncMock(return_value=False)
    repo.fetch_organization_timezone = AsyncMock(return_value="UTC")

    with (
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository",
            return_value=repo,
        ),
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.PushNotificationDispatcher",
        ),
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticeRecipientResolutionService",
        ),
    ):
        result = await process_recurring_notices(MagicMock(), organization_id="org-1")
    assert result == []
