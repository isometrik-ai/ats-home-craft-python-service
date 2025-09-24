"""Test module for exception middleware.

This module contains tests for:
- Request context extraction
- Exception context extraction
- Request body caching middleware
- Audit logging handler
- Unified exception handler
"""

import pytest
import pytest_asyncio
import uuid
import json
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.user_service.app.dependencies.exception_middleware import (
    extract_request_context,
    extract_exception_context,
    CacheRequestBodyMiddleware,
    _handle_audit_logging,
    unified_exception_handler,
)


@pytest.fixture
def mock_request():
    """Create a mock request object for testing."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {
        "user-agent": "test-agent",
        "x-forwarded-for": "10.0.0.1",
        "content-type": "application/json"
    }
    request.form = AsyncMock(return_value={})
    request.body = AsyncMock(return_value=b'{"test": "data"}')
    request.json = AsyncMock(return_value={"test": "data"})
    request.url = MagicMock()
    request.url.path = "/test/path"
    request.method = "POST"
    request.query_params = {"param1": "value1"}
    request.scope = {
        "endpoint": None,
        "path": "/test/path",
        "method": "POST",
        "type": "http"
    }
    return request


@pytest.fixture
def mock_app():
    """Create a mock FastAPI app for testing."""
    app = FastAPI()

    @app.post("/test")
    async def test_post_endpoint():
        return {"message": "success"}

    @app.get("/test")
    async def test_get_endpoint():
        return {"message": "success"}

    @app.get("/error")
    async def error_endpoint():
        raise HTTPException(status_code=400, detail="Test error")

    @app.get("/validation-error")
    async def validation_error_endpoint():
        raise RequestValidationError(errors=[{"loc": ["test"], "msg": "Test error"}])

    @app.get("/unexpected-error")
    async def unexpected_error_endpoint():
        raise ValueError("Test error")

    return app


class TestRequestContextExtraction:
    """Tests for request context extraction."""

    def test_extract_request_context_basic(self, mock_request):
        """Test basic request context extraction."""
        context = extract_request_context(mock_request)

        assert context["method"] == "POST"
        assert context["url"] == str(mock_request.url)
        assert context["path"] == "/test/path"
        assert context["query_params"] == {"param1": "value1"}
        assert context["client_ip"] == "127.0.0.1"
        assert context["user_agent"] == "test-agent"

    def test_extract_request_context_with_body(self, mock_request):
        """Test request context extraction with body."""
        mock_request.state._cached_body = b'{"test": "data"}'
        context = extract_request_context(mock_request)

        assert context["body_preview"] == '{"test": "data"}'

    def test_extract_request_context_with_large_body(self, mock_request):
        """Test request context extraction with large body."""
        large_body = b'{"test": "' + b'x' * 300 + b'"}'
        mock_request.state._cached_body = large_body
        context = extract_request_context(mock_request)

        assert len(context["body_preview"]) <= 203  # 200 chars + "..."
        assert context["body_preview"].endswith("...")

    def test_extract_request_context_with_invalid_body(self, mock_request):
        """Test request context extraction with invalid body."""
        mock_request.state._cached_body = b'\x80invalid'
        context = extract_request_context(mock_request)

        assert "Unable to decode body" in context["body_preview"]


class TestExceptionContextExtraction:
    """Tests for exception context extraction."""

    def test_extract_exception_context_basic(self):
        """Test basic exception context extraction."""
        try:
            raise ValueError("Test error")
        except ValueError as e:
            context = extract_exception_context(e)

        assert context["exception_type"] == "ValueError"
        assert context["exception_message"] == "Test error"
        assert "file_path" in context
        assert "line_number" in context
        assert "function_name" in context
        assert "module_name" in context

    def test_extract_exception_context_without_traceback(self):
        """Test exception context extraction without traceback."""
        exc = ValueError("Test error")
        context = extract_exception_context(exc)

        assert context["exception_type"] == "ValueError"
        assert context["exception_message"] == "Test error"


class TestCacheRequestBodyMiddleware:
    """Tests for CacheRequestBodyMiddleware."""

    @pytest.mark.asyncio
    async def test_middleware_caches_body(self, mock_app):
        """Test that middleware caches request body."""
        app = mock_app
        app.add_middleware(CacheRequestBodyMiddleware)
        client = TestClient(app)

        response = client.post("/test", json={"test": "data"})
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_handles_empty_body(self, mock_app):
        """Test middleware handling of empty body."""
        app = mock_app
        app.add_middleware(CacheRequestBodyMiddleware)
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_handles_invalid_body(self, mock_app):
        """Test middleware handling of invalid body."""
        app = mock_app
        app.add_middleware(CacheRequestBodyMiddleware)
        client = TestClient(app)

        response = client.post("/test", content=b'\x80invalid')
        assert response.status_code == 200


class TestAuditLoggingHandler:
    """Tests for audit logging handler."""

    @pytest.mark.asyncio
    async def test_handle_audit_logging_success(self, mock_request):
        """Test successful audit logging."""
        with patch('apps.user_service.app.dependencies.exception_middleware.maybe_log_audit_on_error') as mock_log:
            await _handle_audit_logging(mock_request, "Test error", 500)
            mock_log.assert_called_once_with(mock_request, "Test error", status_code=500)

    @pytest.mark.asyncio
    async def test_handle_audit_logging_with_value_error(self, mock_request):
        """Test audit logging with ValueError."""
        with patch('apps.user_service.app.dependencies.exception_middleware.maybe_log_audit_on_error') as mock_log:
            mock_log.side_effect = ValueError("Test error")
            await _handle_audit_logging(mock_request, "Test error", 500)
            mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_audit_logging_with_io_error(self, mock_request):
        """Test audit logging with IOError."""
        with patch('apps.user_service.app.dependencies.exception_middleware.maybe_log_audit_on_error') as mock_log:
            mock_log.side_effect = IOError("Test error")
            await _handle_audit_logging(mock_request, "Test error", 500)
            mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_audit_logging_with_runtime_error(self, mock_request):
        """Test audit logging with RuntimeError."""
        with patch('apps.user_service.app.dependencies.exception_middleware.maybe_log_audit_on_error') as mock_log:
            mock_log.side_effect = RuntimeError("Test error")
            await _handle_audit_logging(mock_request, "Test error", 500)
            mock_log.assert_called_once()


class TestUnifiedExceptionHandler:
    """Tests for unified exception handler."""

    @pytest.mark.asyncio
    async def test_handle_http_exception(self, mock_request):
        """Test handling of HTTPException."""
        exc = HTTPException(status_code=400, detail="Test error")
        response = await unified_exception_handler(mock_request, exc)

        assert response.status_code == 400
        assert response.body == b'{"detail":"Test error"}'

    @pytest.mark.asyncio
    async def test_handle_validation_error(self, mock_request):
        """Test handling of RequestValidationError."""
        exc = RequestValidationError(errors=[{"loc": ["test"], "msg": "Test error"}])
        response = await unified_exception_handler(mock_request, exc)

        assert response.status_code == 422
        assert b"Validation error" in response.body

    @pytest.mark.asyncio
    async def test_handle_unexpected_error(self, mock_request):
        """Test handling of unexpected error."""
        exc = ValueError("Test error")
        response = await unified_exception_handler(mock_request, exc)

        assert response.status_code == 500
        assert b"Internal server error" in response.body

    @pytest.mark.asyncio
    async def test_handle_error_with_audit_metadata(self, mock_request):
        """Test error handling with audit metadata."""
        mock_request.scope = {"route": MagicMock()}
        mock_request.scope["route"].endpoint = MagicMock()
        mock_request.scope["route"].endpoint.__audit_api_call_params__ = {
            "action_type": "test",
            "table_name": "test_table"
        }

        exc = ValueError("Test error")
        response = await unified_exception_handler(mock_request, exc)

        assert response.status_code == 500
        assert hasattr(mock_request.state, "audit_metadata")
