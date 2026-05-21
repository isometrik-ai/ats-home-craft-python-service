"""Supermemory service — async, httpx-only.

Design decisions
----------------
- No Supermemory SDK: keeps dependencies minimal and matches ``typesense_service``.
- Single process-global ``httpx.AsyncClient`` with a double-checked async lock.
- Call ``init_supermemory_http_client()`` during application startup when enabled.
- Retries only on network errors and transient HTTP status codes (429, 5xx).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote

import httpx

from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("supermemory_service")

_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_ENTITY_CONTEXT_MAX_LEN: Final[int] = 1500


def container_tag_for_organization(organization_id: str) -> str:
    """Supermemory container tag that scopes documents to one CRM tenant.

    Must match the value used when upserting via ``POST /v3/documents``.
    """
    return f"org_{organization_id}"


@dataclass(slots=True)
class SupermemorySearchHit:
    """One row from Supermemory hybrid search (memory fact or document chunk)."""

    id: str
    text: str
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class _ClientState:
    """Process-global cached httpx client state."""

    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _ClientState()


def is_supermemory_configured(settings: SharedAppSettings | None = None) -> bool:
    """Return whether Supermemory API calls are allowed for this process."""
    cfg = (settings or shared_settings).supermemory
    return bool(cfg.enabled and cfg.api_key.strip())


def _build_auth_headers(settings: SharedAppSettings) -> dict[str, str]:
    """Return authorization headers for the Supermemory API."""
    return {
        "Authorization": f"Bearer {settings.supermemory.api_key}",
        "Content-Type": "application/json",
    }


async def init_supermemory_http_client(
    settings: SharedAppSettings | None = None,
) -> None:
    """Eagerly create the Supermemory HTTP client at application startup.

    No-op when ``SUPERMEMORY_ENABLED`` is false or the API key is unset.
    """
    if not is_supermemory_configured(settings):
        logger.info("supermemory_http_client_init_skipped_not_configured")
        return
    await get_supermemory_http_client(settings)
    logger.info("supermemory_http_client_initialized")


async def get_supermemory_http_client(
    settings: SharedAppSettings | None = None,
) -> httpx.AsyncClient:
    """Return the process-global ``httpx.AsyncClient`` (lazy create if needed).

    Prefer ``init_supermemory_http_client()`` during startup so the first request
    does not pay connection setup cost.

    Raises:
        RuntimeError: If Supermemory is not configured but a client is requested.
    """
    if _state.client is not None:
        return _state.client

    cfg = settings or shared_settings
    if not is_supermemory_configured(cfg):
        raise RuntimeError(
            "Supermemory HTTP client is not configured "
            "(set SUPERMEMORY_ENABLED=true and SUPERMEMORY_API_KEY)"
        )

    async with _state.lock:
        if _state.client is not None:
            return _state.client

        supermemory_settings = cfg.supermemory
        _state.client = httpx.AsyncClient(
            base_url=supermemory_settings.base_url.rstrip("/"),
            timeout=httpx.Timeout(supermemory_settings.request_timeout_seconds),
            headers=_build_auth_headers(cfg),
        )
    return _state.client


async def close_supermemory_http_client() -> None:
    """Close and clear the cached Supermemory HTTP client."""
    async with _state.lock:
        client, _state.client = _state.client, None
    if client is not None:
        await client.aclose()
        logger.info("supermemory_http_client_closed")


class SupermemoryService:
    """Thin wrapper around Supermemory document ingest APIs."""

    __slots__ = ("_root_settings", "_settings")

    def __init__(self, settings: SharedAppSettings | None = None) -> None:
        """Initialize with optional settings override (defaults to ``shared_settings``)."""
        self._root_settings = settings or shared_settings
        self._settings = self._root_settings.supermemory

    @classmethod
    def from_settings(cls, settings: SharedAppSettings | None = None) -> SupermemoryService:
        """Build a service instance from shared or explicit settings."""
        return cls(settings=settings)

    @property
    def is_configured(self) -> bool:
        """True when global Supermemory env settings allow API calls."""
        return is_supermemory_configured(self._root_settings)

    async def add_or_replace_document(
        self,
        *,
        content: str,
        container_tag: str,
        custom_id: str,
        metadata: dict[str, str | int | float | bool],
        entity_context: str | None = None,
    ) -> dict[str, Any] | None:
        """Upsert a CRM snapshot with full content replacement.

        Supermemory treats ``POST /v3/documents`` with an existing ``customId`` as an
        incremental merge (diff-only processing). Canonical CRM state must fully replace
        the prior document, so we ``PATCH`` first and ``POST`` only when the document
        does not exist yet (see Supermemory ingesting docs: "Replace entire document").

        Returns:
            Parsed JSON response, or ``None`` when Supermemory is not configured.
        """
        if not self.is_configured:
            logger.warning("supermemory_add_skipped_not_configured custom_id=%s", custom_id)
            return None

        payload = self._document_upsert_payload(
            content=content,
            container_tag=container_tag,
            custom_id=custom_id,
            metadata=metadata,
            entity_context=entity_context,
        )

        try:
            return await self._request_json(
                method="PATCH",
                path=self._document_path(custom_id),
                json_body=payload,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            logger.info(
                "supermemory_document_create_after_missing custom_id=%s",
                custom_id,
            )
            return await self._request_json(
                method="POST",
                path="/v3/documents",
                json_body=payload,
            )

    @staticmethod
    def _document_path(custom_id: str) -> str:
        """URL path for document get/update/delete (accepts ``customId`` or internal id)."""
        return f"/v3/documents/{quote(custom_id, safe='')}"

    @staticmethod
    def _document_upsert_payload(
        *,
        content: str,
        container_tag: str,
        custom_id: str,
        metadata: dict[str, str | int | float | bool],
        entity_context: str | None,
    ) -> dict[str, Any]:
        """Build the JSON body shared by create (POST) and replace (PATCH)."""
        payload: dict[str, Any] = {
            "content": content,
            "containerTag": container_tag,
            "customId": custom_id,
            "metadata": metadata,
        }
        if entity_context:
            payload["entityContext"] = entity_context[:_ENTITY_CONTEXT_MAX_LEN]
        return payload

    async def search_hybrid(
        self,
        *,
        query: str,
        container_tag: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SupermemorySearchHit]:
        """Semantic + chunk search scoped to one container (``POST /v4/search``).

        Hybrid mode returns ``memory`` and/or ``chunk`` text per hit per Supermemory docs.
        """
        if not self.is_configured:
            logger.warning("supermemory_search_skipped_not_configured")
            return []

        payload: dict[str, Any] = {
            "q": query,
            "containerTag": container_tag,
            "searchMode": "hybrid",
            "limit": max(1, min(limit, 100)),
        }
        if filters is not None:
            payload["filters"] = filters

        data = await self._request_json(
            method="POST",
            path="/v4/search",
            json_body=payload,
        )
        return _parse_search_hits(data)

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        json_body: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an HTTP request with retries on transient failures."""
        client = await get_supermemory_http_client(self._root_settings)
        last_error: Exception | None = None

        for attempt in range(self._settings.num_retries):
            try:
                response = await client.request(method, path, json=json_body)
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                if not response.content:
                    return {}
                data = response.json()
                return data if isinstance(data, dict) else {"result": data}
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 >= self._settings.num_retries:
                    break
                delay = self._settings.retry_interval_seconds * (2**attempt)
                logger.warning(
                    "supermemory_request_retry attempt=%s path=%s delay=%s",
                    attempt + 1,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)

        logger.exception(
            "supermemory_request_failed",
            extra={"path": path, "custom_id": json_body.get("customId")},
        )
        if last_error is not None:
            raise last_error
        raise RuntimeError("supermemory_request_failed")


def _parse_search_hits(data: dict[str, Any]) -> list[SupermemorySearchHit]:
    """Normalize Supermemory search JSON to ``SupermemorySearchHit`` rows."""
    raw = data.get("results")
    if not isinstance(raw, list):
        return []
    hits: list[SupermemorySearchHit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        hit_id = str(item.get("id") or "").strip()
        memory = item.get("memory")
        chunk = item.get("chunk")
        if isinstance(memory, str) and memory.strip():
            text = memory.strip()
        elif isinstance(chunk, str) and chunk.strip():
            text = chunk.strip()
        else:
            continue
        meta = item.get("metadata")
        metadata = meta if isinstance(meta, dict) else None
        if not hit_id:
            hit_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
        hits.append(SupermemorySearchHit(id=hit_id, text=text, metadata=metadata))
    return hits
