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
if not os.environ.get("ENVIRONMENT") == "test":
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


class ApplicationSettings(BaseSettings):
    """Application settings."""

    shared_settings: SharedAppSettings = shared_settings
    two_fa_settings: TwoFASettings = TwoFASettings()
    invite_expiry_days: int = config("INVITE_EXPIRY_DAYS", default=7)


app_settings = ApplicationSettings()
