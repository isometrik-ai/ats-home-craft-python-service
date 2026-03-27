"""Isometrik external authentication dependency.

This dependency is intended for *external* APIs where the caller authenticates
using Isometrik application credentials (licenseKey/appSecret) rather than our
JWT bearer token.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Header

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("isometrik-external-auth")

@dataclass(slots=True)
class _ClientState:
    """State of the Isometrik HTTP client."""
    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _ClientState()


async def get_isometrik_http_client() -> httpx.AsyncClient:
    """Return (or lazily create) a process-global ``httpx.AsyncClient``.

    Mirrors the Typesense pattern: one pooled client reused across requests.
    """
    if _state.client is not None:
        return _state.client

    async with _state.lock:
        if _state.client is not None:
            return _state.client

        timeout_seconds = 10.0
        _state.client = httpx.AsyncClient(
            base_url=shared_settings.isometrik.api_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        logger.info(
            "isometrik_http_client_created",
            extra={"base_url": shared_settings.isometrik.api_url},
        )
        return _state.client


async def close_isometrik_http_client() -> None:
    """Close and discard the cached ``httpx.AsyncClient``."""
    async with _state.lock:
        if _state.client is None:
            return
        client, _state.client = _state.client, None

    await client.aclose()
    logger.info("isometrik_http_client_closed")


@dataclass(frozen=True)
class IsometrikExternalContext:
    """Context derived from Isometrik credential decode."""

    project_id: str
    raw: dict[str, Any]


async def isometrik_auth_without_token_middleware(
    license_key: str = Header(..., alias="licenseKey"),
    app_secret: str = Header(..., alias="appSecret"),
) -> IsometrikExternalContext:
    """Validate external caller via Isometrik credential decode.

    Requires headers:
    - licenseKey
    - appSecret

    Returns:
        IsometrikExternalContext with organization_id extracted from decode response.
    """
    if not license_key or not app_secret:
        raise BadRequestException(message_key="errors.bad_request")

    headers = {"licenseKey": license_key, "appSecret": app_secret}

    try:
        client = await get_isometrik_http_client()
        resp = await client.get("/chat/user/credentials/decode", headers=headers)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    except httpx.HTTPStatusError as e:
        # Surface 4xx as auth error; 5xx as upstream error.
        status_code = e.response.status_code
        if 400 <= status_code < 500:
            raise UnauthorizedException(message_key="errors.unauthorized") from e
        logger.exception("isometrik_decode_failed", extra={"status_code": status_code})
        raise ServiceUnavailableException(message_key="errors.service_unavailable") from e
    except Exception as e:
        logger.exception("isometrik_decode_unexpected_error")
        raise ServiceUnavailableException(message_key="errors.service_unavailable") from e

    project_id = payload.get("projectId")
    if not (isinstance(project_id, str) and project_id.strip()):
        logger.error("isometrik_decode_missing_project_id", extra={"payload": payload})
        raise BadRequestException(message_key="errors.bad_request")

    return IsometrikExternalContext(project_id=project_id.strip(), raw=payload)
