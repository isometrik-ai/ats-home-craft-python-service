"""Unit tests for audit_decorator helpers and decorator wrapper."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    _extract_request_body,
    _parse_form_data,
    _parse_json_body,
    _should_log_audit,
    audit_api_call,
    build_changed_data,
    maybe_log_audit_on_error,
)

_AUDIT_DECORATOR = "apps.user_service.app.dependencies.audit_logs.audit_decorator"


def _request(
    *,
    path: str = "/api/contacts",
    method: str = "POST",
    headers: list[tuple[str, str]] | None = None,
    client_host: str = "127.0.0.1",
    query: str = "",
) -> Request:
    """Build a minimal Starlette Request."""
    hdrs = headers or [("content-type", "application/json")]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs],
        "client": (client_host, 12345),
    }
    return Request(scope)


def _user_context(*, email: str = "user@example.com") -> dict[str, str]:
    return {
        "organization_id": "org-1",
        "user_id": "user-1",
        "user_email": email,
    }


def test_should_log_audit_requires_complete_context():
    """Audit logging is skipped when user context is incomplete."""
    req = _request()
    req.state.audit_user_context = {"organization_id": "org-1", "user_id": "u1"}
    assert _should_log_audit(req) is False

    req.state.audit_user_context = _user_context(email="unknown")
    assert _should_log_audit(req) is False

    req.state.audit_user_context = _user_context()
    assert _should_log_audit(req) is True


def test_build_changed_data_nested_and_added_removed_keys():
    """build_changed_data captures nested edits and key add/remove."""
    old = {"name": "A", "meta": {"tier": "gold", "tags": ["a"]}, "score": 1}
    new = {"name": "B", "meta": {"tier": "gold", "tags": ["a", "b"]}, "extra": True}

    old_delta, new_delta, changed = build_changed_data(old, new)

    assert "name" in changed
    assert "meta.tags" in changed
    assert "extra" in changed
    assert old_delta["name"] == "A"
    assert new_delta["name"] == "B"
    assert old_delta["meta"]["tags"] == ["a"]
    assert new_delta["meta"]["tags"] == ["a", "b"]


def test_build_changed_data_skips_equal_nested_dicts():
    """Unchanged nested dicts produce no delta entries."""
    payload = {"settings": {"theme": "dark"}}
    old_delta, new_delta, changed = build_changed_data(payload, dict(payload))
    assert not changed
    assert not old_delta
    assert not new_delta


def test_build_changed_data_list_equality():
    """List values compare via JSON serialization."""
    old_delta, new_delta, changed = build_changed_data(
        {"items": [1, 2]},
        {"items": [1, 2]},
    )
    assert not changed

    _, _, changed2 = build_changed_data({"items": [1]}, {"items": [2]})
    assert changed2 == ["items"]


def test_parse_json_body_invalid_json_returns_string():
    """Invalid JSON bytes fall back to decoded string."""
    assert _parse_json_body(b"not-json") == "not-json"


@pytest.mark.asyncio
async def test_extract_request_body_json_cached():
    """JSON body is parsed from cached bytes on request.state."""
    req = _request(headers=[("content-type", "application/json")])
    req.state.cached_body = b'{"name": "Jane"}'

    body = await _extract_request_body(req)
    assert body == {"name": "Jane"}


@pytest.mark.asyncio
async def test_extract_request_body_missing_body_returns_empty():
    """Missing cached body or content-type yields empty dict."""
    req = _request(headers=[("content-type", "application/json")])
    assert await _extract_request_body(req) == {}


@pytest.mark.asyncio
async def test_extract_request_body_form_urlencoded():
    """Form bodies are parsed into a flat dict."""
    req = _request(headers=[("content-type", "application/x-www-form-urlencoded")])
    req.state.cached_body = b"a=1"

    with patch(
        f"{_AUDIT_DECORATOR}._parse_form_data",
        AsyncMock(return_value={"a": "1"}),
    ):
        body = await _extract_request_body(req)
    assert body == {"a": "1"}


@pytest.mark.asyncio
async def test_extract_request_body_multipart_file_placeholder():
    """Multipart uploads stringify file fields."""
    req = _request(headers=[("content-type", "multipart/form-data; boundary=x")])
    req.state.cached_body = b"content"

    with patch(
        f"{_AUDIT_DECORATOR}._parse_form_data",
        AsyncMock(return_value={"file": "<file: doc.pdf>"}),
    ):
        body = await _extract_request_body(req)
    assert body["file"] == "<file: doc.pdf>"


@pytest.mark.asyncio
async def test_parse_form_data_multipart_replaces_uploads():
    """_parse_form_data labels uploaded files in multipart requests."""
    req = _request(headers=[("content-type", "multipart/form-data; boundary=x")])
    upload = MagicMock()
    upload.filename = "doc.pdf"
    form_data = MagicMock()
    form_data.items.return_value = [("file", upload)]
    req.form = AsyncMock(return_value=form_data)

    body = await _parse_form_data(req, "multipart/form-data; boundary=x")
    assert body["file"] == "<file: doc.pdf>"


@pytest.mark.asyncio
async def test_extract_request_body_decode_error_returns_error_dict():
    """Decode failures return a structured _error payload."""
    req = _request(headers=[("content-type", "application/json")])
    req.state.cached_body = b"\xff\xfe"

    body = await _extract_request_body(req)
    assert "_error" in body


@pytest.mark.asyncio
async def test_audit_api_call_skips_logging_without_user_context():
    """Decorator returns handler result when audit user context is absent."""

    @audit_api_call(action_type="CREATE", table_name="contacts")
    async def handler(**kwargs):
        return Response(status_code=201)

    req = _request()
    req.state.audit_user_context = {}

    with patch(f"{_AUDIT_DECORATOR}._log_audit_event", AsyncMock()) as mock_log:
        result = await handler(request=req)

    assert result.status_code == 201
    mock_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_api_call_logs_when_context_present():
    """Decorator invokes _log_audit_event when audit context is valid."""

    @audit_api_call(action_type="UPDATE", table_name="leads", category="crm")
    async def handler(**kwargs):
        return Response(status_code=200)

    req = _request()
    req.state.audit_user_context = _user_context()
    req.state.audit_description = "Updated lead"
    req.state.raw_audit_old_data = {"name": "Old"}
    req.state.raw_audit_new_data = {"name": "New"}

    with patch(f"{_AUDIT_DECORATOR}._log_audit_event", AsyncMock()) as mock_log:
        result = await handler(request=req)

    assert result.status_code == 200
    mock_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_api_call_raises_when_description_missing():
    """_log_audit_event requires audit_description on the request."""

    @audit_api_call(action_type="DELETE", table_name="contacts")
    async def handler(**kwargs):
        return Response(status_code=204)

    req = _request()
    req.state.audit_user_context = _user_context()

    with pytest.raises(ValueError, match="Missing required audit description"):
        await handler(request=req)


@pytest.mark.asyncio
async def test_maybe_log_audit_on_error_skips_incomplete_context():
    """Error audit helper no-ops without full user context."""
    req = _request()
    req.state.audit_user_context = {"user_email": "unknown"}

    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.audit_logger"
    ) as mock_logger:
        await maybe_log_audit_on_error(req, description="boom")
        mock_logger.log_audit_event.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_log_audit_on_error_logs_event():
    """Error audit helper writes a high-risk audit event."""
    req = _request()
    req.state.audit_user_context = _user_context()
    req.state._audit_metadata = {
        "table_name": "contacts",
        "data_classification": "pii",
        "compliance_tags": ["gdpr"],
        "category": "crm",
    }
    req.state.cached_body = json.dumps({"id": "c1"}).encode()

    mock_logger = MagicMock()
    mock_logger.log_audit_event = AsyncMock()
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.audit_logger",
        mock_logger,
    ):
        await maybe_log_audit_on_error(req, description="server error", status_code=500)

    mock_logger.log_audit_event.assert_awaited_once()
    event = mock_logger.log_audit_event.await_args.args[0]
    assert event.action_type == "ERROR"
    assert event.risk_level == "high"


@pytest.mark.asyncio
async def test_maybe_log_audit_on_error_swallows_attribute_error():
    """AttributeError during error audit is logged and suppressed."""
    req = MagicMock()
    req.state = MagicMock()
    type(req.state).audit_user_context = property(
        lambda self: (_ for _ in ()).throw(AttributeError("missing"))
    )

    with patch(f"{_AUDIT_DECORATOR}.logger") as mock_logger:
        await maybe_log_audit_on_error(req, description="boom")
        mock_logger.warning.assert_called_once()
