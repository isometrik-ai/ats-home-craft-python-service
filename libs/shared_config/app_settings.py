"""Application settings module"""

import os
from enum import Enum

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from starlette.config import Config

# Only load main .env if not in test environment
if os.environ.get("ENVIRONMENT") != "test":
    load_dotenv()  # Only affects local runs, safe in Docker too

config = Config()


class LogLevelOption(Enum):
    """Log level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EnvironmentOption(Enum):
    """Environment options."""

    DEVELOPMENT = "development"
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class DatabaseSettings(BaseSettings):
    """Database settings."""

    host: str = config("DB_HOST", default="localhost")
    port: int = config("DB_PORT", default=5432)
    database: str = config("DB_DATABASE")
    db_user: str = config("DB_USER")
    password: str = config("DB_PASSWORD")
    url: str | None = config("DATABASE_URL", default=None)
    ssl_mode: str = config("DB_SSL_MODE", default="disable")
    ssl_root_cert: str | None = config("DB_SSL_ROOT_CERT", default=None)
    min_pool: int = config("DB_MIN_POOL", default=1)
    max_pool: int = config("DB_MAX_POOL", default=10)
    command_timeout: float = config("DB_COMMAND_TIMEOUT", default=30.0)
    max_idle_time: float = config("DB_MAX_IDLE_TIME", default=300.0)
    statement_timeout_ms: int | None = config("DB_STATEMENT_TIMEOUT_MS", default=None)
    acquire_max_retry: int = config("DB_ACQUIRE_MAX_RETRY", default=3)
    acquire_base_delay: float = config("DB_ACQUIRE_BASE_DELAY", default=1.0)
    acquire_timeout: float = config("DB_ACQUIRE_TIMEOUT", default=10.0)


class SupabaseSettings(BaseSettings):
    """Supabase settings."""

    url: str = config("SUPABASE_URL")
    anon_key: str = config("SUPABASE_ANON_KEY")
    service_key: str = config("SUPABASE_SERVICE_KEY")
    jwt_secret: str = config("SUPABASE_JWT_SECRET")


class IsometrikSettings(BaseSettings):
    """Isometrik settings."""

    is_enabled: bool = config("ISOMETRIK_ENABLED", default=True)
    admin_api_url: str = config("ISOMETRIK_ADMIN_API_URL")
    api_url: str = config("ISOMETRIK_API_URL")
    client_name: str = config("ISOMETRIK_CLIENT_NAME")
    region_id: str = config("ISOMETRIK_REGION_ID")
    auth_token: str = config("ISOMETRIK_AUTH_TOKEN")


class CloudflareR2Settings(BaseSettings):
    """R2 settings."""

    account_id: str = config("R2_ACCOUNT_ID")
    access_key: str = config("R2_ACCESS_KEY")
    secret_key: str = config("R2_SECRET_KEY")
    bucket_name: str = config("R2_BUCKET_NAME")
    media_url: str = config("R2_MEDIA_URL")


class SharedAppSettings(BaseSettings):
    """Application settings."""

    database: DatabaseSettings = DatabaseSettings()
    supabase: SupabaseSettings = SupabaseSettings()
    isometrik: IsometrikSettings = IsometrikSettings()
    cloudflare_r2: CloudflareR2Settings = CloudflareR2Settings()
    environment: EnvironmentOption = config("ENVIRONMENT", default=EnvironmentOption.LOCAL)
    log_level: LogLevelOption = config("LOG_LEVEL", default=LogLevelOption.INFO.value)
    website_url: str = config("WEBSITE_URL")
    app_name: str = config("APP_NAME", default="House Of Apps AI")
    app_version: str = config("APP_VERSION", default="1.0.0")
    app_description: str = config("APP_DESCRIPTION", default="API for House Of Apps AI")
    app_author: str = config("APP_AUTHOR", default="Rahul Sharma")
    app_author_email: str = config("APP_AUTHOR_EMAIL", default="rahul@3embed.com")
    app_author_url: str = config("APP_AUTHOR_URL", default="https://houseofapps.ai")
    app_license: str = config("APP_LICENSE", default="MIT")
    app_license_url: str = config("APP_LICENSE_URL", default="https://opensource.org/licenses/MIT")
    company_name: str = config("COMPANY_NAME", default="House of App AI")
    company_address: str = config("COMPANY_ADDRESS", default="123 Main Street, City, State 12345")
    company_support_email: str = config("COMPANY_SUPPORT_EMAIL", default="support@houseofapps.ai")
    company_website: str = config("COMPANY_WEBSITE", default="https://houseofapps.ai")
    company_privacy_policy_url: str = config(
        "COMPANY_PRIVACY_POLICY_URL", default="https://houseofapps.ai/privacy"
    )
    company_terms_url: str = config("COMPANY_TERMS_URL", default="https://houseofapps.ai/terms")


shared_settings = SharedAppSettings()
