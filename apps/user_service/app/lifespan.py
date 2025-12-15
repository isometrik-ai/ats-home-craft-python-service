"""Lifespan event handler for the FastAPI application.

This module contains the lifespan event handler that manages startup and shutdown
events for the application.
"""

from asyncio import Event
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.user_service.app.dependencies.audit_logs.audit_logger import audit_logger
from apps.user_service.app.dependencies.logger import app_logger
from libs.shared_db.drivers.asyncpg_client import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan event handler."""
    initialization_complete = Event()
    app.state.initialization_complete = initialization_complete
    # Startup
    app_logger.info("Starting up user service application")

    audit_logger.start_processing()
    app_logger.info("Audit logger processing started successfully")

    # Initialize DB pool
    await get_pool()
    app_logger.info("Database pool initialized successfully")

    try:
        yield
    finally:
        # Shutdown (if needed)
        app_logger.info("Shutting down user service application")
        await close_pool()
        app_logger.info("Database pool closed successfully")
