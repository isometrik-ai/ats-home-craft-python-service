"""
Lifespan event handler for the FastAPI application.

This module contains the lifespan event handler that manages startup and shutdown
events for the application.
"""

from contextlib import asynccontextmanager
from apps.user_service.app.dependencies.audit_logs.audit_logger import audit_logger
from apps.user_service.app.dependencies.logger import app_logger



@asynccontextmanager
async def lifespan(app):  # pylint: disable=unused-argument
    """Application lifespan event handler"""
    # Startup
    app_logger.info("Starting up user service application")
    # db_pool = await get_async_connection_pool()
    await audit_logger.start_processing()
    app_logger.info("Audit logger processing started successfully")

    yield

    # Shutdown (if needed)
    app_logger.info("Shutting down user service application")
