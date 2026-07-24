"""Unit tests for Isometrik Strands HTTP client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import libs.shared_utils.isometrik_strands_client as strands_module
from libs.shared_utils.isometrik_strands_client import (
    _new_session_id,
    _preview_strands_response_body,
    _sanitize_agent_message,
    _strands_headers,
    _truncate_for_log,
    call_strands_agent,
    close_strands_http_client,
    get_strands_http_client,
    init_strands_http_client,
)


@pytest.fixture(autouse=True)
def reset_strands_state():
    """Reset process-global Strands client between tests."""
    strands_module._state.client = None
    yield
    strands_module._state.client = None


def test_truncate_for_log_short_and_long():
    """Log truncation keeps short strings and truncates long ones."""
    assert _truncate_for_log("hello") == "hello"
    long_text = "x" * 3000
    truncated = _truncate_for_log(long_text, max_len=10)
    assert truncated.startswith("x" * 10)
    assert "truncated" in truncated


def test_preview_strands_response_body_prefers_text():
    """Response preview prefers the agent text field."""
    preview = _preview_strands_response_body({"text": "  agent reply  ", "other": 1})
    assert preview == "agent reply"


def test_preview_strands_response_body_json_fallback():
    """Response preview falls back to compact JSON when text is absent."""
    preview = _preview_strands_response_body({"status": "ok", "count": 2})
    assert "status" in preview
    assert "ok" in preview


def test_sanitize_agent_message_strips_and_bounds():
    """Agent messages are trimmed and capped in length."""
    assert _sanitize_agent_message("  hello  ") == "hello"
    long_msg = "a" * 5000
    assert len(_sanitize_agent_message(long_msg)) == 4000


def test_new_session_id_is_numeric_string():
    """Session ids are millisecond timestamp strings."""
    session_id = _new_session_id()
    assert session_id.isdigit()


def test_strands_headers_include_auth(monkeypatch):
    """Headers include authorization from settings."""
    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(strands_auth_token="  token-abc  ")),
    )
    headers = _strands_headers()
    assert headers["Authorization"] == "token-abc"
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_get_strands_http_client_creates_singleton(monkeypatch):
    """get_strands_http_client lazily creates and reuses one client."""
    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(
            isometrik=SimpleNamespace(
                admin_api_url="https://admin.example.com/",
                strands_request_timeout_seconds=90,
            )
        ),
    )
    client_a = await get_strands_http_client()
    client_b = await get_strands_http_client()
    assert client_a is client_b
    assert str(client_a.base_url).rstrip("/") == "https://admin.example.com"
    await close_strands_http_client()


@pytest.mark.asyncio
async def test_init_strands_http_client_skipped_when_disabled(monkeypatch):
    """init_strands_http_client is a no-op when Isometrik is disabled."""
    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(isometrik=SimpleNamespace(is_enabled=False)),
    )
    await init_strands_http_client()
    assert strands_module._state.client is None


@pytest.mark.asyncio
async def test_init_strands_http_client_eager_create(monkeypatch):
    """init_strands_http_client creates the client when enabled."""
    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(
            isometrik=SimpleNamespace(
                is_enabled=True,
                admin_api_url="https://admin.example.com",
                strands_request_timeout_seconds=120,
            )
        ),
    )
    await init_strands_http_client()
    assert strands_module._state.client is not None
    await close_strands_http_client()
    assert strands_module._state.client is None


@pytest.mark.asyncio
async def test_call_strands_agent_posts_and_parses(monkeypatch):
    """call_strands_agent POSTs payload and returns parsed JSON body."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "done", "meta": {}}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(
            isometrik=SimpleNamespace(
                admin_api_url="https://admin.example.com",
                strands_auth_token="tok",
                strands_request_timeout_seconds=60,
            )
        ),
    )
    monkeypatch.setattr(
        strands_module, "get_strands_http_client", AsyncMock(return_value=mock_client)
    )

    body = await call_strands_agent(
        agent_id="agent-1",
        message="create template",
        stream=False,
        schema={"organization_id": "org-1"},
    )

    assert body["text"] == "done"
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.await_args.kwargs
    payload = call_kwargs["json"]
    assert payload["agent_id"] == "agent-1"
    assert payload["message"] == "create template"
    assert payload["schema"] == {"organization_id": "org-1"}
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_call_strands_agent_raises_on_non_object_body(monkeypatch):
    """call_strands_agent rejects non-dict JSON responses."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = ["not", "a", "dict"]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(
            isometrik=SimpleNamespace(
                admin_api_url="https://admin.example.com",
                strands_auth_token="tok",
                strands_request_timeout_seconds=60,
            )
        ),
    )
    monkeypatch.setattr(
        strands_module, "get_strands_http_client", AsyncMock(return_value=mock_client)
    )

    with pytest.raises(ValueError, match="JSON object"):
        await call_strands_agent(agent_id="a1", message="hi")


@pytest.mark.asyncio
async def test_call_strands_agent_propagates_http_error(monkeypatch):
    """HTTP errors from the Strands API propagate to callers."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "boom",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
    )

    monkeypatch.setattr(
        strands_module,
        "shared_settings",
        SimpleNamespace(
            isometrik=SimpleNamespace(
                admin_api_url="https://admin.example.com",
                strands_auth_token="tok",
                strands_request_timeout_seconds=60,
            )
        ),
    )
    monkeypatch.setattr(
        strands_module, "get_strands_http_client", AsyncMock(return_value=mock_client)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await call_strands_agent(agent_id="a1", message="hi")
