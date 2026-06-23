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
    whatsapp_workflow_id: str = config(
        "ISOMETRIK_WHATSAPP_WORKFLOW_ID",
        default="6a056642da7550b8dd2632a1",
    )
    strands_auth_token: str = config("ISOMETRIK_STRANDS_AUTH_TOKEN", default="")
    domain_discovery_agent_id: str = config(
        "ISOMETRIK_DOMAIN_DISCOVERY_AGENT_ID",
        default="6a1e96782a47334a19c74188",
    )
    business_overview_agent_id: str = config(
        "ISOMETRIK_BUSINESS_OVERVIEW_AGENT_ID",
        default="6a1e806e8dc032d543776318",
    )
    email_template_agent_id: str = config(
        "ISOMETRIK_EMAIL_TEMPLATE_AGENT_ID",
        default="6a2831dddb53333f06d01e68",
    )
    org_business_overview_on_create_enabled: bool = config(
        "ISOMETRIK_ORG_BUSINESS_OVERVIEW_ON_CREATE_ENABLED",
        default=True,
    )
    strands_request_timeout_seconds: float = config(
        "ISOMETRIK_STRANDS_REQUEST_TIMEOUT_SECONDS",
        default=300.0,
    )
    org_enrichment_max_concurrent: int = config(
        "ISOMETRIK_ORG_ENRICHMENT_MAX_CONCURRENT",
        default=5,
    )
    org_overview_openai_timeout_seconds: float = config(
        "ORG_OVERVIEW_OPENAI_TIMEOUT_SECONDS",
        default=360.0,
    )
    private_key: str = config("ISOMETRIK_PRIVATE_KEY", default="")
    token_exp_minutes: int = config("ISOMETRIK_TOKEN_EXP_MIN", default=540)


class CloudflareR2Settings(BaseSettings):
    """R2 settings."""

    account_id: str = config("R2_ACCOUNT_ID")
    access_key: str = config("R2_ACCESS_KEY")
    secret_key: str = config("R2_SECRET_KEY")
    bucket_name: str = config("R2_BUCKET_NAME")
    media_url: str = config("R2_MEDIA_URL")


class AgentMailSettings(BaseSettings):
    """AgentMail API configuration for inbound attachment downloads."""

    api_key: str = config("AGENTMAIL_API_KEY", default="")
    base_url: str = config("AGENTMAIL_BASE_URL", default="https://api.agentmail.to")
    request_timeout_seconds: float = config("AGENTMAIL_REQUEST_TIMEOUT_SECONDS", default=30.0)


class SupermemorySettings(BaseSettings):
    """Supermemory API configuration for CRM entity memory sync."""

    api_key: str = config("SUPERMEMORY_API_KEY", default="")
    enabled: bool = config("SUPERMEMORY_ENABLED", default=False)
    base_url: str = config("SUPERMEMORY_BASE_URL", default="https://api.supermemory.ai")
    consumer_group_id: str = config(
        "SUPERMEMORY_CONSUMER_GROUP_ID",
        default="crm-supermemory-sync",
    )
    request_timeout_seconds: float = config(
        "SUPERMEMORY_REQUEST_TIMEOUT_SECONDS",
        default=30.0,
    )
    num_retries: int = config("SUPERMEMORY_NUM_RETRIES", default=3)
    retry_interval_seconds: float = config(
        "SUPERMEMORY_RETRY_INTERVAL_SECONDS",
        default=1.0,
    )


class TelemetrySettings(BaseSettings):
    """OpenTelemetry / SigNoz telemetry settings."""

    enabled: bool = config("ENABLE_TELEMETRY", default=True)
    service_name: str = config("SIGNOZ_SERVICE_NAME", default="legalai-user-service")
    service_version: str = config("SERVICE_VERSION", default="1.0.0")
    environment: str = config("SIGNOZ_ENVIRONMENT", default="development")
    signoz_cloud_url: str = config("SIGNOZ_CLOUD_URL", default="")
    signoz_cloud_token: str = config("SIGNOZ_CLOUD_TOKEN", default="")
    signoz_endpoint: str = config("SIGNOZ_ENDPOINT", default="")


class RedisSettings(BaseSettings):
    """Redis connection and session-context cache settings."""

    url: str = config("REDIS_URL", default="redis://localhost:6379/0")
    enabled: bool = config("REDIS_ENABLED", default=True)
    max_connections: int = config("REDIS_MAX_CONNECTIONS", default=200)
    session_ctx_cache_enabled: bool = config("SESSION_CTX_CACHE_ENABLED", default=True)
    session_ctx_cache_ttl_seconds: int = config("SESSION_CTX_CACHE_TTL_SECONDS", default=300)
    session_revoked_cache_ttl_seconds: int = config(
        "SESSION_REVOKED_CACHE_TTL_SECONDS", default=3600
    )
    user_deleted_cache_ttl_seconds: int = config("USER_DELETED_CACHE_TTL_SECONDS", default=86400)


class TypesenseSettings(BaseSettings):
    """Typesense cluster and API key configuration."""

    host: str = config("TYPESENSE_HOST")
    port: int = config("TYPESENSE_PORT", default=8108)
    protocol: str = config("TYPESENSE_PROTOCOL", default="https")
    admin_api_key: str = config("TYPESENSE_ADMIN_API_KEY")
    search_only_api_key: str = config("TYPESENSE_SEARCH_ONLY_KEY")
    vector_distance_threshold: float = config(
        "TYPESENSE_VECTOR_DISTANCE_THRESHOLD",
        default=0.30,
    )
    connection_timeout_seconds: float = config(
        "TYPESENSE_CONNECTION_TIMEOUT_SECONDS",
        default=5.0,
    )
    num_retries: int = config("TYPESENSE_NUM_RETRIES", default=3)
    retry_interval_seconds: float = config(
        "TYPESENSE_RETRY_INTERVAL_SECONDS",
        default=0.1,
    )
    clients_collection_name: str = config("TYPESENSE_CLIENTS_COLLECTION_NAME")
    contacts_collection_name: str = config(
        "TYPESENSE_CONTACTS_COLLECTION_NAME",
        default="contacts",
    )
    companies_collection_name: str = config(
        "TYPESENSE_COMPANIES_COLLECTION_NAME",
        default="companies",
    )


class SharedAppSettings(BaseSettings):
    """Application settings."""

    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    supabase: SupabaseSettings = SupabaseSettings()
    isometrik: IsometrikSettings = IsometrikSettings()
    cloudflare_r2: CloudflareR2Settings = CloudflareR2Settings()
    typesense: TypesenseSettings = TypesenseSettings()
    agentmail: AgentMailSettings = AgentMailSettings()
    supermemory: SupermemorySettings = SupermemorySettings()
    telemetry: TelemetrySettings = TelemetrySettings()
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
    openai_api_key: str = config("OPENAI_API_KEY")
    org_memory_llm_model: str = config("ORG_MEMORY_LLM_MODEL", default="gpt-4.1-mini")
    rossai_api_key: str = config("ROSSAI_API_KEY", default="")


shared_settings = SharedAppSettings()
