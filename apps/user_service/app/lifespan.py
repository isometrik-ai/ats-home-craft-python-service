"""Lifespan event handler for the FastAPI application.

This module contains the lifespan event handler that manages startup and shutdown
events for the application.
"""

from asyncio import Event
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.user_service.app.dependencies.audit_logs.audit_logger import audit_logger
from apps.user_service.app.dependencies.logger import app_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan event handler."""
    initialization_complete = Event()
    app.state.initialization_complete = initialization_complete
    # Startup
    app_logger.info("Starting up user service application")

    audit_logger.start_processing()
    app_logger.info("Audit logger processing started successfully")

    yield

    # Shutdown (if needed)
    app_logger.info("Shutting down user service application")
