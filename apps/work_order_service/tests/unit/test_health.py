"""Health endpoint tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_health_check_ok(client) -> None:
    """Health returns healthy when DB responds."""
    with patch(
        "apps.work_order_service.app.main.db_conn",
    ):
        # Patch fetchval on dependency injection path via conn override
        async def _override():
            """Override."""
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        from apps.work_order_service.app.dependencies.db import db_conn
        from apps.work_order_service.app.main import app

        app.dependency_overrides[db_conn] = _override
        try:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "healthy"
            assert body["service"] == "work_order_service"
            assert body["database"] == "healthy"
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_db_unhealthy(client) -> None:
    """Health returns degraded when DB fails."""

    async def _override():
        """Override."""
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        yield conn

    from apps.work_order_service.app.dependencies.db import db_conn
    from apps.work_order_service.app.main import app

    app.dependency_overrides[db_conn] = _override
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unhealthy"
    finally:
        app.dependency_overrides.clear()
