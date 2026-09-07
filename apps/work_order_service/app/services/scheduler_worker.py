"""Background scheduler loop with Postgres advisory lock."""

from __future__ import annotations

import asyncio

from apps.work_order_service.app.config.app_settings import app_settings
from apps.work_order_service.app.services.scheduler_service import SchedulerService
from libs.shared_db.drivers.asyncpg_client import get_pool
from libs.shared_utils.logger import app_logger

# Stable advisory lock key for work-order scheduler (two-int form).
_SCHEDULER_LOCK_KEY = (824_731, 1)


async def _try_advisory_lock(conn) -> bool:
    """Try advisory lock."""
    return await conn.fetchval(
        "SELECT pg_try_advisory_lock($1, $2)",
        _SCHEDULER_LOCK_KEY[0],
        _SCHEDULER_LOCK_KEY[1],
    )


async def _release_advisory_lock(conn) -> None:
    """Release advisory lock."""
    await conn.execute(
        "SELECT pg_advisory_unlock($1, $2)",
        _SCHEDULER_LOCK_KEY[0],
        _SCHEDULER_LOCK_KEY[1],
    )


async def run_scheduler_once() -> dict[str, int]:
    """Run one scheduler sweep under advisory lock."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO work_order, public")
        if not await _try_advisory_lock(conn):
            app_logger.info("Scheduler skipped — another instance holds the lock")
            return {"created": 0, "cancelled": 0, "skipped": 0, "locked": 1}
        try:
            service = SchedulerService(conn)
            return await service.generate_due_work_orders()
        finally:
            await _release_advisory_lock(conn)


async def run_scheduler_loop() -> None:
    """Periodic scheduler loop."""
    interval = max(1, app_settings.scheduler.interval_minutes) * 60
    app_logger.info("Scheduler loop interval=%ss", interval)
    while True:
        try:
            result = await run_scheduler_once()
            if result.get("created") or result.get("cancelled"):
                app_logger.info("Scheduler run: %s", result)
        except Exception:
            app_logger.exception("Scheduler run failed")
        await asyncio.sleep(interval)
