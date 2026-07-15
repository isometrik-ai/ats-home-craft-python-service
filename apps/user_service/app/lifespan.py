"""Lifespan event handler for the FastAPI application.

This module contains the lifespan event handler that manages startup and shutdown
events for the application.
"""

from asyncio import Event
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.user_service.app.db.repositories import init_session_repo
from apps.user_service.app.dependencies.audit_logs.audit_logger import audit_logger
from libs.shared_db.drivers.asyncpg_client import close_pool, get_pool
from libs.shared_db.drivers.redis_client import init_redis
from libs.shared_utils.graphiti_service import (
    close_graphiti_client,
    init_graphiti_client,
)
from libs.shared_utils.isometrik_strands_client import (
    close_strands_http_client,
    init_strands_http_client,
)
from libs.shared_utils.logger import app_logger
from libs.shared_utils.openai_chat_service import (
    close_openai_http_client,
    init_openai_http_client,
)
from libs.shared_utils.telemetry_config import telemetry_config
from libs.shared_utils.typesense_service import (
    close_typesense_http_client,
    get_typesense_http_client,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan event handler."""
    initialization_complete = Event()
    app.state.initialization_complete = initialization_complete
    # Startup
    app_logger.info("Starting up user service application")

    # Initialize DB pool (audit_logger will use the same pool)
    await get_pool()
    app_logger.info("Database pool initialized successfully")

    # Start audit logger processing (pool is already initialized)
    await audit_logger.start_processing()
    app_logger.info("Audit logger processing started successfully")

    # Initialize cached Typesense HTTP client (connection pooled)
    await get_typesense_http_client()
    app_logger.info("Typesense HTTP client initialized successfully")

    # Initialize Graphiti + FalkorDB client when enabled
    await init_graphiti_client()
    app_logger.info("Graphiti client startup complete")

    await init_openai_http_client()
    app_logger.info("OpenAI HTTP client startup complete")

    await init_strands_http_client()
    app_logger.info("Isometrik admin HTTP client startup complete")

    await init_redis()
    app_logger.info("Redis client initialized successfully")

    init_session_repo()
    app_logger.info("Session repository initialized successfully")

    try:
        yield
    finally:
        # Shutdown (if needed)
        app_logger.info("Shutting down user service application")
        await close_strands_http_client()
        app_logger.info("Isometrik admin HTTP client closed successfully")
        await close_openai_http_client()
        app_logger.info("OpenAI HTTP client closed successfully")
        await close_graphiti_client()
        app_logger.info("Graphiti client closed successfully")
        await close_typesense_http_client()
        app_logger.info("Typesense HTTP client closed successfully")
        await close_pool()
        app_logger.info("Database pool closed successfully")
        telemetry_config.shutdown()
