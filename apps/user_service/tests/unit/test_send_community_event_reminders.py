"""Unit tests for community event reminder job."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.send_community_event_reminders import (
    send_community_event_reminders,
)

ORG = "11111111-1111-1111-1111-111111111111"
EVENT = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_send_community_event_reminders_no_events():
    conn = MagicMock()
    with (
        patch(
            "apps.user_service.app.jobs.send_community_event_reminders.CommunityEventsRepository"
        ) as repo_cls,
        patch(
            "apps.user_service.app.jobs.send_community_event_reminders.CommunityEventNotificationService"
        ),
    ):
        repo = repo_cls.return_value
        repo.list_events_due_for_reminder = AsyncMock(return_value=[])
        result = await send_community_event_reminders(conn)
    assert result == {"events_processed": 0, "reminders_sent": 0}


@pytest.mark.asyncio
async def test_send_community_event_reminders_sends_for_bookers():
    conn = MagicMock()
    event = {"id": EVENT, "organization_id": ORG, "title": "Summer fest"}

    with (
        patch(
            "apps.user_service.app.jobs.send_community_event_reminders.CommunityEventsRepository"
        ) as repo_cls,
        patch(
            "apps.user_service.app.jobs.send_community_event_reminders.CommunityEventNotificationService"
        ) as notify_cls,
    ):
        repo = repo_cls.return_value
        repo.list_events_due_for_reminder = AsyncMock(return_value=[event])
        repo.list_confirmed_bookers_for_reminder = AsyncMock(
            return_value=[
                {"contact_id": "contact-1", "user_id": "user-1"},
                {"contact_id": "contact-2", "user_id": "user-2"},
            ]
        )
        notifications = notify_cls.return_value
        notifications.notify_event_reminder = AsyncMock()

        result = await send_community_event_reminders(conn, hours_ahead=24)

    assert result == {"events_processed": 1, "reminders_sent": 2}
    assert notifications.notify_event_reminder.await_count == 2
