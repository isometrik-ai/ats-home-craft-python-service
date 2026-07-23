"""Unit tests for Graphiti readiness checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.graphiti_health import run_readiness_checks


@pytest.mark.asyncio
async def test_readiness_ready_when_graphiti_off() -> None:
    """Readiness should pass when Graphiti is disabled."""
    with (
        patch(
            "libs.shared_utils.graphiti_health.check_database_readiness",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.is_graphiti_configured",
            return_value=False,
        ),
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is True
    assert summary["checks"]["database"]["status"] == "ok"
    assert summary["checks"]["graphiti"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_readiness_not_ready_on_db_fail() -> None:
    """Readiness should fail when the database check errors."""
    with (
        patch(
            "libs.shared_utils.graphiti_health.check_database_readiness",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch(
            "libs.shared_utils.graphiti_health.is_graphiti_configured",
            return_value=False,
        ),
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is False
    assert summary["checks"]["database"]["status"] == "error"


@pytest.mark.asyncio
async def test_readiness_graphiti_error_marks_not_ready() -> None:
    """Graphiti probe failures should mark readiness false when configured."""
    with (
        patch(
            "libs.shared_utils.graphiti_health.check_database_readiness",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.is_graphiti_configured",
            return_value=True,
        ),
        patch(
            "libs.shared_utils.graphiti_health.check_graphiti_readiness",
            new=AsyncMock(side_effect=RuntimeError("graphiti down")),
        ),
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is False
    assert summary["checks"]["graphiti"]["status"] == "error"


@pytest.mark.asyncio
async def test_readiness_checks_graphiti_when_configured() -> None:
    """Graphiti-enabled deployments should include graphiti readiness."""
    with (
        patch(
            "libs.shared_utils.graphiti_health.check_database_readiness",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.is_graphiti_configured",
            return_value=True,
        ),
        patch(
            "libs.shared_utils.graphiti_health.check_graphiti_readiness",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is True
    assert summary["checks"]["graphiti"]["status"] == "ok"


@pytest.mark.asyncio
async def test_check_database_readiness_success() -> None:
    """check_database_readiness verifies SELECT 1 through the pool."""
    from libs.shared_utils.graphiti_health import check_database_readiness

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)

    class _AcquireCtx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *_args):
            return False

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AcquireCtx())

    with patch(
        "libs.shared_db.drivers.asyncpg_client.get_pool",
        new=AsyncMock(return_value=mock_pool),
    ):
        result = await check_database_readiness()

    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_check_database_readiness_bad_value() -> None:
    """Unexpected readiness query result raises RuntimeError."""
    from libs.shared_utils.graphiti_health import check_database_readiness

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=0)

    class _AcquireCtx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *_args):
            return False

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AcquireCtx())

    with (
        patch(
            "libs.shared_db.drivers.asyncpg_client.get_pool", new=AsyncMock(return_value=mock_pool)
        ),
        pytest.raises(RuntimeError),
    ):
        await check_database_readiness()


@pytest.mark.asyncio
async def test_check_graphiti_readiness_disabled() -> None:
    """Graphiti readiness returns disabled when not configured."""
    from libs.shared_utils.graphiti_health import check_graphiti_readiness

    with patch("libs.shared_utils.graphiti_health.is_graphiti_configured", return_value=False):
        result = await check_graphiti_readiness()
    assert result == {"status": "disabled"}


@pytest.mark.asyncio
async def test_check_graphiti_readiness_ok_and_degraded() -> None:
    """Graphiti readiness reports ok/degraded from index verification."""
    from libs.shared_utils.graphiti_health import check_graphiti_readiness

    mock_driver = AsyncMock()
    mock_driver.health_check = AsyncMock()

    with (
        patch("libs.shared_utils.graphiti_health.is_graphiti_configured", return_value=True),
        patch("libs.shared_utils.graphiti_health.is_graphiti_initialized", return_value=True),
        patch("libs.shared_utils.graphiti_health.get_driver", return_value=mock_driver),
        patch(
            "libs.shared_utils.graphiti_health.verify_graphiti_indices",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.shared_settings",
            MagicMock(graphiti=MagicMock(falkor_database="graph")),
        ),
    ):
        ok_result = await check_graphiti_readiness()
    assert ok_result["status"] == "ok"

    with (
        patch("libs.shared_utils.graphiti_health.is_graphiti_configured", return_value=True),
        patch("libs.shared_utils.graphiti_health.is_graphiti_initialized", return_value=True),
        patch("libs.shared_utils.graphiti_health.get_driver", return_value=mock_driver),
        patch(
            "libs.shared_utils.graphiti_health.verify_graphiti_indices",
            new=AsyncMock(return_value={"ok": False}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.shared_settings",
            MagicMock(graphiti=MagicMock(falkor_database="graph")),
        ),
    ):
        degraded = await check_graphiti_readiness()
    assert degraded["status"] == "degraded"


@pytest.mark.asyncio
async def test_readiness_strict_index_verify_marks_not_ready() -> None:
    """Strict index verification forces readiness false when graphiti degraded."""
    graphiti_settings = MagicMock()
    graphiti_settings.strict_index_verify = True
    graphiti_settings.falkor_database = "graph"

    with (
        patch(
            "libs.shared_utils.graphiti_health.check_database_readiness",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
        patch("libs.shared_utils.graphiti_health.is_graphiti_configured", return_value=True),
        patch(
            "libs.shared_utils.graphiti_health.check_graphiti_readiness",
            new=AsyncMock(return_value={"status": "degraded"}),
        ),
        patch(
            "libs.shared_utils.graphiti_health.shared_settings",
            MagicMock(graphiti=graphiti_settings),
        ),
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is False
