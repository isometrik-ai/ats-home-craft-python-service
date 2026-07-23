"""Unit tests for OpenAI chat completion HTTP helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import libs.shared_utils.openai_chat_service as oai_module
from libs.shared_utils.openai_chat_service import (
    _build_chat_completion_payload,
    _content_from_message,
    _extract_message_content,
    close_openai_http_client,
    create_chat_completion,
    is_openai_configured,
)


@pytest.fixture(autouse=True)
def reset_openai_client():
    """Reset cached OpenAI HTTP client between tests."""
    oai_module._state.client = None
    yield
    oai_module._state.client = None


def test_is_openai_configured_with_key() -> None:
    """is_openai_configured returns True when API key is set."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    assert is_openai_configured(settings) is True


def test_is_openai_configured_without_key() -> None:
    """is_openai_configured returns False for blank API key."""
    settings = MagicMock()
    settings.openai_api_key = "   "
    assert is_openai_configured(settings) is False


def test_content_from_message_string() -> None:
    """_content_from_message extracts plain string content."""
    assert _content_from_message({"content": " hello "}) == "hello"


def test_content_from_message_multipart() -> None:
    """_content_from_message joins multipart text blocks."""
    message = {"content": [{"text": "Line 1"}, {"text": "Line 2"}]}
    assert _content_from_message(message) == "Line 1\nLine 2"


def test_extract_message_content_from_choices() -> None:
    """_extract_message_content reads assistant text from API body."""
    data = {
        "choices": [
            {
                "message": {"content": "Answer"},
                "finish_reason": "stop",
            }
        ]
    }
    assert _extract_message_content(data) == "Answer"


def test_build_chat_completion_payload() -> None:
    """Payload builder should include optional response_format."""
    payload = _build_chat_completion_payload(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Hi"}],
        max_completion_tokens=128,
        response_format={"type": "json_object"},
    )
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["response_format"]["type"] == "json_object"


@pytest.mark.asyncio
async def test_create_chat_completion_returns_text(monkeypatch) -> None:
    """create_chat_completion should return assistant message text."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"choices": [{"message": {"content": "Done"}, "finish_reason": "stop"}]}
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    async def fake_get_client(_settings=None):
        return mock_client

    monkeypatch.setattr(oai_module, "get_openai_http_client", fake_get_client)

    text = await create_chat_completion(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Summarize"}],
        max_completion_tokens=64,
        settings=settings,
    )
    assert text == "Done"
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_chat_completion_not_configured() -> None:
    """create_chat_completion raises when OpenAI key is missing."""
    settings = MagicMock()
    settings.openai_api_key = ""
    with pytest.raises(RuntimeError, match="OpenAI is not configured"):
        await create_chat_completion(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Hi"}],
            max_completion_tokens=32,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_close_openai_http_client() -> None:
    """close_openai_http_client should close and clear cached client."""
    mock_client = AsyncMock()
    oai_module._state.client = mock_client
    await close_openai_http_client()
    mock_client.aclose.assert_awaited_once()
    assert oai_module._state.client is None


@pytest.mark.asyncio
async def test_post_chat_completion_retries_on_429(monkeypatch) -> None:
    """Retry wrapper should succeed after transient HTTP 429."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"

    retry_response = MagicMock()
    retry_response.status_code = 429
    retry_response.request = MagicMock()

    success_response = MagicMock()
    success_response.status_code = 200
    success_response.raise_for_status = MagicMock()
    success_response.json = MagicMock(
        return_value={"choices": [{"message": {"content": "Retry ok"}, "finish_reason": "stop"}]}
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[retry_response, success_response])

    async def fake_get_client(_settings=None):
        return mock_client

    monkeypatch.setattr(oai_module, "get_openai_http_client", fake_get_client)
    monkeypatch.setattr(oai_module.asyncio, "sleep", AsyncMock())

    text = await create_chat_completion(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Hi"}],
        max_completion_tokens=32,
        settings=settings,
    )
    assert text == "Retry ok"
    assert mock_client.post.await_count == 2


def test_content_from_message_ignores_non_dict_blocks() -> None:
    """_content_from_message skips invalid multipart blocks."""
    message = {"content": ["plain", {"text": "  kept  "}, {"text": ""}]}
    assert _content_from_message(message) == "kept"


def test_content_from_message_unknown_shape() -> None:
    """_content_from_message returns empty string for unsupported content."""
    assert _content_from_message({"content": 42}) == ""


def test_extract_message_content_empty_choices() -> None:
    """_extract_message_content handles missing or invalid choices."""
    assert _extract_message_content({}) == ""
    assert _extract_message_content({"choices": []}) == ""
    assert _extract_message_content({"choices": ["bad"]}) == ""


