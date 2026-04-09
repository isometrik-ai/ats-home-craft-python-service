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
from openai import AsyncOpenAI

from apps.user_service.app.search.client_typesense_schema import CLIENT_COLLECTION_SCHEMA
from apps.user_service.app.search.company_typesense_schema import COMPANIES_COLLECTION_SCHEMA
from apps.user_service.app.search.contact_typesense_schema import CONTACTS_COLLECTION_SCHEMA
from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504})
_EMBEDDING_MODEL: Final[str] = "text-embedding-3-large"
_EMBEDDING_DIMENSIONS: Final[int] = 3072
_EMBEDDING_FIELD_NAME: Final[str] = "embedding"
_SCOPED_KEY_TTL_SECONDS: Final[int] = 3_600  # 1 hour

logger = get_logger("typesense_service")


def _default_schema_for_collection(
    *,
    collection_name: str,
    settings: SharedAppSettings,
) -> Mapping[str, Any]:
    """Return the dedicated schema for a known collection name.

    Falls back to the legacy/shared client schema for backwards compatibility.
    """
    typesense_settings = settings.typesense
    contacts_name = getattr(typesense_settings, "contacts_collection_name", None)
    companies_name = getattr(typesense_settings, "companies_collection_name", None)
    if contacts_name and collection_name == contacts_name:
        return CONTACTS_COLLECTION_SCHEMA
    if companies_name and collection_name == companies_name:
        return COMPANIES_COLLECTION_SCHEMA
    return CLIENT_COLLECTION_SCHEMA


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


@dataclass(slots=True)
class _EmbeddingClientState:
    """Process-global cached OpenAI client state for embeddings."""

    client: AsyncOpenAI | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_embedding_state = _EmbeddingClientState()


async def _get_embedding_client() -> AsyncOpenAI:
    """Return (or lazily create) the process-global ``AsyncOpenAI`` client."""
    if _embedding_state.client is not None:
        return _embedding_state.client

    async with _embedding_state.lock:
        if _embedding_state.client is not None:
            return _embedding_state.client

        api_key = shared_settings.openai_api_key
        _embedding_state.client = AsyncOpenAI(api_key=api_key)
        logger.info("openai_embedding_client_created")

    return _embedding_state.client


def _build_embedding_text(document: Mapping[str, Any]) -> str:
    """Return a concatenated text payload for embedding based on the client schema.

    The fields roughly mirror ``SEARCH_PARAMS["query_by"]`` so the vector
    representation aligns with the main full-text surface area.
    """
    text_parts: list[str] = []

    def _add_value(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            text_parts.append(value.strip())
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())

    # Keep in sync with primary query fields across dedicated schemas.
    for key in (
        # Dedicated contacts collection (v2)
        "full_name",
        "title",
        "company_names",
        # Legacy/shared client schema fields
        "name",
        "primary_contact_full_name",
        "email",
        "phone_numbers",
        "company_name",
        "primary_contact_title",
        "tags",
        "industry",
        "description",
        "address_cities",
        "work_history_companies",
        "work_history_titles",
        "skills",
        "target_market_segments",
        "current_tech_stack",
        "key_people_names",
        "product_names",
        "custom_field_values",
    ):
        if key in document:
            _add_value(document[key])

    return " ".join(text_parts).strip()


async def _embed_documents(documents: list[Mapping[str, Any]]) -> list[list[float] | None]:
    """Return embeddings for *documents* or ``None`` when no text is available.

    The returned list is positional: each entry corresponds to the respective
    document in *documents*.
    """
    texts: list[str] = []
    text_indices: list[int] = []

    for idx, doc in enumerate(documents):
        text = _build_embedding_text(doc)
        if not text:
            texts.append("")
            text_indices.append(idx)
            continue
        texts.append(text)
        text_indices.append(idx)

    # Fast path: nothing to embed.
    if not any(texts):
        return [None] * len(documents)

    # Only send non-empty texts to OpenAI to avoid wasting tokens.
    non_empty_payload: list[str] = [t for t in texts if t]
    if not non_empty_payload:
        return [None] * len(documents)

    client = await _get_embedding_client()
    response = await client.embeddings.create(
        model=_EMBEDDING_MODEL,
        input=non_empty_payload,
        dimensions=_EMBEDDING_DIMENSIONS,
    )

    embeddings: list[list[float]] = [item.embedding for item in response.data]

    # Map back to original document order, filling gaps with None where needed.
    result: list[list[float] | None] = [None] * len(documents)
    emb_idx = 0
    for original_idx, text in zip(text_indices, texts, strict=False):
        if not text:
            result[original_idx] = None
            continue
        result[original_idx] = embeddings[emb_idx]
        emb_idx += 1

    return result


