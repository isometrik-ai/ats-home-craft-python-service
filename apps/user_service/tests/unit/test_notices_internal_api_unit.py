"""Unit tests for internal notices job API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.notices_internal import (
    expire_notice_pins_internal,
    publish_due_notices_internal,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/notices/publish-due",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


@pytest.mark.asyncio
async def test_publish_due_notices_internal():
    with (
        patch(
            "apps.user_service.app.api.notices_internal.require_super_admin",
            new_callable=AsyncMock,
        ),
        patch(
            "apps.user_service.app.api.notices_internal.publish_scheduled_notices",
            new_callable=AsyncMock,
            return_value=["n-1", "n-2"],
        ),
    ):
        response = await publish_due_notices_internal(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "admin-1"},
        )

    assert response.status_code == 200
    body = response.body.decode()
    assert "n-1" in body
    assert '"count":2' in body.replace(" ", "")


@pytest.mark.asyncio
async def test_expire_notice_pins_internal():
    with (
        patch(
            "apps.user_service.app.api.notices_internal.require_super_admin",
            new_callable=AsyncMock,
        ),
        patch(
            "apps.user_service.app.api.notices_internal.expire_notice_pins",
            new_callable=AsyncMock,
            return_value=5,
        ),
    ):
        response = await expire_notice_pins_internal(
            request=_request(),
            db_connection=MagicMock(),
            current_user={"sub": "admin-1"},
        )

    assert response.status_code == 200
    assert '"expired_count":5' in response.body.decode().replace(" ", "")