def test_extract_message_content_logs_empty_assistant_text(monkeypatch) -> None:
    """_extract_message_content logs when assistant content is empty."""
    logged: list[tuple] = []

    def fake_warning(*args, **kwargs):
        logged.append((args, kwargs))

    monkeypatch.setattr(oai_module.logger, "warning", fake_warning)
    data = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {
            "completion_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }
    assert _extract_message_content(data) == ""
    assert logged


@pytest.mark.asyncio
async def test_init_openai_http_client_skipped_when_not_configured(monkeypatch) -> None:
    """init_openai_http_client no-ops when API key is missing."""
    settings = MagicMock()
    settings.openai_api_key = ""
    fake_get = AsyncMock()
    monkeypatch.setattr(oai_module, "get_openai_http_client", fake_get)
    await oai_module.init_openai_http_client(settings)
    fake_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_init_openai_http_client_eager_create(monkeypatch) -> None:
    """init_openai_http_client eagerly creates client when configured."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    fake_get = AsyncMock(return_value=AsyncMock())
    monkeypatch.setattr(oai_module, "get_openai_http_client", fake_get)
    await oai_module.init_openai_http_client(settings)
    fake_get.assert_awaited_once_with(settings)


@pytest.mark.asyncio
async def test_get_openai_http_client_lazy_create(monkeypatch) -> None:
    """get_openai_http_client creates and caches httpx client once."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    created = MagicMock()
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=created))

    client = await oai_module.get_openai_http_client(settings)
    assert client is created
    assert oai_module._state.client is created

    again = await oai_module.get_openai_http_client(settings)
    assert again is created
    assert httpx.AsyncClient.call_count == 1


@pytest.mark.asyncio
async def test_get_openai_http_client_not_configured() -> None:
    """get_openai_http_client raises when OpenAI key is missing."""
    settings = MagicMock()
    settings.openai_api_key = ""
    with pytest.raises(RuntimeError, match="OpenAI HTTP client is not configured"):
        await oai_module.get_openai_http_client(settings)


@pytest.mark.asyncio
async def test_post_chat_completion_raises_on_retryable_status(monkeypatch) -> None:
    """_post_chat_completion raises HTTPStatusError for retryable codes."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.request = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(httpx.HTTPStatusError):
        await oai_module._post_chat_completion(mock_client, {"model": "gpt-4.1-mini"})


@pytest.mark.asyncio
async def test_post_chat_completion_logs_client_errors(monkeypatch) -> None:
    """_post_chat_completion logs 4xx responses before raising."""
    logged: list[tuple] = []

    def fake_error(*args, **kwargs):
        logged.append((args, kwargs))

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "bad request"
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=mock_response)
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(oai_module.logger, "error", fake_error)

    with pytest.raises(httpx.HTTPStatusError):
        await oai_module._post_chat_completion(mock_client, {"model": "gpt-4.1-mini"})
    assert logged


@pytest.mark.asyncio
async def test_post_chat_completion_non_dict_json() -> None:
    """_post_chat_completion returns empty string when JSON body is not a dict."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[])
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    text = await oai_module._post_chat_completion(mock_client, {"model": "gpt-4.1-mini"})
    assert text == ""


@pytest.mark.asyncio
async def test_post_chat_completion_with_retries_exhausts(monkeypatch) -> None:
    """_post_chat_completion_with_retries re-raises after retries exhausted."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.NetworkError("down"))
    monkeypatch.setattr(oai_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(oai_module, "_DEFAULT_NUM_RETRIES", 2)

    with pytest.raises(httpx.NetworkError):
        await oai_module._post_chat_completion_with_retries(
            mock_client,
            {"model": "gpt-4.1-mini"},
        )
    assert mock_client.post.await_count == 2


@pytest.mark.asyncio
async def test_create_chat_completion_custom_timeout(monkeypatch) -> None:
    """create_chat_completion passes custom read timeout to retry wrapper."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    mock_client = AsyncMock()

    async def fake_get_client(_settings=None):
        return mock_client

    fake_retry = AsyncMock(return_value="ok")
    monkeypatch.setattr(oai_module, "get_openai_http_client", fake_get_client)
    monkeypatch.setattr(oai_module, "_post_chat_completion_with_retries", fake_retry)

    text = await create_chat_completion(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Hi"}],
        max_completion_tokens=32,
        settings=settings,
        timeout_seconds=45.0,
    )
    assert text == "ok"
    timeout_arg = fake_retry.await_args.kwargs["timeout"]
    assert timeout_arg.read == 45.0
