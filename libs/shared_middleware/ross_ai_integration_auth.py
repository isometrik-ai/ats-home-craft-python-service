"""Ross AI integration API key authentication for external partner endpoints."""

from __future__ import annotations

import secrets

from fastapi import Header

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.http_exceptions import (
    ServiceUnavailableException,
    UnauthorizedException,
)


async def verify_ross_ai_integration_api_key(
    rossai_api_key: str = Header(..., alias="ROSSAI_API_KEY"),
) -> None:
    """Validate the Ross AI integration API key from ``ROSSAI_API_KEY`` header."""
    expected = shared_settings.rossai_api_key.strip()
    if not expected:
        raise ServiceUnavailableException(message_key="errors.service_unavailable")

    provided = (rossai_api_key or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise UnauthorizedException(message_key="errors.unauthorized")
