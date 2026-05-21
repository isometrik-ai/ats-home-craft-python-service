"""OpenAI Chat Completions — async httpx-only with a process-global cached client.

Matches ``supermemory_service`` / ``typesense_service``: no OpenAI SDK for chat calls.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("openai_chat_service")

_OPENAI_BASE_URL: Final[str] = "https://api.openai.com/v1"
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 60.0
_DEFAULT_NUM_RETRIES: Final[int] = 3
_DEFAULT_RETRY_INTERVAL_SECONDS: Final[float] = 1.0


@dataclass(slots=True)
class _ClientState:
    """Process-global cached httpx client state."""

    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _ClientState()


def is_openai_configured(settings: SharedAppSettings | None = None) -> bool:
    """Return whether OpenAI API calls are allowed (non-empty API key)."""
    return bool((settings or shared_settings).openai_api_key.strip())


def _build_auth_headers(settings: SharedAppSettings) -> dict[str, str]:
    """Return authorization headers for the OpenAI API."""
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


async def init_openai_http_client(settings: SharedAppSettings | None = None) -> None:
    """Eagerly create the OpenAI HTTP client at application startup."""
    if not is_openai_configured(settings):
        logger.info("openai_http_client_init_skipped_not_configured")
        return
    await get_openai_http_client(settings)
    logger.info("openai_http_client_initialized")


async def get_openai_http_client(
    settings: SharedAppSettings | None = None,
) -> httpx.AsyncClient:
    """Return the process-global ``httpx.AsyncClient`` (lazy create if needed)."""
    if _state.client is not None:
        return _state.client

    cfg = settings or shared_settings
    if not is_openai_configured(cfg):
        raise RuntimeError("OpenAI HTTP client is not configured (set OPENAI_API_KEY)")

    async with _state.lock:
        if _state.client is not None:
            return _state.client

        _state.client = httpx.AsyncClient(
            base_url=_OPENAI_BASE_URL,
            timeout=httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS),
            headers=_build_auth_headers(cfg),
        )
    return _state.client


async def close_openai_http_client() -> None:
    """Close and clear the cached OpenAI HTTP client."""
    async with _state.lock:
        client, _state.client = _state.client, None
    if client is not None:
        await client.aclose()
        logger.info("openai_http_client_closed")


def _content_from_message(message: dict[str, Any]) -> str:
    """Extract visible assistant text (string or multipart content)."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    return ""


def _extract_message_content(data: dict[str, Any]) -> str:
    """Read assistant message text from a chat completions JSON body."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""

    text = _content_from_message(message)
    if text:
        return text

    finish_reason = first.get("finish_reason")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    reasoning_tokens = completion_details.get("reasoning_tokens")
    logger.warning(
        "openai_chat_empty_assistant_content model_finish_reason=%s "
        "completion_tokens=%s reasoning_tokens=%s",
        finish_reason,
        usage.get("completion_tokens"),
        reasoning_tokens,
    )
    return ""


def _build_chat_completion_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_completion_tokens: int,
    response_format: dict[str, str] | None,
) -> dict[str, Any]:
    """Build JSON body for ``POST /chat/completions``."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


async def _post_chat_completion(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> str:
    """Execute one chat completion request and return assistant text."""
    response = await client.post("/chat/completions", json=payload)
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise httpx.HTTPStatusError(
            f"retryable status {response.status_code}",
            request=response.request,
            response=response,
        )
    if response.status_code >= 400:
        logger.error(
            "openai_chat_request_error status=%s model=%s body=%s",
            response.status_code,
            payload.get("model"),
            response.text[:2000],
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return ""
    return _extract_message_content(data)


async def _post_chat_completion_with_retries(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> str:
    """POST with exponential backoff on transient failures."""
    last_error: Exception | None = None

    for attempt in range(_DEFAULT_NUM_RETRIES):
        try:
            return await _post_chat_completion(client, payload)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt + 1 >= _DEFAULT_NUM_RETRIES:
                break
            delay = _DEFAULT_RETRY_INTERVAL_SECONDS * (2**attempt)
            logger.warning(
                "openai_chat_request_retry attempt=%s delay=%s",
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)

    logger.exception("openai_chat_request_failed")
    if last_error is not None:
        raise last_error
    raise RuntimeError("openai_chat_request_failed")


async def create_chat_completion(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_completion_tokens: int,
    response_format: dict[str, str] | None = None,
    settings: SharedAppSettings | None = None,
) -> str:
    """POST ``/chat/completions`` and return assistant message text."""
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI is not configured (set OPENAI_API_KEY)")

    payload = _build_chat_completion_payload(
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        response_format=response_format,
    )
    client = await get_openai_http_client(settings)
    return await _post_chat_completion_with_retries(client, payload)
