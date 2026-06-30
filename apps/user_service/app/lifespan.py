"""Lifespan event handler for the FastAPI application."""

from asyncio import Event
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.user_service.app.db.repositories import init_session_repo
from apps.user_service.app.dependencies.audit_logs.audit_logger import audit_logger
from libs.shared_db.drivers.asyncpg_client import close_pool, get_pool
from libs.shared_db.drivers.redis_client import init_redis
from libs.shared_utils.logger import app_logger
from libs.shared_utils.telemetry_config import telemetry_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan event handler."""
    initialization_complete = Event()
    app.state.initialization_complete = initialization_complete
    app_logger.info("Starting up user service application")

    await get_pool()
    app_logger.info("Database pool initialized successfully")

    await audit_logger.start_processing()
    app_logger.info("Audit logger processing started successfully")

    await init_redis()
    app_logger.info("Redis client initialized successfully")

    init_session_repo()
    app_logger.info("Session repository initialized successfully")

    try:
        yield
    finally:
        app_logger.info("Shutting down user service application")
        await close_pool()
        app_logger.info("Database pool closed successfully")
        telemetry_config.shutdown()
