"""Unit tests for internal community events job API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.community_events_internal import (
    complete_past_community_events_internal,
    send_community_event_reminders_internal,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/community-events/complete-past",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


@pytest.mark.asyncio
async def test_complete_past_community_events_internal():
    with (
        patch(
            "apps.user_service.app.api.community_events_internal.require_super_admin",
            new_callable=AsyncMock,
        ),
        patch(
            "apps.user_service.app.api.community_events_internal.complete_past_community_events",
            new_callable=AsyncMock,
            return_value=["evt-1"],
        ),
    ):
        response = await complete_past_community_events_internal(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "admin-1"},
        )

    assert response.status_code == 200
    body = response.body.decode().replace(" ", "")
    assert '"count":1' in body
    assert "evt-1" in body


@pytest.mark.asyncio
async def test_send_community_event_reminders_internal():
    with (
        patch(
            "apps.user_service.app.api.community_events_internal.require_super_admin",
            new_callable=AsyncMock,
        ),
        patch(
            "apps.user_service.app.api.community_events_internal.send_community_event_reminders",
            new_callable=AsyncMock,
            return_value={"sent_count": 3, "event_ids": ["e-1"]},
        ),
    ):
        response = await send_community_event_reminders_internal(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "admin-1"},
        )

    assert response.status_code == 200
    assert "sent_count" in response.body.decode()
