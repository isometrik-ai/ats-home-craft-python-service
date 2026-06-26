"""Unit tests for Graphiti readiness checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from libs.shared_utils.graphiti_health import run_readiness_checks


@pytest.mark.asyncio
async def test_run_readiness_checks_ready_when_graphiti_disabled() -> None:
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
async def test_run_readiness_checks_not_ready_when_database_fails() -> None:
    with patch(
        "libs.shared_utils.graphiti_health.check_database_readiness",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ), patch(
        "libs.shared_utils.graphiti_health.is_graphiti_configured",
        return_value=False,
    ):
        summary = await run_readiness_checks()

    assert summary["ready"] is False
    assert summary["checks"]["database"]["status"] == "error"
