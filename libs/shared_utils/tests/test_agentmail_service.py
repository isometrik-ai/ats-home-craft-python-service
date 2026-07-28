"""Unit tests for AgentMail HTTP helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from libs.shared_config.app_settings import AgentMailSettings, SharedAppSettings
from libs.shared_utils.agentmail_service import (
    AgentMailService,
    attachment_text_for_supermemory,
    is_agentmail_configured,
    normalize_attachment_meta,
)


def _settings(
    *, api_key: str = "test-key", base_url: str = "https://api.agentmail.to"
) -> SharedAppSettings:
    """Build shared settings with AgentMail configuration."""
    settings = SharedAppSettings()
    settings.agentmail = AgentMailSettings(
        api_key=api_key,
        base_url=base_url,
        request_timeout_seconds=5.0,
    )
    return settings


def test_is_agentmail_configured_false_when_key_blank() -> None:
    """Configuration check rejects blank API keys."""
    assert is_agentmail_configured(_settings(api_key="  ")) is False


def test_is_agentmail_configured_true_when_key_present() -> None:
    """Configuration check passes when API key is set."""
    assert is_agentmail_configured(_settings(api_key="secret")) is True


def test_agentmail_service_is_configured() -> None:
    """Service instance reflects global AgentMail configuration."""
    service = AgentMailService(settings=_settings(api_key="secret"))
    with patch("libs.shared_utils.agentmail_service.is_agentmail_configured", return_value=True):
        assert service.is_configured is True


@pytest.mark.asyncio
async def test_fetch_message_attachment_not_configured() -> None:
    """Unconfigured service returns None without HTTP call."""
    service = AgentMailService(settings=_settings(api_key=""))

    result = await service.fetch_message_attachment(
        inbox_id="inbox-1",
        message_id="msg-1",
        attachment_id="att-1",
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_message_attachment_success() -> None:
    """Configured service downloads attachment bytes."""
    service = AgentMailService(settings=_settings(api_key="secret"))
    response = MagicMock()
    response.content = b"hello attachment"
    response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("libs.shared_utils.agentmail_service.is_agentmail_configured", return_value=True),
        patch("libs.shared_utils.agentmail_service.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await service.fetch_message_attachment(
            inbox_id="inbox/1",
            message_id="msg 1",
            attachment_id="att#1",
        )

    assert result == b"hello attachment"
    called_url = mock_client.get.await_args.args[0]
    assert "inboxes/inbox%2F1/messages/msg%201/attachments/att%231" in called_url
    assert mock_client.get.await_args.kwargs["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_fetch_message_attachment_http_error() -> None:
    """HTTP failures are logged and return None."""
    service = AgentMailService(settings=_settings(api_key="secret"))

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("libs.shared_utils.agentmail_service.httpx.AsyncClient", return_value=mock_client):
        result = await service.fetch_message_attachment(
            inbox_id="inbox-1",
            message_id="msg-1",
            attachment_id="att-1",
        )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_message_attachment_too_large() -> None:
    """Oversized attachments are rejected."""
    service = AgentMailService(settings=_settings(api_key="secret"))
    response = MagicMock()
    response.content = b"x" * (5 * 1024 * 1024 + 1)
    response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("libs.shared_utils.agentmail_service.httpx.AsyncClient", return_value=mock_client):
        result = await service.fetch_message_attachment(
            inbox_id="inbox-1",
            message_id="msg-1",
            attachment_id="att-1",
        )

    assert result is None


def test_attachment_text_for_supermemory_without_bytes() -> None:
    """Metadata-only attachments omit content body."""
    text = attachment_text_for_supermemory(
        filename="notes.txt",
        content_type="text/plain",
        raw_bytes=None,
        size=12,
    )

    assert "Filename: notes.txt" in text
    assert "Size (bytes): 12" in text
    assert "Content:" not in text


def test_attachment_text_for_supermemory_text_content() -> None:
    """Text attachments include decoded content."""
    text = attachment_text_for_supermemory(
        filename="notes.txt",
        content_type="text/plain",
        raw_bytes=b"Hello tenant",
    )

    assert "Content:\nHello tenant" in text


def test_attachment_text_for_supermemory_truncates_long_text() -> None:
    """Very long text attachments are truncated."""
    text = attachment_text_for_supermemory(
        filename="notes.txt",
        content_type="application/json",
        raw_bytes=("a" * 60_000).encode(),
    )

    assert text.endswith("…")
    assert len(text) < 60_000


def test_attachment_text_for_supermemory_binary_content() -> None:
    """Binary attachments report byte size without extraction."""
    text = attachment_text_for_supermemory(
        filename="scan.pdf",
        content_type="application/pdf",
        raw_bytes=b"%PDF-1.4",
    )

    assert "binary file (8 bytes)" in text


def test_normalize_attachment_meta_accepts_dict_variants() -> None:
    """Webhook attachment objects normalize id and metadata fields."""
    meta = normalize_attachment_meta(
        {
            "attachmentId": "att-1",
            "name": "lease.pdf",
            "contentType": "application/pdf",
            "size": 1024,
            "inline": True,
        }
    )

    assert meta == {
        "attachment_id": "att-1",
        "filename": "lease.pdf",
        "content_type": "application/pdf",
        "size": 1024,
        "inline": True,
    }


def test_normalize_attachment_meta_rejects_invalid_items() -> None:
    """Invalid attachment payloads return None."""
    assert normalize_attachment_meta("not-a-dict") is None
    assert normalize_attachment_meta({"filename": "missing-id.pdf"}) is None


def test_agentmail_service_from_settings() -> None:
    """Factory builds service from explicit settings."""
    settings = _settings(api_key="factory-key")
    service = AgentMailService.from_settings(settings)
    assert service._settings.api_key == "factory-key"
