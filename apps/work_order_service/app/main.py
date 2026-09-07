"""Work order service FastAPI entrypoint."""

import os
from pathlib import Path

from fastapi import Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.work_order_service.app.api.routes import router as api_router
from apps.work_order_service.app.app_instance import app
from apps.work_order_service.app.config.app_settings import app_settings
from apps.work_order_service.app.dependencies.db import db_conn
from libs.shared_middleware.jwt_auth import JWTAuthMiddleware
from libs.shared_utils.fastapi_exception_handlers import register_exception_handlers
from libs.shared_utils.logger import setup_logging
from libs.shared_utils.translations import register_translation_path

service_locale_dir = Path(os.path.dirname(__file__)) / "locales"
register_translation_path(service_locale_dir)
setup_logging()

app.title = "Work Order Management Service"
app.description = "Facility management — assets, contracts, work orders, invoices"
app.version = app_settings.shared_settings.app_version


class HealthResponse(BaseModel):
    """Response payload for health."""

    status: str = "healthy"
    service: str = "work_order_service"
    database: str = "unknown"


@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check(conn=Depends(db_conn)):
    """Health check with database connectivity."""
    db_status = "healthy"
    try:
        await conn.fetchval("SELECT 1")
    except Exception:  # — health endpoint
        db_status = "unhealthy"
    overall = "healthy" if db_status == "healthy" else "degraded"
    return HealthResponse(status=overall, database=db_status)


_origins = [o.strip() for o in app_settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

if app_settings.datadog_tracing_enabled:
    from ddtrace.contrib.asgi import TraceMiddleware
    from ddtrace.trace import tracer

    app.add_middleware(TraceMiddleware, tracer=tracer)

app.add_middleware(JWTAuthMiddleware)
app.include_router(api_router)
