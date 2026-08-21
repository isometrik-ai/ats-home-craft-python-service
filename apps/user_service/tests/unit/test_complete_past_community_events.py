"""Unit tests for complete_past_community_events job."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.complete_past_community_events import (
    complete_past_community_events,
)


@pytest.mark.asyncio
async def test_complete_past_community_events_delegates_to_repository():
    conn = MagicMock()
    with patch(
        "apps.user_service.app.jobs.complete_past_community_events.CommunityEventsRepository"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.complete_past_events = AsyncMock(return_value=["evt-1", "evt-2"])
        result = await complete_past_community_events(conn)

    repo_cls.assert_called_once_with(conn)
    assert result == ["evt-1", "evt-2"]
