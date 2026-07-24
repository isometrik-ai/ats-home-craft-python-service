"""Unit tests for TypesenseService HTTP helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import libs.shared_utils.typesense_service as ts_module
from libs.shared_utils.typesense_service import TypesenseService


@pytest.fixture(autouse=True)
def reset_typesense_client():
    """Reset process-global httpx client between tests."""
    ts_module._state.client = None
    yield
    ts_module._state.client = None


def test_parse_import_response_success_lines() -> None:
    """Import parser should ignore successful NDJSON lines."""
    text = '{"success": true}\n{"success": true}\n'
    assert TypesenseService._parse_import_response(text) == []


def test_parse_import_response_collects_failures() -> None:
    """Import parser should return failed document entries."""
    text = '{"success": true}\n{"success": false, "error": "bad field", "document": "{}"}\n'
    failures = TypesenseService._parse_import_response(text)
    assert len(failures) == 1
    assert failures[0]["error"] == "bad field"


def test_build_embedding_text_concatenates_fields() -> None:
    """Embedding text builder should join searchable document fields."""
    text = ts_module._build_embedding_text(
        {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "tags": ["vip"],
        }
    )
    assert "Jane Doe" in text
    assert "jane@example.com" in text
    assert "vip" in text


@pytest.mark.asyncio
async def test_ensure_collection_existing() -> None:
    """ensure_collection should no-op when collection already exists."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, **_kwargs):
        del self, method, path
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        await service.ensure_collection()
    assert service._ensured is True


@pytest.mark.asyncio
async def test_ensure_collection_creates_on_404() -> None:
    """ensure_collection should create collection when missing."""
    service = TypesenseService(collection_name="contacts")
    calls: list[str] = []

    async def fake_request(self, method, path, **_kwargs):
        del self
        calls.append(f"{method}:{path}")
        if method == "GET":
            response = MagicMock()
            response.status_code = 404
            raise httpx.HTTPStatusError(
                "missing",
                request=MagicMock(),
                response=response,
            )
        response = MagicMock()
        response.status_code = 201
        response.raise_for_status = MagicMock()
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        await service.ensure_collection()
    assert service._ensured is True
    assert any(call.startswith("POST:/collections") for call in calls)


@pytest.mark.asyncio
async def test_delete_document_calls_api() -> None:
    """delete_document should DELETE the document endpoint."""
    service = TypesenseService(collection_name="contacts")
    captured: dict[str, str] = {}

    async def fake_request(self, method, path, **_kwargs):
        del self
        captured["method"] = method
        captured["path"] = path
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        with patch.object(TypesenseService, "ensure_collection", AsyncMock()):
            await service.delete_document("doc-1")
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/collections/contacts/documents/doc-1"


@pytest.mark.asyncio
async def test_search_returns_first_result() -> None:
    """search should return the first multi_search result payload."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, *, json_body=None, **_kwargs):
        del self, method, path
        assert json_body["searches"][0]["collection"] == "contacts"
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"results": [{"found": 2, "hits": []}]})
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        with patch.object(TypesenseService, "ensure_collection", AsyncMock()):
            result = await service.search({"q": "acme", "query_by": "name"})
    assert result["found"] == 2


@pytest.mark.asyncio
async def test_generate_scoped_search_key() -> None:
    """generate_scoped_search_key should return scoped key from API."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, *, json_body=None, **_kwargs):
        del self, method, path
        assert json_body["filter_by"] == "organization_id:=org-1"
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"scoped_key": "scoped-key-123"})
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        key = await service.generate_scoped_search_key("org-1")
    assert key == "scoped-key-123"


@pytest.mark.asyncio
async def test_upsert_documents_bulk_empty_is_noop() -> None:
    """upsert_documents_bulk should no-op on empty input."""
    service = TypesenseService(collection_name="contacts")
    request_mock = AsyncMock()
    with patch.object(TypesenseService, "_request", request_mock):
        await service.upsert_documents_bulk([])
    request_mock.assert_not_called()


@pytest.mark.asyncio
async def test_embed_query_text_returns_none_for_blank() -> None:
    """embed_query_text should return None for empty strings."""
    service = TypesenseService(collection_name="contacts")
    assert await service.embed_query_text("   ") is None


@pytest.mark.asyncio
async def test_embed_query_text_uses_openai_client(monkeypatch) -> None:
    """embed_query_text should return embedding vector from OpenAI."""
    service = TypesenseService(collection_name="contacts")
    embedding = [0.1, 0.2, 0.3]

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=embedding)])
    )

    async def fake_get_client():
        return mock_client

    monkeypatch.setattr(ts_module, "_get_embedding_client", fake_get_client)
    result = await service.embed_query_text("search me")
    assert result == embedding


