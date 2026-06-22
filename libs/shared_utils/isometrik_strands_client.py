"""Isometrik Strands agent chat HTTP client (admin API).

Transport only: pooled ``httpx.AsyncClient`` and ``call_strands_agent``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("isometrik_strands_client")

_STRANDS_PATH = "/v1/agent/chat/strands/"
_MAX_AGENT_MESSAGE_LEN = 4000
_RESPONSE_LOG_MAX_LEN = 2000
_CONNECT_TIMEOUT_SECONDS = 30.0


def _truncate_for_log(value: str, *, max_len: int = _RESPONSE_LOG_MAX_LEN) -> str:
    """Return value truncated to max_len for safe logging."""
    text = value.strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...(truncated)"


def _preview_strands_response_body(body: dict[str, Any]) -> str:
    """Prefer agent ``text`` field; otherwise log a compact JSON snapshot."""
    raw_text = body.get("text")
    if isinstance(raw_text, str) and raw_text.strip():
        return _truncate_for_log(raw_text)
    try:
        return _truncate_for_log(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return _truncate_for_log(str(body))


@dataclass(slots=True)
class _ClientState:
    """Process-global cached httpx client for Strands admin API."""

    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _ClientState()


def _strands_timeout() -> httpx.Timeout:
    """Build httpx timeout from shared Isometrik strands settings."""
    read_seconds = max(60.0, float(shared_settings.isometrik.strands_request_timeout_seconds))
    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT_SECONDS,
        read=read_seconds,
        write=_CONNECT_TIMEOUT_SECONDS,
        pool=_CONNECT_TIMEOUT_SECONDS,
    )


async def init_strands_http_client() -> None:
    """Eagerly create the Strands HTTP client at application startup."""
    if not shared_settings.isometrik.strands_auth_token.strip():
        logger.info("strands_http_client_init_skipped_not_configured")
        return
    await get_strands_http_client()
    logger.info("strands_http_client_initialized")


async def get_strands_http_client() -> httpx.AsyncClient:
    """Return the process-global Strands ``httpx.AsyncClient``."""
    if _state.client is not None:
        return _state.client

    async with _state.lock:
        if _state.client is not None:
            return _state.client

        base_url = shared_settings.isometrik.admin_api_url.rstrip("/")
        _state.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=_strands_timeout(),
        )
        logger.info("strands_http_client_created", extra={"base_url": base_url})
        return _state.client


async def close_strands_http_client() -> None:
    """Close and clear the cached Strands HTTP client."""
    async with _state.lock:
        client, _state.client = _state.client, None
    if client is not None:
        await client.aclose()
        logger.info("strands_http_client_closed")


def _new_session_id() -> str:
    """Return a millisecond timestamp string for strands session_id."""
    return str(int(time.time() * 1000))


def _sanitize_agent_message(message: str) -> str:
    """Bound agent input size (defense in depth for external API calls)."""
    return message.strip()[:_MAX_AGENT_MESSAGE_LEN]


def _strands_headers() -> dict[str, str]:
    """Authorization and content headers for strands admin API."""
    return {
        "Authorization": shared_settings.isometrik.strands_auth_token.strip(),
        "Content-Type": "application/json",
        "Accept": "text/plain",
    }


async def call_strands_agent(
    *,
    agent_id: str,
    message: str,
    stream: bool = False,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST to Isometrik strands chat and return the parsed JSON body."""
    client = await get_strands_http_client()
    payload = {
        "session_id": _new_session_id(),
        "agent_id": agent_id,
        "message": _sanitize_agent_message(message),
        "files": [],
        "schema": schema or {},
        "stream": stream,
    }
    response = await client.post(_STRANDS_PATH, headers=_strands_headers(), json=payload)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("strands response must be a JSON object")
    logger.info(
        "strands_agent_response | agent_id=%s status_code=%s message_len=%s body_keys=%s "
        "response_preview=%s",
        agent_id,
        response.status_code,
        len(payload["message"]),
        list(body.keys()),
        _preview_strands_response_body(body),
    )
    return body
