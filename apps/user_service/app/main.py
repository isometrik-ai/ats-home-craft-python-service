"""Main FastAPI application module for the API service.

This module initializes the FastAPI application, sets up middleware,
and includes API routes. It also handles path configuration for imports.
"""

# Standard library imports
import os
from pathlib import Path

from ddtrace.contrib.asgi import TraceMiddleware

# Third-party imports
from ddtrace.trace import tracer
from fastapi import status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.user_service.app.api.routes import router as api_router

# Local application imports
from apps.user_service.app.app_instance import app
from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.dependencies.exception_middleware import (
    CacheRequestBodyMiddleware,
)
from libs.shared_middleware.jwt_auth import JWTAuthMiddleware
from libs.shared_utils.fastapi_exception_handlers import register_exception_handlers
from libs.shared_utils.logger import setup_logging
from libs.shared_utils.translations import register_translation_path

# Register app-specific locale directory for translations
service_locale_dir = Path(os.path.dirname(__file__)) / "locales"
register_translation_path(service_locale_dir)

# Initialize logging at module level
app_logger = setup_logging()

# ddtrace.auto is imported above and automatically patches supported libraries


# Update the app's metadata
app.title = app_settings.shared_settings.app_name
app.description = app_settings.shared_settings.app_description
app.version = app_settings.shared_settings.app_version


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str = "healthy"
    version: str = "1.0.0"


@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint to verify the API service is running.

    Returns:
        HealthResponse: A response indicating the service is healthy
    """
    return HealthResponse()


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or replace * with your frontend URL like "http://localhost:3000"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Add Datadog tracing middleware (monitors all requests)
app.add_middleware(TraceMiddleware, tracer=tracer)

# Cache request body BEFORE JWT and auditing (allows multiple reads)
app.add_middleware(CacheRequestBodyMiddleware)

# JWT authentication middleware (validates tokens)
app.add_middleware(JWTAuthMiddleware)
app.include_router(api_router)
