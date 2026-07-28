"""Unit tests for Supabase FastAPI dependencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from apps.user_service.app.dependencies import supabase as supabase_deps


@pytest.mark.asyncio
async def test_supabase_service_returns_client():
    """Service dependency delegates to cached service client."""
    mock_client = MagicMock()
    with patch(
        "apps.user_service.app.dependencies.supabase.get_supabase_service_client",
        AsyncMock(return_value=mock_client),
    ):
        client = await supabase_deps.supabase_service()
    assert client is mock_client


@pytest.mark.asyncio
async def test_supabase_anon_returns_client():
    """Anon dependency delegates to cached anon client."""
    mock_client = MagicMock()
    with patch(
        "apps.user_service.app.dependencies.supabase.get_supabase_client",
        AsyncMock(return_value=mock_client),
    ):
        client = await supabase_deps.supabase_anon()
    assert client is mock_client


@pytest.mark.asyncio
async def test_supabase_anon_client_with_headers_forwards_request():
    """Per-request client includes forwarded headers."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (b"user-agent", b"TestAgent/1.0"),
                (b"x-device-signature", b"sig-123"),
            ],
        }
    )
    mock_client = MagicMock()
    with patch(
        "apps.user_service.app.dependencies.supabase.supabase_anon_with_headers",
        AsyncMock(return_value=mock_client),
    ) as mock_factory:
        client = await supabase_deps.supabase_anon_client_with_headers(request)

    assert client is mock_client
    mock_factory.assert_awaited_once_with(request)
