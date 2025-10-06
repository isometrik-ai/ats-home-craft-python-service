# from fastapi import FastAPI
# from pydantic import BaseModel

# app = FastAPI()

# @app.get("/")
# def read_root():
#     return {"message": "Hello, World!"}


"""Main FastAPI application module for the API service.

This module initializes the FastAPI application, sets up middleware,
and includes API routes. It also handles path configuration for imports.
"""
# Standard library imports
import os
import sys

# Third-party imports
import ddtrace.auto  # This replaces patch_all()
from ddtrace.trace import tracer
from ddtrace.contrib.asgi import TraceMiddleware
from dotenv import load_dotenv
from fastapi import status, HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# Local application imports
from apps.user_service.app.app_instance import app
from apps.user_service.app.api.routes import router as api_router
from apps.user_service.app.dependencies.exception_middleware import (
    unified_exception_handler,
    CacheRequestBodyMiddleware,
)
from apps.user_service.app.dependencies.logger import setup_logging
from libs.shared_middleware.jwt_auth import JWTAuthMiddleware
from libs.shared_db.supabase_db.admin_operations.session import get_session_by_id_admin

# Setup paths and environment
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
monorepo_root = os.path.abspath(os.path.join(base_path, "../.."))
sys.path.insert(0, base_path)
sys.path.insert(0, monorepo_root)
load_dotenv(os.path.join(monorepo_root, ".env"))

# Initialize logging at module level
app_logger = setup_logging(log_level="INFO")

# ddtrace.auto is imported above and automatically patches supported libraries


# Update the app's metadata
app.title = "House Of Apps AI"
app.description = "API For House Of Apps AI"
app.version = "1.0.0"


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str = "healthy"
    version: str = "1.0.0"


@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check():
    """
    Health check endpoint to verify the API service is running.

    Returns:
        HealthResponse: A response indicating the service is healthy
        test
    """
    result = await get_session_by_id_admin("b4575ad2-debc-4340-9427-bec3468b1cd7")
    print("result", result)
    return HealthResponse()


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*"
    ],  # Or replace * with your frontend URL like "http://localhost:3000"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(Exception, unified_exception_handler)
app.add_exception_handler(StarletteHTTPException, unified_exception_handler)
app.add_exception_handler(RequestValidationError, unified_exception_handler)
app.add_exception_handler(FastAPIHTTPException, unified_exception_handler)
# Add Datadog tracing middleware
app.add_middleware(TraceMiddleware, tracer=tracer)
# ✅ cache request body BEFORE JWT and auditing
app.add_middleware(CacheRequestBodyMiddleware)
# Add Datadog tracing middleware
app.add_middleware(TraceMiddleware, tracer=tracer)

app.add_middleware(JWTAuthMiddleware)
app.include_router(api_router)
