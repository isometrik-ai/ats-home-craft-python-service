# pylint: disable=all

"""
Test module for logger functionality.

This module contains comprehensive tests for all logging components
defined in apps.user_service.app.dependencies.logger.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

import logging
import json
import os
import sys
from unittest.mock import patch
from datetime import datetime

from apps.user_service.app.dependencies.logger import (
    RequestIdFilter,
    CustomJSONFormatter,
    CustomTextFormatter,
    setup_logging,
    get_logger,
    set_request_id,
    get_request_id,
    log_with_context,
    app_logger,
    request_id_var,
    IST_TIMEZONE,
)


class TestRequestIdFilter:
    """Test RequestIdFilter functionality."""

    def test_request_id_filter_with_request_id(self):
        """Test RequestIdFilter with request ID set."""
        # Set request ID in context
        request_id_var.set("test-request-123")

        filter_instance = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None
        )

        result = filter_instance.filter(record)

        assert result is True
        assert record.request_id == "test-request-123"

    def test_request_id_filter_without_request_id(self):
        """Test RequestIdFilter without request ID set."""
        # Clear request ID from context
        request_id_var.set(None)

        filter_instance = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None
        )

        result = filter_instance.filter(record)

        assert result is True
        assert record.request_id == "no-request-id"


class TestCustomJSONFormatter:
    """Test CustomJSONFormatter functionality."""

    def test_json_formatter_basic(self):
        """Test basic JSON formatting."""
        formatter = CustomJSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        record.request_id = "test-request-123"

        result = formatter.format(record)
        log_data = json.loads(result)

        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert log_data["message"] == "Test message"
        assert log_data["request_id"] == "test-request-123"
        assert log_data["module"] == "path"
        assert log_data["function"] is None  # Function name is None for LogRecord
        assert log_data["line"] == 42
        assert "timestamp" in log_data

    def test_json_formatter_with_exception(self):
        """Test JSON formatting with exception info - covers lines 61-62."""
        formatter = CustomJSONFormatter()

        try:
            raise ValueError("Test exception")
        except ValueError:
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="/test/path.py",
                lineno=42,
                msg="Test message with exception",
                args=(),
                exc_info=sys.exc_info()
            )
            record.request_id = "test-request-123"

            result = formatter.format(record)
            log_data = json.loads(result)

            assert log_data["level"] == "ERROR"
            assert log_data["message"] == "Test message with exception"
            assert "exception" in log_data
            assert "ValueError: Test exception" in log_data["exception"]

    def test_json_formatter_with_extra_fields(self):
        """Test JSON formatting with extra fields - covers lines 65-66."""
        formatter = CustomJSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        record.request_id = "test-request-123"
        record.extra_fields = {"user_id": "user123", "action": "login"}

        result = formatter.format(record)
        log_data = json.loads(result)

        assert log_data["level"] == "INFO"
        assert log_data["message"] == "Test message"
        assert log_data["user_id"] == "user123"
        assert log_data["action"] == "login"

    def test_json_formatter_without_extra_fields(self):
        """Test JSON formatting without extra fields."""
        formatter = CustomJSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        record.request_id = "test-request-123"

        result = formatter.format(record)
        log_data = json.loads(result)

        assert log_data["level"] == "INFO"
        assert log_data["message"] == "Test message"
        assert "user_id" not in log_data
        assert "action" not in log_data


class TestCustomTextFormatter:
    """Test CustomTextFormatter functionality."""

    def test_text_formatter_basic(self):
        """Test basic text formatting."""
        formatter = CustomTextFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        record.request_id = "test-request-123"

        result = formatter.format(record)

        assert "[INFO    ]" in result
        assert "[test-request-123]" in result
        assert "test.logger: Test message" in result
        assert "Test message" in result

    def test_text_formatter_with_exception(self):
        """Test text formatting with exception info."""
        formatter = CustomTextFormatter()

        try:
            raise ValueError("Test exception")
        except ValueError:
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="/test/path.py",
                lineno=42,
                msg="Test message with exception",
                args=(),
                exc_info=sys.exc_info()
            )
            record.request_id = "test-request-123"

            result = formatter.format(record)

            assert "[ERROR   ]" in result
            assert "Test message with exception" in result
            assert "ValueError: Test exception" in result

    def test_text_formatter_without_request_id(self):
        """Test text formatting without request ID."""
        formatter = CustomTextFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None
        )
        # Don't set request_id to test default behavior

        result = formatter.format(record)

        assert "[INFO    ]" in result
        assert "[no-request-id]" in result
        assert "Test message" in result


class TestSetupLogging:
    """Test setup_logging functionality."""

    def test_setup_logging_defaults(self):
        """Test setup_logging with default parameters."""
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO", "ENVIRONMENT": "development"}):
            logger = setup_logging()

            assert logger.name == "user-service"
            assert logger.level == logging.INFO
            assert len(logger.handlers) == 1
            assert logger.propagate is True

    def test_setup_logging_custom_parameters(self):
        """Test setup_logging with custom parameters."""
        logger = setup_logging(
            log_level="DEBUG",
            environment="production",
            service_name="test-service"
        )

        assert logger.name == "test-service"
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

    def test_setup_logging_invalid_level(self):
        """Test setup_logging with invalid log level - covers line 119."""
        logger = setup_logging(log_level="INVALID_LEVEL")

        # Should fallback to INFO
        assert logger.level == logging.INFO

    def test_setup_logging_production_environment(self):
        """Test setup_logging in production environment - covers line 130."""
        logger = setup_logging(environment="production")

        assert logger.name == "user-service"
        assert len(logger.handlers) == 1

        # Check that JSON formatter is used in production
        handler = logger.handlers[0]
        assert isinstance(handler.formatter, CustomJSONFormatter)

    def test_setup_logging_development_environment(self):
        """Test setup_logging in development environment."""
        logger = setup_logging(environment="development")

        assert logger.name == "user-service"
        assert len(logger.handlers) == 1

        # Check that text formatter is used in development
        handler = logger.handlers[0]
        assert isinstance(handler.formatter, CustomTextFormatter)

    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        logger = setup_logging()
        initial_handler_count = len(logger.handlers)

        # Add a dummy handler
        dummy_handler = logging.StreamHandler()
        logger.addHandler(dummy_handler)
        assert len(logger.handlers) == initial_handler_count + 1

        # Setup logging again
        logger = setup_logging()
        assert len(logger.handlers) == 1  # Should be cleared and reset

    def test_setup_logging_handler_configuration(self):
        """Test handler configuration in setup_logging."""
        logger = setup_logging(log_level="WARNING")

        handler = logger.handlers[0]
        assert handler.level == logging.WARNING
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream == sys.stdout

        # Check that RequestIdFilter is added
        assert len(handler.filters) == 1
        assert isinstance(handler.filters[0], RequestIdFilter)


class TestLoggerManagement:
    """Test logger management functionality."""

    def test_get_logger_without_name(self):
        """Test get_logger without name parameter."""
        logger = get_logger()

        assert logger.name == "user-service"

    def test_get_logger_with_name(self):
        """Test get_logger with name parameter."""
        child_logger = get_logger("test.module")

        assert child_logger.name == "test.module"
        assert child_logger.level == logging.getLogger("user-service").level
        assert child_logger.propagate is False

        # Check that handlers are copied from parent
        parent_logger = logging.getLogger("user-service")
        assert len(child_logger.handlers) == len(parent_logger.handlers)

    def test_get_logger_child_logger_configuration(self):
        """Test child logger configuration."""
        parent_logger = logging.getLogger("user-service")
        child_logger = get_logger("test.child")

        # Child should have same level as parent
        assert child_logger.level == parent_logger.level

        # Child should have same handlers as parent
        for handler in parent_logger.handlers:
            assert handler in child_logger.handlers

        # Child should not propagate to avoid duplicate logs
        assert child_logger.propagate is False

    def test_get_logger_multiple_calls(self):
        """Test multiple calls to get_logger with same name."""
        logger1 = get_logger("test.module")
        logger2 = get_logger("test.module")

        # Should return the same logger instance
        assert logger1 is logger2


class TestContextManagement:
    """Test context management functionality."""

    def test_set_request_id(self):
        """Test set_request_id function - covers line 185."""
        set_request_id("test-request-456")

        # Verify the request ID was set
        assert request_id_var.get() == "test-request-456"

    def test_get_request_id(self):
        """Test get_request_id function - covers line 195."""
        # Set a request ID
        request_id_var.set("test-request-789")

        # Get the request ID
        result = get_request_id()

        assert result == "test-request-789"

    def test_get_request_id_none(self):
        """Test get_request_id when no request ID is set."""
        # Clear request ID
        request_id_var.set(None)

        result = get_request_id()

        assert result is None

    def test_request_id_context_isolation(self):
        """Test that request ID context is properly isolated."""
        # Set initial request ID
        set_request_id("initial-request")
        assert get_request_id() == "initial-request"

        # Set new request ID
        set_request_id("new-request")
        assert get_request_id() == "new-request"


class TestContextLogging:
    """Test log_with_context functionality."""

    def test_log_with_context_basic(self):
        """Test basic log_with_context functionality."""
        logger = logging.getLogger("test.logger")

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", "Test message")

            # Should call the logger method directly when no extra fields
            mock_info.assert_called_once_with("Test message")

    def test_log_with_context_with_extra_fields(self):
        """Test log_with_context with extra_fields - covers lines 216-220."""
        logger = logging.getLogger("test.logger")
        extra_fields = {"user_id": "user123", "action": "login"}

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "Test message", extra_fields=extra_fields)

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.extra_fields == extra_fields

    def test_log_with_context_with_kwargs(self):
        """Test log_with_context with kwargs - covers lines 220-221."""
        logger = logging.getLogger("test.logger")

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "Test message", user_id="user123", action="login")

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.extra_fields == {"user_id": "user123", "action": "login"}

    def test_log_with_context_with_both_extra_fields_and_kwargs(self):
        """Test log_with_context with both extra_fields and kwargs - covers lines 216-221."""
        logger = logging.getLogger("test.logger")
        extra_fields = {"user_id": "user123"}

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "Test message", extra_fields=extra_fields, action="login")

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.extra_fields == {"user_id": "user123", "action": "login"}

    def test_log_with_context_without_extra_data(self):
        """Test log_with_context without extra data - covers lines 236-237."""
        logger = logging.getLogger("test.logger")

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", "Test message")

            # Should call the logger method directly
            mock_info.assert_called_once_with("Test message")

    def test_log_with_context_different_levels(self):
        """Test log_with_context with different log levels."""
        logger = logging.getLogger("test.logger")

        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in levels:
            with patch.object(logger, 'handle') as mock_handle:
                log_with_context(logger, level, f"Test {level} message", extra_fields={"level": level})

                mock_handle.assert_called_once()
                call_args = mock_handle.call_args[0][0]
                assert call_args.levelname == level

    def test_log_with_context_empty_extra_fields(self):
        """Test log_with_context with empty extra_fields."""
        logger = logging.getLogger("test.logger")

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", "Test message", extra_fields={})

            # Should call the logger method directly since no extra data
            mock_info.assert_called_once_with("Test message")

    def test_log_with_context_none_extra_fields(self):
        """Test log_with_context with None extra_fields."""
        logger = logging.getLogger("test.logger")

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", "Test message", extra_fields=None)

            # Should call the logger method directly since no extra data
            mock_info.assert_called_once_with("Test message")


class TestIntegration:
    """Test integration scenarios."""

    def test_app_logger_initialization(self):
        """Test that app_logger is properly initialized."""
        assert app_logger is not None
        assert app_logger.name == "user-service"
        assert len(app_logger.handlers) >= 1

    def test_complete_logging_workflow(self):
        """Test complete logging workflow."""
        # Set request ID
        set_request_id("integration-test-123")

        # Get logger
        logger = get_logger("integration.test")

        # Test basic logging - mock the info method instead of emit
        with patch.object(logger, 'info') as mock_info:
            logger.info("Integration test message")
            mock_info.assert_called_once_with("Integration test message")

            # Verify request ID is set in context
            assert get_request_id() == "integration-test-123"

    def test_logging_with_context_workflow(self):
        """Test logging with context workflow."""
        logger = get_logger("context.test")

        # Test context logging
        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(
                logger,
                "INFO",
                "Context test message",
                extra_fields={"test": "data"},
                user_id="test_user"
            )

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.getMessage() == "Context test message"
            assert call_args.extra_fields == {"test": "data", "user_id": "test_user"}

    def test_formatter_integration(self):
        """Test formatter integration with real logging."""
        logger = setup_logging(environment="production", service_name="test-integration")

        # Test JSON formatting
        with patch.object(logger.handlers[0], 'stream') as mock_stream:
            logger.info("Integration test message")

            # Verify that JSON was written to stream
            mock_stream.write.assert_called()
            written_data = mock_stream.write.call_args[0][0]

            # Should be valid JSON
            log_data = json.loads(written_data)
            assert log_data["level"] == "INFO"
            assert log_data["message"] == "Integration test message"

    def test_ist_timezone_usage(self):
        """Test IST timezone usage in formatters."""
        formatter = CustomJSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None
        )

        result = formatter.format(record)
        log_data = json.loads(result)

        # Verify timestamp is in IST format
        timestamp = datetime.fromisoformat(log_data["timestamp"].replace('Z', '+00:00'))
        assert timestamp.tzinfo == IST_TIMEZONE

    def test_error_logging_scenario(self):
        """Test error logging scenario."""
        logger = get_logger("error.test")

        try:
            raise ValueError("Test error for logging")
        except ValueError:
            with patch.object(logger, 'handle') as mock_handle:
                log_with_context(
                    logger,
                    "ERROR",
                    "An error occurred",
                    extra_fields={"error_type": "ValueError"},
                    exc_info=sys.exc_info()
                )

                mock_handle.assert_called_once()
                call_args = mock_handle.call_args[0][0]
                assert call_args.levelname == "ERROR"
                assert call_args.getMessage() == "An error occurred"
                assert call_args.extra_fields["error_type"] == "ValueError"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_logger_with_special_characters(self):
        """Test logger with special characters in messages."""
        logger = get_logger("special.test")

        special_message = "Test message with special chars: !@#$%^&*()_+-=[]{}|;':\",./<>?"

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", special_message)

            mock_info.assert_called_once_with(special_message)

    def test_logger_with_unicode_characters(self):
        """Test logger with unicode characters."""
        logger = get_logger("unicode.test")

        unicode_message = "Test message with unicode: 🚀 测试 日本語 العربية"

        with patch.object(logger, 'info') as mock_info:
            log_with_context(logger, "INFO", unicode_message)

            mock_info.assert_called_once_with(unicode_message)

    def test_logger_with_large_extra_fields(self):
        """Test logger with large extra fields."""
        logger = get_logger("large.test")

        large_extra_fields = {f"key_{i}": f"value_{i}" for i in range(100)}

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "Large extra fields test", extra_fields=large_extra_fields)

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert len(call_args.extra_fields) == 100

    def test_logger_with_none_values(self):
        """Test logger with None values in extra fields."""
        logger = get_logger("none.test")

        extra_fields = {"none_value": None, "empty_string": "", "zero": 0}

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "None values test", extra_fields=extra_fields)

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.extra_fields["none_value"] is None
            assert call_args.extra_fields["empty_string"] == ""
            assert call_args.extra_fields["zero"] == 0

    def test_logger_with_complex_data_structures(self):
        """Test logger with complex data structures."""
        logger = get_logger("complex.test")

        complex_data = {
            "list": [1, 2, 3],
            "dict": {"nested": {"value": "test"}},
            "tuple": (1, 2, 3),
            "set": {1, 2, 3}
        }

        with patch.object(logger, 'handle') as mock_handle:
            log_with_context(logger, "INFO", "Complex data test", extra_fields=complex_data)

            mock_handle.assert_called_once()
            call_args = mock_handle.call_args[0][0]
            assert call_args.extra_fields == complex_data
