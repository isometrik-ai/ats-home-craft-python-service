"""Unit tests for the awaiting walk-in expiry job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.jobs.expire_awaiting_walk_ins import (
    AWAITING_WALK_IN_TTL_HOURS,
    expire_awaiting_walk_ins,
)


@pytest.mark.asyncio
async def test_expire_awaiting_walk_ins_uses_one_hour_cutoff():
    """The job expires only walk-ins awaiting longer than 1 hour."""
    fixed_now = datetime(2026, 11, 26, 13, 0, tzinfo=timezone.utc)
    expire = AsyncMock(return_value=["walk-in-1"])
    with (
        patch("apps.user_service.app.jobs.expire_awaiting_walk_ins.datetime") as clock,
        patch("apps.user_service.app.jobs.expire_awaiting_walk_ins.WalkInRepository") as repo_cls,
    ):
        clock.now.return_value = fixed_now
        repo_cls.return_value.expire_stale_awaiting_entries = expire
        result = await expire_awaiting_walk_ins(MagicMock())

    assert result == ["walk-in-1"]
    assert AWAITING_WALK_IN_TTL_HOURS == 1
    assert expire.await_args.kwargs == {
        "cutoff": fixed_now - timedelta(hours=1),
        "ttl_hours": 1,
    }
