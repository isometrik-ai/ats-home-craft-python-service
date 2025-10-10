# pylint: disable=all

"""Comprehensive tests for audit decorator module.

This module tests all functions in apps/user_service/app/dependencies/audit_logs/audit_decorator.py
to achieve high coverage for the audit decorator system.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
from fastapi import Request, HTTPException
from fastapi.testclient import TestClient

from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
    _should_log_audit,
    _log_audit_event,
    _collect_audit_state,
    _build_new_values,
    get_changed_fields,
    maybe_log_audit_on_error,
    _extract_request_body,
    parse_body_by_content_type,
    _parse_json_body,
    _parse_form_data,
)
from apps.user_service.app.dependencies.audit_logs.audit_logger import AuditEventData


@pytest.fixture
def mock_request():
    """Create a mock request object for testing."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {
        "user-agent": "test-agent",
        "content-type": "application/json"
    }
    request.url = MagicMock()
    request.url.path = "/test/path"
    request.method = "POST"
    request.query_params = {"param1": "value1"}
    request.form = AsyncMock(return_value={})
    return request


@pytest.fixture
def mock_audit_event_data():
    """Create mock audit event data."""
    return AuditEventData(
        user_context={"user_id": "123", "organization_id": "org123", "user_email": "test@example.com"},
        action_type="CREATE",
        data_classification="general",
        table_name="test_table",
        record_id="record123",
        old_values=None,
        new_values={"data": "test"},
        changed_fields=None,
        compliance_tags=[],
        risk_level="low",
        description="Test audit event",
        status_code=200,
        category="test"
    )


