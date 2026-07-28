"""Unit tests for Supabase client factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from libs.shared_db.supabase_db import client as supabase_client


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset cached clients between tests."""
    supabase_client._cache.reset_all()
    yield
    supabase_client._cache.reset_all()


@pytest.mark.asyncio
async def test_get_supabase_client_creates_and_caches_anon_client():
    """Anon client is created once and reused."""
    mock_client = MagicMock()
    with (
        patch.object(supabase_client, "SUPABASE_URL", "https://example.supabase.co"),
        patch.object(supabase_client, "SUPABASE_ANON_KEY", "anon-key"),
        patch.object(
            supabase_client,
            "create_async_client",
            AsyncMock(return_value=mock_client),
        ) as create_mock,
    ):
        first = await supabase_client.get_supabase_client()
        second = await supabase_client.get_supabase_client()

    assert first is mock_client
    assert second is mock_client
    create_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_supabase_client_missing_config_raises():
    """Missing URL/key raises runtime error."""
    with (
        patch.object(supabase_client, "SUPABASE_URL", ""),
        patch.object(supabase_client, "SUPABASE_ANON_KEY", ""),
    ):
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            await supabase_client.get_supabase_client()


@pytest.mark.asyncio
async def test_supabase_anon_with_headers_builds_client():
    """Per-request client forwards optional headers."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"user-agent", b"MyAgent")],
        }
    )
    mock_client = MagicMock()
    with (
        patch.object(supabase_client, "SUPABASE_URL", "https://example.supabase.co"),
        patch.object(supabase_client, "SUPABASE_ANON_KEY", "anon-key"),
        patch.object(
            supabase_client,
            "create_async_client",
            AsyncMock(return_value=mock_client),
        ) as create_mock,
    ):
        client = await supabase_client.supabase_anon_with_headers(request)

    assert client is mock_client
    _, kwargs = create_mock.await_args
    assert kwargs["options"].headers == {"User-Agent": "MyAgent"}


@pytest.mark.asyncio
async def test_get_supabase_service_client_warmup_failure_clears_cache():
    """Service client warm-up failure resets cache and raises."""
    mock_client = MagicMock()
    mock_client.auth.admin.list_users = AsyncMock(side_effect=RuntimeError("bad key"))
    with (
        patch.object(supabase_client, "SUPABASE_URL", "https://example.supabase.co"),
        patch.object(supabase_client, "SUPABASE_SERVICE_KEY", "service-key"),
        patch.object(
            supabase_client,
            "create_async_client",
            AsyncMock(return_value=mock_client),
        ),
    ):
        with pytest.raises(RuntimeError, match="warm-up failed"):
            await supabase_client.get_supabase_service_client()

    assert supabase_client._cache.service is None


def test_reset_and_fresh_service_client():
    """Reset helpers clear cached service client."""
    supabase_client._cache.service = MagicMock()
    supabase_client.reset_supabase_service_client()
    assert supabase_client._cache.service is None
