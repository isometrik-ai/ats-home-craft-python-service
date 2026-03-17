"""Typesense service — async, httpx-only, Python 3.13+.

Design decisions
----------------
- No Typesense SDK dependency: avoids port/path quirks across SDK versions.
- Single process-global ``httpx.AsyncClient`` with a double-checked lock for safe
  lazy initialisation under async concurrency.
- Retry logic is intentional and minimal: only network/timeout errors and the small
  set of Typesense-specific transient HTTP codes (429, 502, 503, 504) are retried.
- ``ensure_collection`` is idempotent and cached per ``TypesenseService`` instance so
  that the first request pays the cost but subsequent requests are essentially free.
- NDJSON bulk import follows Typesense's documented contract exactly.
- Scoped search keys expire in one hour; adjust ``_SCOPED_KEY_TTL_SECONDS`` as needed.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from apps.user_service.app.search.client_typesense_schema import (
    CLIENT_COLLECTION_SCHEMA,
)
from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504})
_SCOPED_KEY_TTL_SECONDS: Final[int] = 3_600  # 1 hour

logger = get_logger("typesense_service")


# ---------------------------------------------------------------------------
# Process-global HTTP client (lazy, connection-pooled)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ClientState:
    """Process-global cached httpx client state."""

    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state = _ClientState()


def _build_base_url(settings: SharedAppSettings) -> str:
    """Return ``<protocol>://<host>`` — port is intentionally omitted.

    The Typesense SDK has version-specific ``port`` / ``path`` quirks; this module
    speaks raw HTTP and relies on the host being reachable via DNS or reverse proxy.
    """
    typesense_settings = settings.typesense
    protocol: str = getattr(typesense_settings, "protocol", "http")
    return f"{protocol}://{typesense_settings.host}"


def _auth_headers(settings: SharedAppSettings) -> dict[str, str]:
    """Return the authentication headers for the Typesense API."""
    return {"X-TYPESENSE-API-KEY": settings.typesense.admin_api_key}


async def get_typesense_http_client(
    settings: SharedAppSettings | None = None,
) -> httpx.AsyncClient:
    """Return (or lazily create) the process-global ``httpx.AsyncClient``.

    Thread-/coroutine-safe via a double-checked async lock.
    """
    if _state.client is not None:
        return _state.client

    async with _state.lock:
        if _state.client is not None:
            return _state.client

        cfg = settings or shared_settings
        typesense_settings = cfg.typesense
        timeout = float(getattr(typesense_settings, "connection_timeout_seconds", 10.0))

        _state.client = httpx.AsyncClient(
            base_url=_build_base_url(cfg),
            headers=_auth_headers(cfg),
            timeout=httpx.Timeout(timeout),
        )
        logger.info(
            "typesense_http_client_created",
            extra={"base_url": _build_base_url(cfg)},
        )

    return _state.client


async def close_typesense_http_client() -> None:
    """Close and discard the cached ``httpx.AsyncClient``.

    Safe to call multiple times; a no-op when the client was never created.
    """
    async with _state.lock:
        if _state.client is None:
            return
        client, _state.client = _state.client, None

    await client.aclose()
    logger.info("typesense_http_client_closed")


# ---------------------------------------------------------------------------
# TypesenseService
# ---------------------------------------------------------------------------


class TypesenseService:
    """Async Typesense helper backed by raw ``httpx`` calls.

    Responsibilities
    ----------------
    - Idempotent collection bootstrap (cached after first success).
    - Focused helpers: bulk upsert, single delete, search, scoped-key generation.
    - Configurable retry with exponential back-off for transient failures.

    Usage
    -----
    .. code-block:: python

        svc = TypesenseService(collection_name="clients")
        await svc.upsert_documents_bulk(docs)
        results = await svc.search({"q": "acme", "query_by": "name"})
    """

    __slots__ = (
        "_settings",
        "_collection_name",
        "_ensure_lock",
        "_ensured",
    )

    def __init__(
        self,
        *,
        collection_name: str,
        settings: SharedAppSettings | None = None,
    ) -> None:
        self._settings = settings or shared_settings
        self._collection_name = collection_name
        self._ensure_lock = asyncio.Lock()
        self._ensured = False

    @classmethod
    def from_settings(
        cls,
        *,
        collection_name: str,
        settings: SharedAppSettings | None = None,
    ) -> "TypesenseService":
        """Convenience constructor to mirror existing call sites.

        Keeps the public API explicit while allowing legacy code to migrate to this
        httpx-based implementation without behavioural changes.
        """
        return cls(collection_name=collection_name, settings=settings)

    # ------------------------------------------------------------------
    # Internal HTTP primitive
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        content: str | bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute a Typesense HTTP request, retrying on transient failures.

        Retry policy
        ~~~~~~~~~~~~
        - ``num_retries`` (default 0) controls how many *extra* attempts are made.
        - Only ``httpx.TimeoutException``, ``httpx.NetworkError``, and HTTP responses
          in ``_RETRYABLE_STATUS_CODES`` trigger a retry.
        - Non-retryable ``HTTPStatusError`` (e.g. 400, 404, 409) raise immediately.
        """
        typesense_settings = self._settings.typesense
        max_retries: int = max(0, int(getattr(typesense_settings, "num_retries", 0)))
        retry_delay: float = float(getattr(typesense_settings, "retry_interval_seconds", 0.2))
        total_attempts = max_retries + 1

        client = await get_typesense_http_client(self._settings)
        last_exc: Exception | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    content=content,
                    headers=extra_headers,
                )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    # Raise so the except branch can decide whether to retry.
                    response.raise_for_status()

                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    # Permanent error — surface immediately, no retry.
                    raise
                last_exc = exc

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc

            if attempt < total_attempts:
                logger.warning(
                    "typesense_request_retrying",
                    extra={
                        "attempt": attempt,
                        "total": total_attempts,
                        "error": str(last_exc),
                    },
                )
                await asyncio.sleep(retry_delay)

        raise last_exc or RuntimeError("typesense_request_failed_unexpectedly")

    # ------------------------------------------------------------------
    # Collection bootstrap
    # ------------------------------------------------------------------

    async def ensure_collection(self) -> None:
        """Create the Typesense collection if it does not yet exist.

        Idempotent: a ``404`` triggers creation; any other error propagates.
        The result is cached on this instance — subsequent calls are a no-op.
        """
        if self._ensured:
            return

        async with self._ensure_lock:
            if self._ensured:
                return

            try:
                await self._request("GET", f"/collections/{self._collection_name}")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise

                schema: dict[str, Any] = {**CLIENT_COLLECTION_SCHEMA, "name": self._collection_name}
                await self._request("POST", "/collections", json_body=schema)
                logger.info(
                    "typesense_collection_created",
                    extra={"collection": self._collection_name},
                )

            self._ensured = True

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def upsert_documents_bulk(self, documents: list[Mapping[str, Any]]) -> None:
        """Bulk-upsert *documents* via the Typesense ``/import`` endpoint.

        The payload is serialised as NDJSON (newline-delimited JSON) as required
        by Typesense.  An empty list is a no-op.
        """
        if not documents:
            return

        await self.ensure_collection()

        ndjson = "\n".join(json.dumps(dict(doc), separators=(",", ":")) for doc in documents)
        await self._request(
            "POST",
            f"/collections/{self._collection_name}/documents/import",
            params={"action": "upsert"},
            content=ndjson,
            extra_headers={"Content-Type": "text/plain"},
        )
        logger.info(
            "typesense_bulk_upsert",
            extra={"count": len(documents)},
        )

    async def delete_document(self, document_id: str) -> None:
        """Delete a single document by its ``id`` field."""
        await self.ensure_collection()
        await self._request(
            "DELETE",
            f"/collections/{self._collection_name}/documents/{document_id}",
        )
        logger.info(
            "typesense_document_deleted",
            extra={"document_id": document_id},
        )

    async def search(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a search against this collection and return the raw JSON result."""
        await self.ensure_collection()
        response = await self._request(
            "GET",
            f"/collections/{self._collection_name}/documents/search",
            params=dict(params),
        )
        return response.json()  # type: ignore[no-any-return]

    async def generate_scoped_search_key(self, organization_id: str) -> str:
        """Return a short-lived, read-only key scoped to *organization_id*.

        The key expires in ``_SCOPED_KEY_TTL_SECONDS`` (default 1 hour) and is
        filtered to documents where ``organization_id`` matches exactly.
        """
        expires_at = int(time.time()) + _SCOPED_KEY_TTL_SECONDS
        response = await self._request(
            "POST",
            "/keys/generate_scoped_search_key",
            json_body={
                "search_api_key": self._settings.typesense.search_only_api_key,
                "expires_at": expires_at,
                "filter_by": f"organization_id:={organization_id}",
            },
        )
        data: dict[str, Any] = response.json()
        scoped_key: str = data.get("scoped_key") or data.get("key") or ""
        if not scoped_key:
            raise ValueError("typesense_scoped_key_missing_in_response", data)
        return scoped_key