@pytest.mark.asyncio
async def test_upsert_documents_bulk_raises_on_failures() -> None:
    """upsert_documents_bulk should raise when import rejects documents."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, **_kwargs):
        del self, method, path
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.text = json.dumps({"success": False, "error": "bad doc"})
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        with patch.object(TypesenseService, "ensure_collection", AsyncMock()):
            with patch.object(ts_module, "_embed_documents", AsyncMock(return_value=[None])):
                with pytest.raises(RuntimeError, match="typesense_bulk_upsert_failed"):
                    await service.upsert_documents_bulk([{"id": "1", "name": "Acme"}])


@pytest.mark.asyncio
async def test_upsert_documents_bulk_success() -> None:
    """upsert_documents_bulk succeeds when import accepts all documents."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, **_kwargs):
        del self, method, path
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.text = '{"success": true}\n'
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        with patch.object(TypesenseService, "ensure_collection", AsyncMock()):
            with patch.object(ts_module, "_embed_documents", AsyncMock(return_value=[[0.1]])):
                await service.upsert_documents_bulk([{"id": "1", "full_name": "Acme"}])


@pytest.mark.asyncio
async def test_request_retries_transient_status() -> None:
    """_request retries retryable HTTP status codes before succeeding."""
    service = TypesenseService(collection_name="contacts")
    attempts = {"count": 0}

    async def fake_client_request(*_args, **_kwargs):
        attempts["count"] += 1
        response = MagicMock()
        if attempts["count"] == 1:
            response.status_code = 503
            raise httpx.HTTPStatusError(
                "unavailable",
                request=MagicMock(),
                response=response,
            )
        response.status_code = 200
        response.raise_for_status = MagicMock()
        return response

    mock_client = MagicMock()
    mock_client.request = fake_client_request
    with patch.object(ts_module, "get_typesense_http_client", AsyncMock(return_value=mock_client)):
        with patch.object(service._settings.typesense, "num_retries", 1, create=True):
            response = await service._request("GET", "/health")
    assert response.status_code == 200
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_get_typesense_http_client_creates_singleton() -> None:
    """get_typesense_http_client lazily creates and reuses the shared client."""
    client = await ts_module.get_typesense_http_client()
    same_client = await ts_module.get_typesense_http_client()
    assert client is same_client
    await ts_module.close_typesense_http_client()


@pytest.mark.asyncio
async def test_close_typesense_http_client_is_safe_when_uninitialized() -> None:
    """close_typesense_http_client is a no-op when no client exists."""
    ts_module._state.client = None
    await ts_module.close_typesense_http_client()


def test_from_settings_constructor() -> None:
    """from_settings mirrors the primary constructor."""
    service = TypesenseService.from_settings(collection_name="contacts")
    assert service._collection_name == "contacts"


@pytest.mark.asyncio
async def test_generate_scoped_search_key_missing_value() -> None:
    """generate_scoped_search_key raises when API omits scoped key."""
    service = TypesenseService(collection_name="contacts")

    async def fake_request(self, method, path, **_kwargs):
        del self, method, path
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={})
        return response

    with patch.object(TypesenseService, "_request", fake_request):
        with pytest.raises(ValueError):
            await service.generate_scoped_search_key("org-1")


@pytest.mark.asyncio
async def test_embed_documents_maps_vectors(monkeypatch) -> None:
    """_embed_documents returns positional embeddings for non-empty documents."""
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.1, 0.2])])
    )

    async def fake_get_client():
        return mock_client

    monkeypatch.setattr(ts_module, "_get_embedding_client", fake_get_client)
    vectors = await ts_module._embed_documents([{"full_name": "Jane Doe"}])
    assert vectors == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_embed_documents_all_empty_returns_none_slots() -> None:
    """_embed_documents returns None slots when documents have no embeddable text."""
    vectors = await ts_module._embed_documents([{"id": "1"}])
    assert vectors == [None]


@pytest.mark.asyncio
async def test_embed_query_text_no_embedding_data(monkeypatch) -> None:
    """embed_query_text returns None when OpenAI returns no vectors."""
    service = TypesenseService(collection_name="contacts")
    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(return_value=MagicMock(data=[]))

    async def fake_get_client():
        return mock_client

    monkeypatch.setattr(ts_module, "_get_embedding_client", fake_get_client)
    assert await service.embed_query_text("search me") is None


def test_default_schema_for_companies_collection() -> None:
    """Schema resolver returns companies schema for companies collection."""
    settings = MagicMock()
    settings.typesense.contacts_collection_name = "contacts"
    settings.typesense.companies_collection_name = "companies"
    schema = ts_module._default_schema_for_collection(
        collection_name="companies",
        settings=settings,
    )
    assert schema is ts_module.COMPANIES_COLLECTION_SCHEMA


@pytest.mark.asyncio
async def test_get_typesense_http_client_double_checked_lock() -> None:
    """Concurrent client creation returns the same cached client."""
    ts_module._state.client = None

    class FakeClient:
        """Minimal stand-in for httpx.AsyncClient in lock tests."""

    with patch.object(ts_module.httpx, "AsyncClient", return_value=FakeClient()):
        first = await ts_module.get_typesense_http_client()
        ts_module._state.client = first
        second = await ts_module.get_typesense_http_client()
    assert first is second


@pytest.mark.asyncio
async def test_get_embedding_client_caches_instance(monkeypatch) -> None:
    """Embedding client is created once and reused."""
    ts_module._embedding_state.client = None
    fake = MagicMock()
    monkeypatch.setattr(ts_module, "AsyncOpenAI", MagicMock(return_value=fake))
    monkeypatch.setattr(ts_module.shared_settings, "openai_api_key", "sk-test")
    first = await ts_module._get_embedding_client()
    second = await ts_module._get_embedding_client()
    assert first is second is fake