# TypesenseService
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
        "_collection_schema",
        "_ensure_lock",
        "_ensured",
    )

    def __init__(
        self,
        *,
        collection_name: str,
        collection_schema: Mapping[str, Any] | None = None,
        settings: SharedAppSettings | None = None,
    ) -> None:
        self._settings = settings or shared_settings
        self._collection_name = collection_name
        # Prefer dedicated schema inferred from collection name; allow explicit override.
        schema = (
            dict(collection_schema)
            if collection_schema is not None
            else dict(_default_schema_for_collection(
                    collection_name=collection_name,
                    settings=self._settings,
                )
            )
        )
        self._collection_schema = schema
        self._ensure_lock = asyncio.Lock()
        self._ensured = False

    @classmethod
    def from_settings(
        cls,
        *,
        collection_name: str,
        collection_schema: Mapping[str, Any] | None = None,
        settings: SharedAppSettings | None = None,
    ) -> "TypesenseService":
        """Convenience constructor to mirror existing call sites.

        Keeps the public API explicit while allowing legacy code to migrate to this
        httpx-based implementation without behavioural changes.
        """
        return cls(
            collection_name=collection_name,
            collection_schema=collection_schema,
            settings=settings,
        )

    # Internal HTTP primitive

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

    # Collection bootstrap

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

                schema: dict[str, Any] = {
                    **dict(self._collection_schema), "name": self._collection_name
                }
                await self._request("POST", "/collections", json_body=schema)
                logger.info(
                    "typesense_collection_created",
                    extra={"collection": self._collection_name},
                )

            self._ensured = True

    # Public helpers
    @staticmethod
    def _parse_import_response(response_text: str) -> list[dict[str, Any]]:
        """Parse Typesense /import NDJSON response and return failure entries.

        Typesense returns one JSON object per line:
          {"success": true}
          {"success": false, "error": "...", "document": "..."}
        HTTP 200 does not guarantee per-document success.
        """
        failures: list[dict[str, Any]] = []
        for line in (response_text or "").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict) and item.get("success") is False:
                failures.append(item)
        return failures

    async def embed_query_text(self, text: str) -> list[float] | None:
        """Return an embedding vector for *text* suitable for ``vector_query``.

        Uses the same model and dimensionality as document embeddings so that
        query vectors live in the same space as indexed vectors.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        client = await _get_embedding_client()
        response = await client.embeddings.create(
            model=_EMBEDDING_MODEL,
            input=[cleaned],
            dimensions=_EMBEDDING_DIMENSIONS,
        )
        if not response.data:
            return None

        return response.data[0].embedding

    async def upsert_documents_bulk(self, documents: list[Mapping[str, Any]]) -> None:
        """Bulk-upsert *documents* via the Typesense ``/import`` endpoint.

        The payload is serialised as NDJSON (newline-delimited JSON) as required
        by Typesense.  An empty list is a no-op.
        """
        if not documents:
            return

        # Enrich documents with OpenAI embeddings so they can be used for
        # vector search in Typesense (via the ``embedding`` float[] field).
        # This mutates shallow copies to avoid side effects on caller data.
        embeddings = await _embed_documents(documents)
        enriched_documents: list[dict[str, Any]] = []
        for doc, embedding in zip(documents, embeddings, strict=False):
            enriched = dict(doc)
            if embedding is not None:
                enriched[_EMBEDDING_FIELD_NAME] = embedding
            enriched_documents.append(enriched)

        await self.ensure_collection()

        ndjson_payload = "\n".join(json.dumps(d) for d in enriched_documents) + "\n"

        response = await self._request(
            "POST",
            f"/collections/{self._collection_name}/documents/import",
            params={"action": "upsert"},
            content=ndjson_payload,
            extra_headers={"Content-Type": "text/plain"},
        )
        try:
            failures = self._parse_import_response(response.text or "")
        except Exception as exc:
            logger.exception(
                "typesense_import_response_parse_failed",
                extra={
                    "collection": self._collection_name,
                    "error": str(exc),
                },
            )
            raise

        if failures:
            # Avoid logging full documents (may contain PII). Keep it high-signal.
            logger.error(
                "typesense_bulk_upsert_partial_failure",
                extra={
                    "collection": self._collection_name,
                    "attempted": len(documents),
                    "failed": len(failures),
                    "sample_error": failures[0].get("error") if failures else None,
                },
            )
            raise RuntimeError(
                f"typesense_bulk_upsert_failed: {len(failures)}/{len(documents)} documents rejected"
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
        # Use POST /multi_search so we don't hit URL length limits when `vector_query`
        # contains an embedding. Some Typesense deployments don't support POST on
        response = await self._request(
            "POST",
            "/multi_search",
            json_body={
                "searches": [
                    {
                        "collection": self._collection_name,
                        **dict(params),
                    }
                ]
            },
        )
        data: dict[str, Any] = response.json()
        results: list[dict[str, Any]] = data.get("results") or []
        return results[0] if results else {}

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
