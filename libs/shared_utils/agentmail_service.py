"""Minimal AgentMail HTTP client for fetching message attachments."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("agentmail_service")

_DEFAULT_BASE_URL = "https://api.agentmail.to"
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def is_agentmail_configured(settings: SharedAppSettings | None = None) -> bool:
    """Return whether AgentMail API calls are allowed."""
    cfg = (settings or shared_settings).agentmail
    return bool(cfg.api_key.strip())


class AgentMailService:
    """Fetch attachment bytes from AgentMail (webhooks omit file content)."""

    def __init__(self, settings: SharedAppSettings | None = None) -> None:
        self._settings = (settings or shared_settings).agentmail

    @classmethod
    def from_settings(cls, settings: SharedAppSettings | None = None) -> AgentMailService:
        """Build a service instance from shared or explicit settings."""
        return cls(settings=settings)

    @property
    def is_configured(self) -> bool:
        """True when AgentMail API calls are allowed for this process."""
        return is_agentmail_configured()

    async def fetch_message_attachment(
        self,
        *,
        inbox_id: str,
        message_id: str,
        attachment_id: str,
    ) -> bytes | None:
        """Download raw attachment bytes for a message attachment."""
        if not self.is_configured:
            return None
        inbox = quote(str(inbox_id).strip(), safe="")
        message = quote(str(message_id).strip(), safe="")
        attachment = quote(str(attachment_id).strip(), safe="")
        base = self._settings.base_url.rstrip("/")
        url = f"{base}/v0/inboxes/{inbox}/messages/{message}/attachments/{attachment}"
        headers = {"Authorization": f"Bearer {self._settings.api_key}"}
        timeout = httpx.Timeout(self._settings.request_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.content
        except httpx.HTTPError:
            logger.exception(
                "agentmail_fetch_attachment_failed inbox_id=%s message_id=%s attachment_id=%s",
                inbox_id,
                message_id,
                attachment_id,
            )
            return None
        if len(data) > _MAX_ATTACHMENT_BYTES:
            logger.warning(
                "agentmail_attachment_too_large attachment_id=%s size=%s",
                attachment_id,
                len(data),
            )
            return None
        return data


def attachment_text_for_supermemory(
    *,
    filename: str,
    content_type: str,
    raw_bytes: bytes | None,
    size: int | None = None,
) -> str:
    """Build searchable text for an attachment document."""
    lines = [
        f"Filename: {filename or 'attachment'}",
        f"Content-Type: {content_type or 'application/octet-stream'}",
    ]
    if size is not None:
        lines.append(f"Size (bytes): {size}")
    if raw_bytes is None:
        return "\n".join(lines)

    ctype = (content_type or "").lower()
    if ctype.startswith("text/") or ctype in {"application/json", "application/xml"}:
        try:
            text = raw_bytes.decode("utf-8", errors="replace").strip()
            if len(text) > 50_000:
                text = f"{text[:50_000]}…"
            lines.append(f"Content:\n{text}")
        except (UnicodeDecodeError, ValueError):
            lines.append("Content: binary file (text extraction failed).")
    else:
        lines.append(
            f"Content: binary file ({len(raw_bytes)} bytes). "
            "Text extraction is only applied to text/* and JSON/XML types."
        )
    return "\n".join(lines)


def normalize_attachment_meta(item: Any) -> dict[str, Any] | None:
    """Normalize one attachment object from a webhook message."""
    if not isinstance(item, dict):
        return None
    attachment_id = str(
        item.get("attachment_id") or item.get("id") or item.get("attachmentId") or ""
    ).strip()
    if not attachment_id:
        return None
    filename = str(item.get("filename") or item.get("name") or "attachment").strip()
    content_type = str(
        item.get("content_type") or item.get("contentType") or "application/octet-stream"
    ).strip()
    size_raw = item.get("size")
    size = int(size_raw) if isinstance(size_raw, (int, float)) else None
    inline = bool(item.get("inline"))
    return {
        "attachment_id": attachment_id,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "inline": inline,
    }
