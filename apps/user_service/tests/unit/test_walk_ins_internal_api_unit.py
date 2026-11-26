"""Unit tests for the internal walk-in expiry API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.walk_ins import expire_awaiting_walk_ins_internal


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/walk-ins/expire-awaiting",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


@pytest.mark.asyncio
async def test_expire_awaiting_walk_ins_internal():
    with patch(
        "apps.user_service.app.api.walk_ins.expire_awaiting_walk_ins",
        new_callable=AsyncMock,
        return_value=["wi-1", "wi-2"],
    ):
        response = await expire_awaiting_walk_ins_internal(
            request=_request(),
            db_connection=MagicMock(),
        )

    assert response.status_code == 200
    body = response.body.decode().replace(" ", "")
    assert '"count":2' in body
    assert "wi-1" in body
    assert "wi-2" in body
