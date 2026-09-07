"""Work order service application settings."""

import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from starlette.config import Config

from libs.shared_config.app_settings import SharedAppSettings, shared_settings

if os.environ.get("ENVIRONMENT") != "test":
    load_dotenv()

config = Config()


class SchedulerSettings(BaseSettings):
    """Background scheduler settings."""

    enabled: bool = config("WOM_SCHEDULER_ENABLED", default=True)
    interval_minutes: int = config("WOM_SCHEDULER_INTERVAL_MINUTES", default=60)
    internal_token: str | None = config("WOM_INTERNAL_SERVICE_TOKEN", default=None)


class WorkOrderServiceSettings(BaseSettings):
    """Top-level settings for work_order_service."""

    shared_settings: SharedAppSettings = shared_settings
    scheduler: SchedulerSettings = SchedulerSettings()
    datadog_tracing_enabled: bool = config("DATADOG_TRACING_ENABLED", default=False)
    user_service_base_url: str = config("USER_SERVICE_BASE_URL", default="http://localhost:5000")
    cors_origins: str = config(
        "WOM_CORS_ORIGINS", default="http://localhost:5173,http://localhost:3000"
    )


app_settings = WorkOrderServiceSettings()
