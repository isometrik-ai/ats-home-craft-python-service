"""Unit tests for Ross AI integration API key auth."""

import pytest

from libs.shared_middleware.ross_ai_integration_auth import (
    verify_ross_ai_integration_api_key,
)
from libs.shared_utils.http_exceptions import UnauthorizedException


@pytest.mark.asyncio
async def test_ross_api_key_rejects_invalid(monkeypatch) -> None:
    """Invalid API keys are rejected."""
    monkeypatch.setattr(
        "libs.shared_middleware.ross_ai_integration_auth.shared_settings.rossai_api_key",
        "expected-key",
    )

    with pytest.raises(UnauthorizedException):
        await verify_ross_ai_integration_api_key(rossai_api_key="wrong-key")


@pytest.mark.asyncio
async def test_ross_api_key_accepts_valid(monkeypatch) -> None:
    """Matching API keys pass validation."""
    monkeypatch.setattr(
        "libs.shared_middleware.ross_ai_integration_auth.shared_settings.rossai_api_key",
        "expected-key",
    )

    await verify_ross_ai_integration_api_key(rossai_api_key="expected-key")
