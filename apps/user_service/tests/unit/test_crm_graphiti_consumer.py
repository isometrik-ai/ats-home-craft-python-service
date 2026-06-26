"""Unit tests for CRM Graphiti consumer commit semantics."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.consumers.crm_supermemory_consumer import CrmSupermemoryConsumer


def _message(*, offset: int = 0, payload: dict | None = None) -> SimpleNamespace:
    body = json.dumps(payload or {"event_id": "e1", "organization_id": "org-1"}).encode()
    return SimpleNamespace(topic="crm.events.dev", partition=0, offset=offset, value=body)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        pool=MagicMock(),
        sync_service=MagicMock(),
        kafka_service=MagicMock(),
        semaphore=MagicMock(),
    )


@pytest.mark.asyncio
async def test_process_message_commits_on_success() -> None:
    consumer = CrmSupermemoryConsumer()
    with patch.object(CrmSupermemoryConsumer, "_sync_crm_event", new=AsyncMock()):
        should_commit = await consumer._process_message(ctx=_ctx(), message=_message())
    assert should_commit is True


@pytest.mark.asyncio
async def test_process_message_commits_after_dlq_on_sync_failure() -> None:
    consumer = CrmSupermemoryConsumer()
    with (
        patch.object(
            CrmSupermemoryConsumer,
            "_sync_crm_event",
            new=AsyncMock(side_effect=RuntimeError("graph down")),
        ),
        patch.object(CrmSupermemoryConsumer, "_publish_dlq", new=AsyncMock(return_value=True)) as publish_dlq,
    ):
        should_commit = await consumer._process_message(ctx=_ctx(), message=_message())
    assert should_commit is True
    publish_dlq.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_does_not_commit_when_dlq_publish_fails() -> None:
    consumer = CrmSupermemoryConsumer()
    with (
        patch.object(
            CrmSupermemoryConsumer,
            "_sync_crm_event",
            new=AsyncMock(side_effect=RuntimeError("graph down")),
        ),
        patch.object(CrmSupermemoryConsumer, "_publish_dlq", new=AsyncMock(return_value=False)),
    ):
        should_commit = await consumer._process_message(ctx=_ctx(), message=_message())
    assert should_commit is False
