"""Application settings module"""

import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from starlette.config import Config

from libs.shared_config.app_settings import (
    SharedAppSettings,
    shared_settings,
)

# Only load main .env if not in test environment
if os.environ.get("ENVIRONMENT") != "test":
    load_dotenv()  # Only affects local runs, safe in Docker too

config = Config()


class TwoFASettings(BaseSettings):
    """Two-Factor Authentication settings."""

    email_otp_enabled: bool = config("EMAIL_OTP_ENABLED", default=True)
    email_default_otp: str = config("EMAIL_DEFAULT_OTP", default="1111")
    phone_otp_enabled: bool = config("PHONE_OTP_ENABLED", default=False)
    phone_default_otp: str = config("PHONE_DEFAULT_OTP", default="1111")
    max_attempt_verification: int = config("MAX_ATTEMPT_VERIFICATION", default=5)
    verification_code_expiry_minutes: int = config("VERIFICATION_CODE_EXPIRY_MINUTES", default=10)
    verification_attempt_window_hours: int = config("VERIFICATION_ATTEMPT_WINDOW_HOURS", default=24)


class EnrichmentServiceSettings(BaseSettings):
    """Client enrichment service settings."""

    enabled: bool = config("ENRICHMENT_SERVICE_ENABLED", default=True)
    base_url: str = config(
        "ENRICHMENT_SERVICE_BASE_URL",
        default="http://91.99.230.218:8071",
    )
    webhook_url: str = config(
        "ENRICHMENT_SERVICE_WEBHOOK_URL",
        default="https://api-v2.houseofapps.ai/v1/webhooks/enrichment",
    )
    timeout_seconds: float = config("ENRICHMENT_SERVICE_TIMEOUT", default=30.0)
    logo_dev_key: str | None = config("LOGO_DEV_KEY", default=None)


class ExternalServiceSettings(BaseSettings):
    """External service settings."""

    social_service_url: str | None = config("SOCIAL_SERVICE_URL", default=None)


class KafkaSettings(BaseSettings):
    """Kafka producer settings for event publishing."""

    enabled: bool = config("KAFKA_ENABLED", default=False)
    bootstrap_servers: str = config("KAFKA_BOOTSTRAP_SERVERS", default="")
    producer_name: str = config("KAFKA_PRODUCER_NAME", default="user_service")
    security_protocol: str = config("KAFKA_SECURITY_PROTOCOL", default="PLAINTEXT")
    sasl_mechanism: str | None = config("KAFKA_SASL_MECHANISM", default=None)
    sasl_username: str | None = config("KAFKA_SASL_USERNAME", default=None)
    sasl_password: str | None = config("KAFKA_SASL_PASSWORD", default=None)
    request_timeout_ms: int = config("KAFKA_REQUEST_TIMEOUT_MS", default=30000)
    max_batch_size: int = config("KAFKA_MAX_BATCH_SIZE", default=16384)
    linger_ms: int = config("KAFKA_LINGER_MS", default=5)
    compression_type: str | None = config("KAFKA_COMPRESSION_TYPE", default="gzip")
    org_enrichment_consumer_group_id: str = config(
        "KAFKA_ORG_ENRICHMENT_CONSUMER_GROUP_ID",
        default="org-enrichment-worker",
    )


class ApplicationSettings(BaseSettings):
    """Application settings."""

    shared_settings: SharedAppSettings = shared_settings
    two_fa_settings: TwoFASettings = TwoFASettings()
    enrichment_service: EnrichmentServiceSettings = EnrichmentServiceSettings()
    invite_expiry_days: int = config("INVITE_EXPIRY_DAYS", default=7)
    datadog_tracing_enabled: bool = config("DATADOG_TRACING_ENABLED", default=False)
    external_service: ExternalServiceSettings = ExternalServiceSettings()
    kafka: KafkaSettings = KafkaSettings()


app_settings = ApplicationSettings()
