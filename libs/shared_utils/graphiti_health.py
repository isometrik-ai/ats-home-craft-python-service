"""Readiness checks for Graphiti + FalkorDB."""

from __future__ import annotations

from typing import Any

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.graphiti_index_maintenance import verify_graphiti_indices
from libs.shared_utils.graphiti_service import (
    get_driver,
    is_graphiti_configured,
    is_graphiti_initialized,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_health")


async def check_database_readiness() -> dict[str, Any]:
    """Verify Postgres connectivity."""
    from libs.shared_db.drivers.asyncpg_client import get_pool

    pool = await get_pool()
    async with pool.acquire() as connection:
        value = await connection.fetchval("SELECT 1")
    if value != 1:
        raise RuntimeError("database readiness query returned unexpected value")
    return {"status": "ok"}


async def check_graphiti_readiness() -> dict[str, Any]:
    """Verify FalkorDB connectivity and required indexes when Graphiti is enabled."""
    if not is_graphiti_configured():
        return {"status": "disabled"}

    if not is_graphiti_initialized():
        raise RuntimeError("Graphiti driver is not initialized")

    driver = get_driver()
    await driver.health_check()
    index_summary = await verify_graphiti_indices(driver)
    status = "ok" if index_summary.get("ok") else "degraded"
    return {
        "status": status,
        "database": shared_settings.graphiti.falkor_database,
        "indexes": index_summary,
    }


async def run_readiness_checks() -> dict[str, Any]:
    """Run all readiness probes and return a structured summary."""
    checks: dict[str, Any] = {}

    try:
        checks["database"] = await check_database_readiness()
    except Exception as exc:
        logger.warning("readiness_database_failed error=%s", exc)
        checks["database"] = {"status": "error", "error": str(exc)}

    if is_graphiti_configured():
        try:
            checks["graphiti"] = await check_graphiti_readiness()
        except Exception as exc:
            logger.warning("readiness_graphiti_failed error=%s", exc)
            checks["graphiti"] = {"status": "error", "error": str(exc)}
    else:
        checks["graphiti"] = {"status": "disabled"}

    ready = checks.get("database", {}).get("status") == "ok" and checks.get("graphiti", {}).get(
        "status"
    ) in ("ok", "disabled")
    if (
        is_graphiti_configured()
        and shared_settings.graphiti.strict_index_verify
        and checks.get("graphiti", {}).get("status") != "ok"
    ):
        ready = False

    return {"ready": ready, "checks": checks}
