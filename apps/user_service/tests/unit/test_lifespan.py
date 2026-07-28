"""Unit tests for FastAPI lifespan startup/shutdown."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from apps.user_service.app.lifespan import lifespan


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown():
    """Lifespan initializes services on startup and closes them on shutdown."""
    app = FastAPI()

    patches = {
        "get_pool": AsyncMock(return_value=MagicMock()),
        "audit_logger.start_processing": AsyncMock(),
        "get_typesense_http_client": AsyncMock(return_value=MagicMock()),
        "init_graphiti_client": AsyncMock(),
        "init_openai_http_client": AsyncMock(),
        "init_strands_http_client": AsyncMock(),
        "init_redis": AsyncMock(),
        "init_session_repo": MagicMock(),
        "close_strands_http_client": AsyncMock(),
        "close_openai_http_client": AsyncMock(),
        "close_graphiti_client": AsyncMock(),
        "close_typesense_http_client": AsyncMock(),
        "close_pool": AsyncMock(),
        "telemetry_config.shutdown": MagicMock(),
    }

    with (
        patch("apps.user_service.app.lifespan.get_pool", patches["get_pool"]),
        patch(
            "apps.user_service.app.lifespan.audit_logger.start_processing",
            patches["audit_logger.start_processing"],
        ),
        patch(
            "apps.user_service.app.lifespan.get_typesense_http_client",
            patches["get_typesense_http_client"],
        ),
        patch(
            "apps.user_service.app.lifespan.init_graphiti_client", patches["init_graphiti_client"]
        ),
        patch(
            "apps.user_service.app.lifespan.init_openai_http_client",
            patches["init_openai_http_client"],
        ),
        patch(
            "apps.user_service.app.lifespan.init_strands_http_client",
            patches["init_strands_http_client"],
        ),
        patch("apps.user_service.app.lifespan.init_redis", patches["init_redis"]),
        patch("apps.user_service.app.lifespan.init_session_repo", patches["init_session_repo"]),
        patch(
            "apps.user_service.app.lifespan.close_strands_http_client",
            patches["close_strands_http_client"],
        ),
        patch(
            "apps.user_service.app.lifespan.close_openai_http_client",
            patches["close_openai_http_client"],
        ),
        patch(
            "apps.user_service.app.lifespan.close_graphiti_client",
            patches["close_graphiti_client"],
        ),
        patch(
            "apps.user_service.app.lifespan.close_typesense_http_client",
            patches["close_typesense_http_client"],
        ),
        patch("apps.user_service.app.lifespan.close_pool", patches["close_pool"]),
        patch(
            "apps.user_service.app.lifespan.telemetry_config.shutdown",
            patches["telemetry_config.shutdown"],
        ),
    ):
        async with lifespan(app):
            assert app.state.initialization_complete.is_set() is False
            app.state.initialization_complete.set()
            assert app.state.initialization_complete.is_set() is True

    patches["get_pool"].assert_awaited_once()
    patches["audit_logger.start_processing"].assert_awaited_once()
    patches["init_redis"].assert_awaited_once()
    patches["init_session_repo"].assert_called_once()
    patches["close_pool"].assert_awaited_once()
    patches["telemetry_config.shutdown"].assert_called_once()
