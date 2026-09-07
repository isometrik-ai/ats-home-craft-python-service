"""FastAPI application instance for work_order_service."""

from apps.work_order_service.app.lifespan import lifespan
from libs.shared_utils.fastapi_app import create_fastapi_app
from libs.shared_utils.telemetry_config import telemetry_config

app, limiter = create_fastapi_app(lifespan=lifespan)
telemetry_config.setup_telemetry(app=app)
