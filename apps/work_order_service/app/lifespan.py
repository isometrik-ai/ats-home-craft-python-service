"""Application lifespan — DB pool, scheduler loop, webhook worker."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.work_order_service.app.config.app_settings import app_settings
from apps.work_order_service.app.services.scheduler_worker import run_scheduler_loop
from apps.work_order_service.app.services.webhook_worker import (
    start_webhook_worker,
    stop_webhook_worker,
)
from libs.shared_db.drivers.asyncpg_client import close_pool, get_pool
from libs.shared_utils.logger import app_logger
from libs.shared_utils.telemetry_config import telemetry_config


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup and shutdown hooks."""
    app_logger.info("Starting work_order_service")
    await get_pool()
    app_logger.info("Database pool ready")

    scheduler_task: asyncio.Task | None = None
    if app_settings.scheduler.enabled:
        scheduler_task = asyncio.create_task(run_scheduler_loop())
        app_logger.info("Scheduler loop started")

    await start_webhook_worker()

    try:
        yield
    finally:
        app_logger.info("Shutting down work_order_service")
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
        await stop_webhook_worker()
        await close_pool()
        telemetry_config.shutdown()