class TestAuditApiCallDecorator:
    """Test audit_api_call decorator."""

    @pytest.mark.asyncio
    async def test_decorator_metadata_attachment(self):
        """Test that decorator properly attaches metadata to function."""
        @audit_api_call(
            action_type="CREATE",
            table_name="test_table",
            data_classification="confidential",
            compliance_tags=["gdpr"],
            category="test"
        )
        async def test_function(request: Request):
            return {"status": "success"}

        # Verify metadata is attached
        assert hasattr(test_function, "audit_metadata")
        metadata = test_function.audit_metadata
        assert metadata["action_type"] == "CREATE"
        assert metadata["table_name"] == "test_table"
        assert metadata["data_classification"] == "confidential"
        assert metadata["compliance_tags"] == ["gdpr"]
        assert metadata["category"] == "test"

    @pytest.mark.asyncio
    async def test_decorator_with_missing_request(self):
        """Test decorator behavior when request argument is missing."""
        @audit_api_call(action_type="CREATE", table_name="test")
        async def test_function():
            return {"status": "success"}

        with pytest.raises(ValueError, match="Request must be passed as a keyword argument"):
            await test_function()

    @pytest.mark.asyncio
    async def test_decorator_successful_execution(self, mock_request):
        """Test decorator with successful execution."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state.audit_description = "Test description"
        mock_request.state._cached_body = b'{"test": "data"}'

        @audit_api_call(action_type="CREATE", table_name="test_table")
        async def test_function(request: Request):
            return {"status": "success"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._should_log_audit', return_value=True):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event', new_callable=AsyncMock) as mock_log:
                # Call the function directly with the request parameter
                result = await test_function(request=mock_request)

                assert result == {"status": "success"}
                mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_decorator_skips_logging_when_should_not_log(self, mock_request):
        """Test decorator skips logging when _should_log_audit returns False."""
        @audit_api_call(action_type="CREATE", table_name="test_table")
        async def test_function(request: Request):
            return {"status": "success"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._should_log_audit', return_value=False):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event', new_callable=AsyncMock) as mock_log:
                result = await test_function(request=mock_request)

                assert result == {"status": "success"}
                mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_decorator_with_default_parameters(self):
        """Test decorator with default parameters."""
        @audit_api_call()
        async def test_function(request: Request):
            return {"status": "success"}

        # Verify default metadata
        metadata = test_function.audit_metadata
        assert metadata["action_type"] is None
        assert metadata["table_name"] is None
        assert metadata["data_classification"] == "general"
        assert metadata["compliance_tags"] is None
        assert metadata["category"] is None


class TestShouldLogAudit:
    """Test _should_log_audit function."""

    def test_should_log_audit_with_complete_context(self, mock_request):
        """Test _should_log_audit with complete user context."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        result = _should_log_audit(mock_request)
        assert result is True

    def test_should_log_audit_with_missing_user_id(self, mock_request):
        """Test _should_log_audit with missing user_id."""
        mock_request.state.audit_user_context = {
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        result = _should_log_audit(mock_request)
        assert result is False

    def test_should_log_audit_with_missing_organization_id(self, mock_request):
        """Test _should_log_audit with missing organization_id."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "user_email": "test@example.com"
        }

        result = _should_log_audit(mock_request)
        assert result is False

    def test_should_log_audit_with_missing_user_email(self, mock_request):
        """Test _should_log_audit with missing user_email."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123"
        }

        result = _should_log_audit(mock_request)
        assert result is False

    def test_should_log_audit_with_unknown_email(self, mock_request):
        """Test _should_log_audit with unknown user_email."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "unknown"
        }

        result = _should_log_audit(mock_request)
        assert result is False

    def test_should_log_audit_with_empty_context(self, mock_request):
        """Test _should_log_audit with empty user context."""
        mock_request.state.audit_user_context = {}

        result = _should_log_audit(mock_request)
        assert result is False

    def test_should_log_audit_with_no_context_attribute(self, mock_request):
        """Test _should_log_audit when request.state has no audit_user_context."""
        delattr(mock_request.state, 'audit_user_context')

        result = _should_log_audit(mock_request)
        assert result is False


class TestCollectAuditState:
    """Test _collect_audit_state function."""

    def test_collect_audit_state_with_all_attributes(self, mock_request):
        """Test _collect_audit_state with all attributes present."""
        mock_request.state.audit_table = "test_table"
        mock_request.state.audit_requested_id = "req123"
        mock_request.state.raw_audit_old_data = {"old": "data"}
        mock_request.state.raw_audit_new_data = {"new": "data"}
        mock_request.state.audit_description = "Test description"
        mock_request.state.audit_risk_level = "high"

        result = _collect_audit_state(mock_request, "fallback_table")

        assert result["table"] == "test_table"
        assert result["requested_id"] == "req123"
        assert result["raw_old"] == {"old": "data"}
        assert result["raw_new"] == {"new": "data"}
        assert result["description"] == "Test description"
        assert result["risk_level"] == "high"

    def test_collect_audit_state_with_fallback_table(self, mock_request):
        """Test _collect_audit_state with fallback table name."""
        # Configure mock to not have the attributes that should use defaults
        # This simulates the case where the attributes don't exist
        delattr(mock_request.state, 'audit_table')
        delattr(mock_request.state, 'audit_requested_id')
        delattr(mock_request.state, 'raw_audit_old_data')
        delattr(mock_request.state, 'raw_audit_new_data')
        delattr(mock_request.state, 'audit_description')
        delattr(mock_request.state, 'audit_risk_level')

        result = _collect_audit_state(mock_request, "fallback_table")

        assert result["table"] == "fallback_table"
        assert result["requested_id"] == ""
        assert result["raw_old"] is None
        assert result["raw_new"] is None
        assert result["description"] == ""
        assert result["risk_level"] == "low"

    def test_collect_audit_state_with_no_fallback_table(self, mock_request):
        """Test _collect_audit_state with no fallback table name."""
        # Configure mock to not have the attributes that should use defaults
        # This simulates the case where the attributes don't exist
        delattr(mock_request.state, 'audit_table')
        delattr(mock_request.state, 'audit_requested_id')
        delattr(mock_request.state, 'raw_audit_old_data')
        delattr(mock_request.state, 'raw_audit_new_data')
        delattr(mock_request.state, 'audit_description')
        delattr(mock_request.state, 'audit_risk_level')

        result = _collect_audit_state(mock_request, None)

        assert result["table"] == ""
        assert result["requested_id"] == ""
        assert result["raw_old"] is None
        assert result["raw_new"] is None
        assert result["description"] == ""
        assert result["risk_level"] == "low"


class TestBuildNewValues:
    """Test _build_new_values function."""

    def test_build_new_values_complete(self, mock_request):
        """Test _build_new_values with complete data."""
        audit_state = {
            "requested_id": "req123",
            "raw_new": {"new": "data"}
        }
        request_body = {"body": "data"}

        result = _build_new_values(mock_request, request_body, audit_state, 201, "test_table")

        assert result["meta"]["path"] == "/test/path"
        assert result["meta"]["method"] == "POST"
        assert result["meta"]["status_code"] == 201
        assert result["meta"]["table"] == "test_table"
        assert result["meta"]["requested_id"] == "req123"
        assert result["meta"]["user_agent"] == "test-agent"
        assert result["meta"]["ip"] == "127.0.0.1"
        assert result["meta"]["query_params"] == {"param1": "value1"}
        assert result["meta"]["request_body"] == request_body
        assert result["meta"]["content_type"] == "application/json"
        assert result["data"] == {"new": "data"}

    def test_build_new_values_with_none_table(self, mock_request):
        """Test _build_new_values with None table name."""
        audit_state = {
            "requested_id": "req123",
            "raw_new": {"new": "data"}
        }
        request_body = {"body": "data"}

        result = _build_new_values(mock_request, request_body, audit_state, 200, None)

        assert result["meta"]["table"] is None

    def test_build_new_values_with_empty_query_params(self, mock_request):
        """Test _build_new_values with empty query params."""
        mock_request.query_params = {}
        audit_state = {
            "requested_id": "req123",
            "raw_new": {"new": "data"}
        }
        request_body = {"body": "data"}

        result = _build_new_values(mock_request, request_body, audit_state, 200, "test_table")

        assert result["meta"]["query_params"] == {}


class TestGetChangedFields:
    """Test get_changed_fields function."""

    def test_get_changed_fields_basic(self):
        """Test basic field comparison."""
        old_data = {"name": "John", "age": 30}
        new_data = {"name": "Jane", "age": 30}

        result = get_changed_fields(old_data, new_data)

        assert "name" in result
        assert "age" not in result

    def test_get_changed_fields_nested(self):
        """Test nested field comparison."""
        old_data = {
            "user": {"name": "John", "age": 30},
            "settings": {"theme": "dark"}
        }
        new_data = {
            "user": {"name": "Jane", "age": 30},
            "settings": {"theme": "light"}
        }

        result = get_changed_fields(old_data, new_data)

        assert "user.name" in result
        assert "user.age" not in result
        assert "settings.theme" in result

    def test_get_changed_fields_deeply_nested(self):
        """Test deeply nested field comparison."""
        old_data = {
            "level1": {
                "level2": {
                    "level3": {"value": "old"}
                }
            }
        }
        new_data = {
            "level1": {
                "level2": {
                    "level3": {"value": "new"}
                }
            }
        }

        result = get_changed_fields(old_data, new_data)

        assert "level1.level2.level3.value" in result

    def test_get_changed_fields_no_common_keys(self):
        """Test with no common keys."""
        old_data = {"key1": "value1"}
        new_data = {"key2": "value2"}

        result = get_changed_fields(old_data, new_data)

        assert result == []

    def test_get_changed_fields_identical_data(self):
        """Test with identical data."""
        data = {"name": "John", "age": 30}

        result = get_changed_fields(data, data)

        assert result == []

    def test_get_changed_fields_with_prefix(self):
        """Test with custom prefix."""
        old_data = {"name": "John"}
        new_data = {"name": "Jane"}

        result = get_changed_fields(old_data, new_data, prefix="user")

        assert "user.name" in result


class TestMaybeLogAuditOnError:
    """Test maybe_log_audit_on_error function."""

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_success(self, mock_request):
        """Test successful audit logging on error."""
        mock_request.state._audit_metadata = {
            "table_name": "test_table",
            "data_classification": "confidential",
            "compliance_tags": ["gdpr"],
            "category": "test"
        }
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state._cached_body = b'{"test": "data"}'

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', return_value={"test": "data"}):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.log_audit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_missing_user_context(self, mock_request):
        """Test audit logging with missing user context."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
            await maybe_log_audit_on_error(mock_request, "Test error", 500)

            mock_logger.log_audit_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_unknown_email(self, mock_request):
        """Test audit logging with unknown email."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "unknown"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
            await maybe_log_audit_on_error(mock_request, "Test error", 500)

            mock_logger.log_audit_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_attribute_error(self, mock_request):
        """Test audit logging with AttributeError."""
        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
            await maybe_log_audit_on_error(mock_request, "Test error", 500)

            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_json_decode_error(self, mock_request):
        """Test audit logging with JSONDecodeError."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0)):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_unicode_error(self, mock_request):
        """Test audit logging with UnicodeError."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', side_effect=UnicodeError("Unicode error")):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_value_error(self, mock_request):
        """Test audit logging with ValueError."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', side_effect=ValueError("Value error")):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_os_error(self, mock_request):
        """Test audit logging with OSError."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', side_effect=OSError("OS error")):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_maybe_log_audit_on_error_runtime_error(self, mock_request):
        """Test audit logging with RuntimeError."""
        mock_request.state._audit_metadata = {}
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', side_effect=RuntimeError("Runtime error")):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                await maybe_log_audit_on_error(mock_request, "Test error", 500)

                mock_logger.warning.assert_called()


class TestExtractRequestBody:
    """Test _extract_request_body function."""

    @pytest.mark.asyncio
    async def test_extract_request_body_success(self, mock_request):
        """Test successful request body extraction."""
        mock_request.state._cached_body = b'{"test": "data"}'
        mock_request.headers = {"content-type": "application/json"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.parse_body_by_content_type', return_value={"test": "data"}):
            result = await _extract_request_body(mock_request)

            assert result == {"test": "data"}

    @pytest.mark.asyncio
    async def test_extract_request_body_no_body(self, mock_request):
        """Test request body extraction with no body."""
        mock_request.state._cached_body = None
        mock_request.headers = {"content-type": "application/json"}

        result = await _extract_request_body(mock_request)

        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_request_body_no_content_type(self, mock_request):
        """Test request body extraction with no content type."""
        mock_request.state._cached_body = b'{"test": "data"}'
        mock_request.headers = {}

        result = await _extract_request_body(mock_request)

        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_request_body_unicode_error(self, mock_request):
        """Test request body extraction with UnicodeError."""
        mock_request.state._cached_body = b'\x80invalid'
        mock_request.headers = {"content-type": "application/json"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
            result = await _extract_request_body(mock_request)

            assert "_error" in result
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_extract_request_body_json_decode_error(self, mock_request):
        """Test request body extraction with JSONDecodeError."""
        mock_request.state._cached_body = b'invalid json'
        mock_request.headers = {"content-type": "application/json"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.parse_body_by_content_type', side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0)):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                result = await _extract_request_body(mock_request)

                assert "_error" in result
                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_extract_request_body_attribute_error(self, mock_request):
        """Test request body extraction with AttributeError."""
        # Create a new mock request without the _cached_body attribute
        mock_request_no_body = MagicMock(spec=Request)
        mock_request_no_body.state = MagicMock()
        mock_request_no_body.headers = {"content-type": "application/json"}
        # Don't set _cached_body attribute to trigger AttributeError

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
            result = await _extract_request_body(mock_request_no_body)

            assert "_error" in result
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_extract_request_body_value_error(self, mock_request):
        """Test request body extraction with ValueError."""
        mock_request.state._cached_body = b'{"test": "data"}'
        mock_request.headers = {"content-type": "application/json"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.parse_body_by_content_type', side_effect=ValueError("Value error")):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.logger') as mock_logger:
                result = await _extract_request_body(mock_request)

                assert "_error" in result
                mock_logger.warning.assert_called()


class TestParseBodyByContentType:
    """Test parse_body_by_content_type function."""

    @pytest.mark.asyncio
    async def test_parse_body_by_content_type_json(self, mock_request):
        """Test parsing JSON content type."""
        body_bytes = b'{"test": "data"}'
        content_type = "application/json"

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._parse_json_body', return_value={"test": "data"}):
            result = await parse_body_by_content_type(mock_request, body_bytes, content_type)

            assert result == {"test": "data"}

    @pytest.mark.asyncio
    async def test_parse_body_by_content_type_form_urlencoded(self, mock_request):
        """Test parsing form-urlencoded content type."""
        body_bytes = b'field1=value1&field2=value2'
        content_type = "application/x-www-form-urlencoded"

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._parse_form_data', return_value={"field1": "value1"}):
            result = await parse_body_by_content_type(mock_request, body_bytes, content_type)

            assert result == {"field1": "value1"}

    @pytest.mark.asyncio
    async def test_parse_body_by_content_type_multipart(self, mock_request):
        """Test parsing multipart content type."""
        body_bytes = b'multipart data'
        content_type = "multipart/form-data"

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._parse_form_data', return_value={"field1": "value1"}):
            result = await parse_body_by_content_type(mock_request, body_bytes, content_type)

            assert result == {"field1": "value1"}

    @pytest.mark.asyncio
    async def test_parse_body_by_content_type_unknown(self, mock_request):
        """Test parsing unknown content type."""
        body_bytes = b'some data'
        content_type = "text/plain"

        result = await parse_body_by_content_type(mock_request, body_bytes, content_type)

        assert result == {}


class TestParseJsonBody:
    """Test _parse_json_body function."""

    def test_parse_json_body_success(self):
        """Test successful JSON parsing."""
        body_bytes = b'{"test": "data"}'

        result = _parse_json_body(body_bytes)

        assert result == {"test": "data"}

    def test_parse_json_body_invalid_json(self):
        """Test JSON parsing with invalid JSON."""
        body_bytes = b'invalid json'

        result = _parse_json_body(body_bytes)

        assert result == "invalid json"


class TestParseFormData:
    """Test _parse_form_data function."""

    @pytest.mark.asyncio
    async def test_parse_form_data_multipart_with_file(self, mock_request):
        """Test parsing multipart form data with file."""
        mock_file = MagicMock()
        mock_file.filename = "test.txt"
        mock_request.form.return_value = {"file": mock_file, "field": "value"}

        result = await _parse_form_data(mock_request, "multipart/form-data")

        assert result["file"] == "<file: test.txt>"
        assert result["field"] == "value"

    @pytest.mark.asyncio
    async def test_parse_form_data_multipart_without_file(self, mock_request):
        """Test parsing multipart form data without file."""
        mock_request.form.return_value = {"field": "value"}

        result = await _parse_form_data(mock_request, "multipart/form-data")

        assert result["field"] == "value"

    @pytest.mark.asyncio
    async def test_parse_form_data_urlencoded(self, mock_request):
        """Test parsing URL-encoded form data."""
        mock_request.form.return_value = {"field": "value"}

        result = await _parse_form_data(mock_request, "application/x-www-form-urlencoded")

        assert result["field"] == "value"


class TestLogAuditEvent:
    """Test _log_audit_event function."""

    @pytest.mark.asyncio
    async def test_log_audit_event_success(self, mock_request):
        """Test successful audit event logging."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state.audit_description = "Test description"
        mock_request.state.raw_audit_old_data = {"old": "data"}
        mock_request.state.raw_audit_new_data = {"new": "data"}
        mock_request.state._cached_body = b'{"test": "data"}'

        result = MagicMock()
        result.status_code = 201

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', return_value={"test": "data"}):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
                mock_logger.log_audit_event = AsyncMock()
                await _log_audit_event(
                    mock_request, result, "CREATE", "confidential",
                    "test_table", ["gdpr"], "test"
                )

                mock_logger.log_audit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_audit_event_missing_description(self, mock_request):
        """Test audit event logging with missing description."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state.audit_description = ""

        result = MagicMock()
        result.status_code = 201

        with pytest.raises(ValueError, match="Missing required audit description"):
            await _log_audit_event(
                mock_request, result, "CREATE", "confidential",
                "test_table", ["gdpr"], "test"
            )

    @pytest.mark.asyncio
    async def test_log_audit_event_with_old_data(self, mock_request):
        """Test audit event logging with old data for change tracking."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state.audit_description = "Test description"
        mock_request.state.raw_audit_old_data = {"name": "John", "age": 30}
        mock_request.state.raw_audit_new_data = {"name": "Jane", "age": 30}
        mock_request.state._cached_body = b'{"test": "data"}'

        result = MagicMock()
        result.status_code = 200

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator._extract_request_body', return_value={"test": "data"}):
            with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
                mock_logger.log_audit_event = AsyncMock()
                await _log_audit_event(
                    mock_request, result, "UPDATE", "confidential",
                    "test_table", ["gdpr"], "test"
                )

                # Verify that old values and changed fields were set
                assert hasattr(mock_request.state, 'audit_old_values')
                assert hasattr(mock_request.state, 'audit_changed_fields')
                mock_logger.log_audit_event.assert_called_once()


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple functions."""

    @pytest.mark.asyncio
    async def test_full_audit_flow_success(self, mock_request):
        """Test complete audit flow from decorator to logging."""
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state.audit_description = "Test description"
        mock_request.state._cached_body = b'{"test": "data"}'

        @audit_api_call(action_type="CREATE", table_name="test_table")
        async def test_function(request: Request):
            return {"status": "success"}

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
            mock_logger.log_audit_event = AsyncMock()
            result = await test_function(request=mock_request)

            assert result == {"status": "success"}
            mock_logger.log_audit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_flow_with_error_handling(self, mock_request):
        """Test audit flow with error handling."""
        mock_request.state._audit_metadata = {
            "table_name": "test_table",
            "data_classification": "confidential"
        }
        mock_request.state.audit_user_context = {
            "user_id": "123",
            "organization_id": "org123",
            "user_email": "test@example.com"
        }
        mock_request.state._cached_body = b'{"test": "data"}'

        with patch('apps.user_service.app.dependencies.audit_logs.audit_decorator.audit_logger') as mock_logger:
            await maybe_log_audit_on_error(mock_request, "Test error", 500)

            mock_logger.log_audit_event.assert_called_once()

    def test_changed_fields_integration(self):
        """Test changed fields detection with complex data structures."""
        old_data = {
            "user": {
                "profile": {
                    "name": "John",
                    "email": "john@example.com"
                },
                "settings": {
                    "theme": "dark",
                    "notifications": True
                }
            },
            "metadata": {
                "created_at": "2023-01-01",
                "updated_at": "2023-01-01"
            }
        }

        new_data = {
            "user": {
                "profile": {
                    "name": "Jane",
                    "email": "jane@example.com"
                },
                "settings": {
                    "theme": "light",
                    "notifications": True
                }
            },
            "metadata": {
                "created_at": "2023-01-01",
                "updated_at": "2023-01-02"
            }
        }

        result = get_changed_fields(old_data, new_data)

        assert "user.profile.name" in result
        assert "user.profile.email" in result
        assert "user.settings.theme" in result
        assert "user.settings.notifications" not in result
        assert "metadata.updated_at" in result
        assert "metadata.created_at" not in result
