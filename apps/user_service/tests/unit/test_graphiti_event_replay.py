"""Unit tests for Graphiti event replay from the events table."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.graphiti_event_replay import (
    GraphitiEventReplayStats,
    _event_envelope,
    replay_pending_crm_events_on_startup,
    replay_stored_crm_events,
)


def test_event_envelope_prefers_jsonb_payload() -> None:
    """Replay envelopes should prefer nested JSONB payload fields."""
    envelope = _event_envelope(
        {
            "event_id": "e1",
            "event_type": "contacts.updated",
            "aggregate_id": "c1",
            "organization_id": "org-1",
            "payload": {
                "event_id": "e1",
                "event_type": "contacts.updated",
                "aggregate_id": "c1",
                "organization_id": "org-1",
                "payload": {"module": "contacts"},
            },
        }
    )
    assert envelope["event_type"] == "contacts.updated"
    assert envelope["payload"]["module"] == "contacts"


@pytest.mark.asyncio
async def test_replay_crm_events_processes_rows() -> None:
    """Mapped CRM events should be replayed and marked synced."""
    sync_service = MagicMock()
    sync_service.process_crm_event = AsyncMock()
    conn = MagicMock()
    rows = [
        {
            "event_id": "e1",
            "event_type": "contacts.updated",
            "aggregate_id": "c1",
            "organization_id": "org-1",
            "status": "published",
            "payload": {
                "event_id": "e1",
                "event_type": "contacts.updated",
                "aggregate_id": "c1",
                "organization_id": "org-1",
                "payload": {"module": "contacts", "action": "update"},
            },
        }
    ]

    with (
        patch("libs.shared_utils.graphiti_event_replay.EventsRepository") as repo_cls,
        patch(
            "libs.shared_utils.graphiti_event_replay.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
    ):
        repo = repo_cls.return_value
        repo.fetch_crm_events_for_graphiti_replay = AsyncMock(return_value=rows)
        repo.mark_graphiti_synced = AsyncMock()
        stats = await replay_stored_crm_events(conn, sync_service)

    assert stats == GraphitiEventReplayStats(selected=1, processed=1)
    sync_service.process_crm_event.assert_awaited_once()
    repo.mark_graphiti_synced.assert_awaited_once_with(event_id="e1")


@pytest.mark.asyncio
async def test_replay_crm_events_skips_unmapped() -> None:
    """Events without sync targets should be skipped."""
    sync_service = MagicMock()
    sync_service.process_crm_event = AsyncMock()
    conn = MagicMock()
    rows = [
        {
            "event_id": "e2",
            "event_type": "email.notification.received",
            "aggregate_id": "a1",
            "organization_id": "org-1",
            "status": "published",
            "payload": {
                "event_id": "e2",
                "event_type": "email.notification.received",
                "aggregate_id": "a1",
                "organization_id": "org-1",
                "payload": {},
            },
        }
    ]

    with patch("libs.shared_utils.graphiti_event_replay.EventsRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.fetch_crm_events_for_graphiti_replay = AsyncMock(return_value=rows)
        stats = await replay_stored_crm_events(conn, sync_service)

    assert stats.selected == 1
    assert stats.processed == 0
    assert stats.skipped_no_targets == 1
    sync_service.process_crm_event.assert_not_called()


@pytest.mark.asyncio
async def test_startup_backfill_skipped_when_disabled() -> None:
    """Startup backfill should no-op when disabled in settings."""
    with patch(
        "libs.shared_utils.graphiti_event_replay.shared_settings.graphiti.startup_backfill_enabled",
        False,
    ):
        stats = await replay_pending_crm_events_on_startup(MagicMock(), MagicMock())

    assert stats == GraphitiEventReplayStats()


@pytest.mark.asyncio
async def test_startup_backfill_runs_when_enabled() -> None:
    """Startup backfill should replay pending events when enabled."""
    with (
        patch(
            "libs.shared_utils.graphiti_event_replay.shared_settings.graphiti."
            "startup_backfill_enabled",
            True,
        ),
        patch(
            "libs.shared_utils.graphiti_event_replay.replay_stored_crm_events",
            new=AsyncMock(return_value=GraphitiEventReplayStats(selected=2, processed=2)),
        ) as replay,
    ):
        stats = await replay_pending_crm_events_on_startup(MagicMock(), MagicMock())

    assert stats.processed == 2
    replay.assert_awaited_once()
    assert replay.await_args.kwargs["statuses"] == ["pending"]
    assert replay.await_args.kwargs["mode"] == "chronological"
