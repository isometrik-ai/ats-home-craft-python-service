"""Unit tests for notice board background jobs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.publish_scheduled_notices import (
    dispatch_due_notice_pushes,
    expire_notice_pins,
    publish_scheduled_notices,
)

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
NOTICE = "33333333-3333-3333-3333-333333333333"


@pytest.mark.asyncio
async def test_publish_scheduled_notices_empty():
    conn = MagicMock()
    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.claim_due_notice_sends = AsyncMock(return_value=[])
        result = await publish_scheduled_notices(conn)
    assert result == []


@pytest.mark.asyncio
async def test_publish_scheduled_notices_dispatches_push():
    conn = MagicMock()
    notice_row = {
        "organization_id": ORG,
        "project_id": PROJECT,
        "title": "Pool closure",
        "scope_type": "whole_society",
    }

    with (
        patch("apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository") as repo_cls,
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticeRecipientResolutionService"
        ) as recipient_cls,
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.PushNotificationDispatcher"
        ) as push_cls,
    ):
        repo = repo_cls.return_value
        repo.claim_due_notice_sends = AsyncMock(
            return_value=[{"id": NOTICE, "fired_at": None, "repeating": False}]
        )
        repo.list_notices_published_since = AsyncMock(return_value=[notice_row])
        repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
        repo.list_contact_recipient_ids = AsyncMock(return_value=[])
        repo.list_unit_ids = AsyncMock(return_value=[])
        repo.list_towers_for_notice = AsyncMock(return_value=[])
        repo.fetch_notice_by_id = AsyncMock(return_value=notice_row)

        recipient = recipient_cls.return_value
        recipient.resolve_recipient_user_ids = AsyncMock(return_value=["user-1", "user-2"])

        push = push_cls.return_value
        push.send_to_user = AsyncMock()

        result = await publish_scheduled_notices(
            conn,
            organization_id=ORG,
            project_id=PROJECT,
        )

    assert result == [NOTICE]
    assert push.send_to_user.await_count == 2


@pytest.mark.asyncio
async def test_publish_scheduled_notices_queues_push():
    """Success: publish-due returns before push delivery when a background queue is set."""
    conn = MagicMock()
    background_tasks = MagicMock()
    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.claim_due_notice_sends = AsyncMock(
            return_value=[{"id": NOTICE, "fired_at": None, "repeating": False}]
        )
        result = await publish_scheduled_notices(conn, background_tasks=background_tasks)

    assert result == [NOTICE]
    background_tasks.add_task.assert_called_once()
    queued = background_tasks.add_task.call_args.args
    assert queued[0] is dispatch_due_notice_pushes
    assert queued[1] == [{"id": NOTICE, "fired_at": None, "repeating": False}]
    assert queued[2] is conn


@pytest.mark.asyncio
async def test_publish_scheduled_notices_skips_missing_notice_rows():
    conn = MagicMock()
    with (
        patch("apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository") as repo_cls,
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.NoticeRecipientResolutionService"
        ),
        patch(
            "apps.user_service.app.jobs.publish_scheduled_notices.PushNotificationDispatcher"
        ) as push_cls,
    ):
        repo = repo_cls.return_value
        repo.claim_due_notice_sends = AsyncMock(
            return_value=[{"id": NOTICE, "fired_at": None, "repeating": False}]
        )
        repo.list_notices_published_since = AsyncMock(return_value=[])
        repo.fetch_notice_by_id = AsyncMock(return_value=None)
        push = push_cls.return_value
        push.send_to_user = AsyncMock()

        result = await publish_scheduled_notices(conn)

    assert result == [NOTICE]
    push.send_to_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_expire_notice_pins():
    conn = MagicMock()
    with patch(
        "apps.user_service.app.jobs.publish_scheduled_notices.NoticesRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.expire_due_pins = AsyncMock(return_value=3)
        count = await expire_notice_pins(conn)
    assert count == 3
