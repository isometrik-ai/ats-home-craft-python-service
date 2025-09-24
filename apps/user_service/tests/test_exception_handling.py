"""Comprehensive tests for exception handling module.

This module tests all functions in libs/shared_db/postgres_db/user_service_operations/exception_handling.py
to achieve high coverage for the database exception handling system.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import json

from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException

from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    # Exception classes
    DatabaseOperationError,
    SupabaseAPIError,
    NetworkError,
    DataValidationError,
    SerializationError,
    DatabaseConnectionError,

    # Core functions
    handle_database_errors,
    create_error_messages,

    # Async utilities
    database_operation,
    execute_safe_query,
    bulk_insert_safe,
    count_records_safe,
    check_record_exists_safe,
    safe_supabase_operation,

    # Logging functions
    log_exception,
    log_exception_with_retry,
    log_database_operation_error,
    format_error_message,
    is_retryable_error,
    get_error_type,

    # Configuration
    ExceptionHandlingConfig,
)


class TestExceptionClasses:
    """Test all exception classes and their methods."""

    def test_database_operation_error(self):
        """Test DatabaseOperationError base class."""
        error = DatabaseOperationError("Test error", "test_operation", {"key": "value"})

        assert str(error) == "Test error"
        assert error.operation == "test_operation"
        assert error.context == {"key": "value"}

        error_dict = error.to_dict()
        assert error_dict["error_type"] == "DatabaseOperationError"
        assert error_dict["message"] == "Test error"
        assert error_dict["operation"] == "test_operation"
        assert error_dict["context"] == {"key": "value"}

    def test_supabase_api_error(self):
        """Test SupabaseAPIError class."""
        error = SupabaseAPIError("API error", 500, "test_operation")

        assert str(error) == "API error"
        assert error.status_code == 500
        assert error.operation == "test_operation"

        # Test retryable status codes
        assert error.is_retryable() is True  # 500 is retryable

        error_400 = SupabaseAPIError("Bad request", 400, "test_operation")
        assert error_400.is_retryable() is False  # 400 is not retryable

    def test_network_error(self):
        """Test NetworkError class."""
        error = NetworkError("Network error", "test_operation", 30)

        assert str(error) == "Network error"
        assert error.operation == "test_operation"
        assert error.retry_after == 30
        assert error.is_retryable() is True  # Network errors are always retryable

    def test_data_validation_error(self):
        """Test DataValidationError class."""
        error = DataValidationError("Validation error", field="email", operation="test_operation")

        assert str(error) == "Validation error"
        assert error.field == "email"
        assert error.operation == "test_operation"

    def test_serialization_error(self):
        """Test SerializationError class."""
        error = SerializationError("Serialization error", data_type="JSON", operation="test_operation")

        assert str(error) == "Serialization error"
        assert error.data_type == "JSON"
        assert error.operation == "test_operation"

    def test_database_connection_error(self):
        """Test DatabaseConnectionError class."""
        error = DatabaseConnectionError("Connection error", operation="test_operation", retry_after=30)

        assert str(error) == "Connection error"
        assert error.operation == "test_operation"
        assert error.retry_after == 30
        assert error.is_retryable() is True


class TestCreateErrorMessages:
    """Test create_error_messages function."""

    def test_create_error_messages_basic(self):
        """Test basic error message creation."""
        messages = create_error_messages("test_operation")

        assert "test_operation" in messages["api_error"]
        assert "test_operation" in messages["network_error"]
        assert "test_operation" in messages["serialization_error"]
        assert "test_operation" in messages["validation_error"]
        assert "test_operation" in messages["unexpected_error"]

    def test_create_error_messages_with_action(self):
        """Test error message creation with action."""
        messages = create_error_messages("test_operation", "creating")

        assert "creating" in messages["api_error"]
        assert "creating" in messages["network_error"]
        assert "creating" in messages["serialization_error"]
        assert "creating" in messages["validation_error"]
        assert "creating" in messages["unexpected_error"]


class TestHandleDatabaseErrorsDecorator:
    """Test handle_database_errors decorator."""

    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """Test decorator with successful operation."""
        @handle_database_errors("test_operation")
        async def test_function():
            return "success"

        result = await test_function()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages(self):
        """Test decorator with custom error messages."""
        custom_messages = create_error_messages("test_operation", "testing")

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            return "success"

        result = await test_function()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_with_supabase_api_error(self):
        """Test decorator handling Supabase API error."""
        @handle_database_errors("test_operation")
        async def test_function():
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            raise error

        with pytest.raises(SupabaseAPIError) as exc_info:
            await test_function()

        assert exc_info.value.status_code == 500
        assert "API Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_network_error(self):
        """Test decorator handling network error."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise HTTPError("Network Error")

        with pytest.raises(NetworkError) as exc_info:
            await test_function()

        assert "Network Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_timeout_error(self):
        """Test decorator handling timeout error."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise TimeoutException("Timeout Error")

        with pytest.raises(NetworkError) as exc_info:
            await test_function()

        assert "Timeout Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_json_decode_error(self):
        """Test decorator handling JSON decode error."""
        import json

        @handle_database_errors("test_operation")
        async def test_function():
            raise json.JSONDecodeError("JSON Error", "doc", 0)

        with pytest.raises(SerializationError) as exc_info:
            await test_function()

        assert "JSON Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_value_error(self):
        """Test decorator handling ValueError."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise ValueError("Validation Error")

        with pytest.raises(DataValidationError) as exc_info:
            await test_function()

        assert "Validation Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_generic_exception(self):
        """Test decorator handling generic exception."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise Exception("Generic Error")

        with pytest.raises(DatabaseOperationError) as exc_info:
            await test_function()

        assert "Generic Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages_api_error(self):
        """Test decorator with custom messages for API error."""
        custom_messages = {
            'api_error': 'Custom API error: {e} in {operation}',
            'network_error': 'Custom network error: {e} in {operation}',
            'serialization_error': 'Custom serialization error: {e} in {operation}',
            'validation_error': 'Custom validation error: {e} in {operation}',
            'unexpected_error': 'Custom unexpected error: {e} in {operation}'
        }

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            raise error

        with pytest.raises(SupabaseAPIError) as exc_info:
            await test_function()

        assert "Custom API error" in str(exc_info.value)
        assert "test_operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages_network_error(self):
        """Test decorator with custom messages for network error."""
        custom_messages = {
            'network_error': 'Custom network error: {e} in {operation}'
        }

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            raise HTTPError("Network Error")

        with pytest.raises(NetworkError) as exc_info:
            await test_function()

        assert "Custom network error" in str(exc_info.value)
        assert "test_operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages_serialization_error(self):
        """Test decorator with custom messages for serialization error."""
        custom_messages = {
            'serialization_error': 'Custom serialization error: {e} in {operation}'
        }

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            raise json.JSONDecodeError("JSON Error", "doc", 0)

        with pytest.raises(SerializationError) as exc_info:
            await test_function()

        assert "Custom serialization error" in str(exc_info.value)
        assert "test_operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages_validation_error(self):
        """Test decorator with custom messages for validation error."""
        custom_messages = {
            'validation_error': 'Custom validation error: {e} in {operation}'
        }

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            raise ValueError("Validation Error")

        with pytest.raises(DataValidationError) as exc_info:
            await test_function()

        assert "Custom validation error" in str(exc_info.value)
        assert "test_operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_messages_unexpected_error(self):
        """Test decorator with custom messages for unexpected error."""
        custom_messages = {
            'unexpected_error': 'Custom unexpected error: {e} in {operation}'
        }

        @handle_database_errors("test_operation", custom_messages=custom_messages)
        async def test_function():
            raise Exception("Unexpected Error")

        with pytest.raises(DatabaseOperationError) as exc_info:
            await test_function()

        assert "Custom unexpected error" in str(exc_info.value)
        assert "test_operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_return_default_api_error(self):
        """Test decorator returning default value for API error."""
        @handle_database_errors("test_operation", reraise=False, return_default="default_value")
        async def test_function():
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            raise error

        result = await test_function()
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_decorator_with_return_default_network_error(self):
        """Test decorator returning default value for network error."""
        @handle_database_errors("test_operation", reraise=False, return_default="default_value")
        async def test_function():
            raise HTTPError("Network Error")

        result = await test_function()
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_decorator_with_return_default_serialization_error(self):
        """Test decorator returning default value for serialization error."""
        @handle_database_errors("test_operation", reraise=False, return_default="default_value")
        async def test_function():
            raise json.JSONDecodeError("JSON Error", "doc", 0)

        result = await test_function()
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_decorator_with_return_default_validation_error(self):
        """Test decorator returning default value for validation error."""
        @handle_database_errors("test_operation", reraise=False, return_default="default_value")
        async def test_function():
            raise ValueError("Validation Error")

        result = await test_function()
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_decorator_with_return_default_unexpected_error(self):
        """Test decorator returning default value for unexpected error."""
        @handle_database_errors("test_operation", reraise=False, return_default="default_value")
        async def test_function():
            raise Exception("Unexpected Error")

        result = await test_function()
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_decorator_with_unicode_error(self):
        """Test decorator handling UnicodeError."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise UnicodeError("Unicode Error")

        with pytest.raises(SerializationError) as exc_info:
            await test_function()

        assert "Unicode Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_key_error(self):
        """Test decorator handling KeyError."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise KeyError("Missing key")

        with pytest.raises(DataValidationError) as exc_info:
            await test_function()

        assert "Missing key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_type_error(self):
        """Test decorator handling TypeError."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise TypeError("Type Error")

        with pytest.raises(DataValidationError) as exc_info:
            await test_function()

        assert "Type Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decorator_with_request_error(self):
        """Test decorator handling RequestError."""
        @handle_database_errors("test_operation")
        async def test_function():
            raise RequestError("Request Error")

        with pytest.raises(NetworkError) as exc_info:
            await test_function()

        assert "Request Error" in str(exc_info.value)


class TestDatabaseOperationContextManager:
    """Test database_operation context manager."""

    @pytest.mark.asyncio
    async def test_database_operation_success(self):
        """Test successful database operation."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            async with database_operation("test_operation") as supabase:
                assert supabase == mock_client
                mock_get_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_operation_with_exception(self):
        """Test database operation with exception."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception):
                async with database_operation("test_operation") as supabase:
                    raise Exception("Test error")


class TestAsyncUtilityFunctions:
    """Test async utility functions."""

    @pytest.mark.asyncio
    async def test_execute_safe_query_success(self):
        """Test successful execute_safe_query."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1, "name": "test"}]
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await execute_safe_query("test_table", "select", {"id": 1})

            assert result["data"] == [{"id": 1, "name": "test"}]
            assert result["count"] == 1
            mock_supabase.table.assert_called_once_with("test_table")

    @pytest.mark.asyncio
    async def test_execute_safe_query_with_filters(self):
        """Test execute_safe_query with filters."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await execute_safe_query("test_table", "select", {"id": 1}, "org123")

            assert result["data"] == [{"id": 1}]
            assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_bulk_insert_safe_success(self):
        """Test successful bulk_insert_safe."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1}, {"id": 2}]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            records = [{"name": "test1"}, {"name": "test2"}]
            result = await bulk_insert_safe("test_table", records)

            assert result["data"] == [{"id": 1}, {"id": 2}]
            assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_bulk_insert_safe_no_data(self):
        """Test bulk_insert_safe with no data."""
        result = await bulk_insert_safe("test_table", [])

        assert result["data"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_count_records_safe_success(self):
        """Test successful count_records_safe."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.count = 5
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await count_records_safe("test_table")

            assert result == 5

    @pytest.mark.asyncio
    async def test_count_records_safe_with_filters(self):
        """Test count_records_safe with filters."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.count = 3
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await count_records_safe("test_table", {"status": "active"})

            assert result == 3

    @pytest.mark.asyncio
    async def test_check_record_exists_safe_true(self):
        """Test check_record_exists_safe returning True."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await check_record_exists_safe("test_table", {"id": 1})

            assert result is True

    @pytest.mark.asyncio
    async def test_check_record_exists_safe_false(self):
        """Test check_record_exists_safe returning False."""
        mock_supabase = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = []
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_response

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
            result = await check_record_exists_safe("test_table", {"id": 1})

            assert result is False

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_success(self):
        """Test successful safe_supabase_operation."""
        async def test_operation():
            return "success"

        result = await safe_supabase_operation(test_operation, "test_operation")
        assert result == "success"

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_api_error(self):
        """Test safe_supabase_operation with API error."""
        async def test_operation():
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            raise error

        with pytest.raises(SupabaseAPIError) as exc_info:
            await safe_supabase_operation(test_operation, "test_operation")

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_network_error(self):
        """Test safe_supabase_operation with network error."""
        async def test_operation():
            raise HTTPError("Network Error")

        with pytest.raises(NetworkError) as exc_info:
            await safe_supabase_operation(test_operation, "test_operation")


class TestLoggingFunctions:
    """Test logging and helper functions."""

    def test_log_exception(self):
        """Test log_exception function."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.logger') as mock_logger:
            with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.sys.exc_info') as mock_exc_info:
                # Create a mock traceback
                mock_tb = MagicMock()
                mock_tb.tb_frame.f_code.co_filename = "/test/path/exception_handling.py"
                mock_tb.tb_lineno = 100
                mock_exc_info.return_value = (ValueError, ValueError("test error"), mock_tb)
                log_exception("test_operation", "test_context")

                mock_logger.error.assert_called_once()
                call_args = mock_logger.error.call_args[0][0]
                assert "test_operation" in call_args
                assert "test_context" in call_args

    def test_log_exception_with_retry(self):
        """Test log_exception_with_retry function."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.logger') as mock_logger:
            with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.sys.exc_info') as mock_exc_info:
                # Create a mock traceback
                mock_tb = MagicMock()
                mock_tb.tb_frame.f_code.co_filename = "/test/path/exception_handling.py"
                mock_tb.tb_lineno = 100
                mock_exc_info.return_value = (ValueError, ValueError("test error"), mock_tb)
                log_exception_with_retry("test_operation", "test_context", 3)

                mock_logger.error.assert_called_once()
                call_args = mock_logger.error.call_args[0][0]
                assert "test_operation" in call_args
                assert "retry" in call_args.lower()

    def test_log_database_operation_error(self):
        """Test log_database_operation_error function."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.logger') as mock_logger:
            with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.sys.exc_info') as mock_exc_info:
                # Create a mock traceback
                mock_tb = MagicMock()
                mock_tb.tb_frame.f_code.co_filename = "/test/path/exception_handling.py"
                mock_tb.tb_lineno = 100
                mock_exc_info.return_value = (ValueError, ValueError("test error"), mock_tb)
                log_database_operation_error("test_operation", "test_table", "test_id")

                mock_logger.error.assert_called_once()
                call_args = mock_logger.error.call_args[0][0]
                assert "test_operation" in call_args
                assert "test_table" in call_args

    def test_format_error_message(self):
        """Test format_error_message function."""
        error = ValueError("test_error")
        context = {"key": "value"}
        message = format_error_message("test_operation", error, context)

        assert "test_operation" in message
        assert "test_error" in message
        assert "key=value" in message

    def test_is_retryable_error_true(self):
        """Test is_retryable_error returning True."""
        error = TimeoutException("Timeout Error")
        assert is_retryable_error(error) is True

        error = RequestError("Request Error")
        assert is_retryable_error(error) is True

        error = HTTPError("HTTP Error")
        assert is_retryable_error(error) is True

    def test_is_retryable_error_false(self):
        """Test is_retryable_error returning False."""
        error = SupabaseAPIError("API Error", 400)
        assert is_retryable_error(error) is False

        error = DataValidationError("Validation Error")
        assert is_retryable_error(error) is False

    def test_get_error_type(self):
        """Test get_error_type function."""
        error = APIError({"message": "Error", "code": "500"})
        assert get_error_type(error) == "supabase_api"

        error = HTTPError("Error")
        assert get_error_type(error) == "network"

        error = json.JSONDecodeError("Error", "doc", 0)
        assert get_error_type(error) == "serialization"

        error = ValueError("Error")
        assert get_error_type(error) == "validation"

        error = Exception("Error")
        assert get_error_type(error) == "unknown"


class TestExceptionHandlingConfig:
    """Test ExceptionHandlingConfig class."""

    def test_config_constants(self):
        """Test config constants."""
        assert ExceptionHandlingConfig.DEFAULT_LOG_LEVEL == "error"
        assert ExceptionHandlingConfig.DEFAULT_RERAISE is True
        assert ExceptionHandlingConfig.DEFAULT_RETURN_VALUE is None
        assert ExceptionHandlingConfig.ENABLE_DETAILED_LOGGING is True
        assert ExceptionHandlingConfig.MAX_RETRY_ATTEMPTS == 3
        assert ExceptionHandlingConfig.RETRY_DELAY_SECONDS == 1


class TestIntegrationScenarios:
    """Test integration scenarios combining multiple functions."""

    @pytest.mark.asyncio
    async def test_decorator_with_async_utility(self):
        """Test decorator with async utility function."""
        @handle_database_errors("test_operation")
        async def test_function():
            mock_supabase = AsyncMock()
            mock_response = MagicMock()
            mock_response.data = [{"id": 1}]
            mock_supabase.table.return_value.select.return_value.execute.return_value = mock_response

            with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client', return_value=mock_supabase):
                return await execute_safe_query("test_table", "select", {"id": 1})

        result = await test_function()
        assert result["data"] == [{"id": 1}]
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_decorator_with_exception_in_utility(self):
        """Test decorator with exception in utility function."""
        @handle_database_errors("test_operation")
        async def test_function():
            return await execute_safe_query("test_table", "select", {"id": 1})

        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_supabase = AsyncMock()
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            mock_supabase.table.return_value.select.return_value.execute.side_effect = error
            mock_get_client.return_value = mock_supabase

            with pytest.raises(DatabaseOperationError) as exc_info:
                await test_function()

            assert "Supabase API error in execute_query" in str(exc_info.value)

    def test_error_message_creation_integration(self):
        """Test error message creation with decorator."""
        custom_messages = create_error_messages("test_operation", "testing")

        assert "testing" in custom_messages["api_error"]
        assert "testing" in custom_messages["network_error"]
        assert "testing" in custom_messages["serialization_error"]
        assert "testing" in custom_messages["validation_error"]
        assert "testing" in custom_messages["unexpected_error"]

    def test_exception_hierarchy_integration(self):
        """Test exception hierarchy and inheritance."""
        # Test that all custom exceptions inherit from DatabaseOperationError
        assert issubclass(SupabaseAPIError, DatabaseOperationError)
        assert issubclass(NetworkError, DatabaseOperationError)
        assert issubclass(DataValidationError, DatabaseOperationError)
        assert issubclass(SerializationError, DatabaseOperationError)
        assert issubclass(DatabaseConnectionError, DatabaseOperationError)

        # Test that all custom exceptions are instances of DatabaseOperationError
        api_error = SupabaseAPIError("Error")
        assert isinstance(api_error, DatabaseOperationError)

        network_error = NetworkError("Error")
        assert isinstance(network_error, DatabaseOperationError)


class TestExecuteSafeQueryMissing:
    """Test missing coverage for execute_safe_query function."""

    @pytest.mark.asyncio
    async def test_execute_safe_query_insert_without_data(self):
        """Test insert operation without data - should raise validation error."""
        with pytest.raises(DatabaseOperationError) as exc_info:
            await execute_safe_query("test_table", "insert")
        
        assert "Data is required for insert operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_update_without_data(self):
        """Test update operation without data - should raise validation error."""
        with pytest.raises(DatabaseOperationError) as exc_info:
            await execute_safe_query("test_table", "update")
        
        assert "Data is required for update operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_update_success(self):
        """Test successful update operation."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": 1, "name": "updated"}]
            
            mock_client.table.return_value = mock_table
            mock_table.update.return_value = mock_query
            mock_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            result = await execute_safe_query(
                "test_table",
                "update",
                data={"name": "updated"}
            )

            assert result["data"] == [{"id": 1, "name": "updated"}]
            assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_execute_safe_query_delete_success(self):
        """Test successful delete operation."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": 1, "name": "deleted"}]
            
            mock_client.table.return_value = mock_table
            mock_table.delete.return_value = mock_query
            mock_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            result = await execute_safe_query("test_table", "delete")

            assert result["data"] == [{"id": 1, "name": "deleted"}]
            assert result["count"] == 1
    @pytest.mark.asyncio
    async def test_execute_safe_query_unsupported_operation(self):
        """Test unsupported operation - should raise validation error."""
        with pytest.raises(DatabaseOperationError) as exc_info:
            await execute_safe_query("test_table", "unsupported")
        
        assert "Unsupported operation: unsupported" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_insert_with_empty_data(self):
        """Test insert operation with empty data - should raise validation error."""
        with pytest.raises(DatabaseOperationError) as exc_info:
            await execute_safe_query("test_table", "insert", data=None)
        
        assert "Data is required for insert operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_update_with_empty_data(self):
        """Test update operation with empty data - should raise validation error."""
        with pytest.raises(DatabaseOperationError) as exc_info:
            await execute_safe_query("test_table", "update", data={})
        
        assert "Data is required for update operation" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_with_api_error(self):
        """Test execute_safe_query with API error."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            
            error = APIError({"message": "API Error", "code": "500"})
            error.status_code = 500
            mock_query.execute.side_effect = error
            mock_get_client.return_value = mock_client

            with pytest.raises(SupabaseAPIError) as exc_info:
                await execute_safe_query("test_table", "select")

            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_execute_safe_query_with_network_error(self):
        """Test execute_safe_query with network error."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            
            mock_query.execute.side_effect = HTTPError("Network Error")
            mock_get_client.return_value = mock_client

            with pytest.raises(NetworkError) as exc_info:
                await execute_safe_query("test_table", "select")

            assert "Network Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_safe_query_with_unexpected_error(self):
        """Test execute_safe_query with unexpected error."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            
            mock_query.execute.side_effect = Exception("Unexpected Error")
            mock_get_client.return_value = mock_client

            with pytest.raises(DatabaseOperationError) as exc_info:
                await execute_safe_query("test_table", "select")

            assert "Unexpected Error" in str(exc_info.value)

class TestMissingCoverageBatch1:
    """Test scenarios to achieve 100% coverage - Batch 1: Conditional Logic."""

    @pytest.mark.asyncio
    async def test_bulk_insert_safe_with_organization_id(self):
        """Test bulk_insert_safe with organization_id - covers line 457-458."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": 1, "name": "test", "organization_id": "org123"}]
            
            mock_client.table.return_value = mock_table
            mock_table.insert.return_value = mock_query
            mock_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            records = [{"name": "test1"}, {"name": "test2"}]
            result = await bulk_insert_safe("test_table", records, organization_id="org123")

            # Verify organization_id was added to each record
            assert mock_table.insert.call_args[0][0] == [
                {"name": "test1", "organization_id": "org123"},
                {"name": "test2", "organization_id": "org123"}
            ]
            assert result["data"] == [{"id": 1, "name": "test", "organization_id": "org123"}]
            assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_count_records_safe_with_list_filters(self):
        """Test count_records_safe with list filters - covers line 491."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.count = 5
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            mock_query.in_.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            filters = {"status": ["active", "pending"], "type": "user"}
            result = await count_records_safe("test_table", filters=filters)

            # Verify in_ was called for list filter
            mock_query.in_.assert_called_with("status", ["active", "pending"])
            # Verify eq was called for single value filter
            mock_query.eq.assert_called_with("type", "user")
            assert result == 5

    @pytest.mark.asyncio
    async def test_count_records_safe_with_organization_id(self):
        """Test count_records_safe with organization_id - covers line 496."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.count = 3
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            result = await count_records_safe("test_table", organization_id="org123")

            # Verify organization_id filter was applied
            mock_query.eq.assert_called_with("organization_id", "org123")
            assert result == 3

    @pytest.mark.asyncio
    async def test_check_record_exists_safe_with_list_filters(self):
        """Test check_record_exists_safe with list filters - covers line 526."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_limit_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = [{"id": 1}]
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            mock_query.in_.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.limit.return_value = mock_limit_query
            mock_limit_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            filters = {"status": ["active", "pending"]}
            result = await check_record_exists_safe("test_table", filters=filters)

            # Verify in_ was called for list filter
            mock_query.in_.assert_called_with("status", ["active", "pending"])
            assert result is True

    @pytest.mark.asyncio
    async def test_check_record_exists_safe_with_organization_id(self):
        """Test check_record_exists_safe with organization_id - covers line 531."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_client = AsyncMock()
            mock_table = AsyncMock()
            mock_query = AsyncMock()
            mock_limit_query = AsyncMock()
            mock_result = MagicMock()
            mock_result.data = []
            
            mock_client.table.return_value = mock_table
            mock_table.select.return_value = mock_query
            mock_query.eq.return_value = mock_query
            mock_query.limit.return_value = mock_limit_query
            mock_limit_query.execute.return_value = mock_result
            mock_get_client.return_value = mock_client

            # Fix: check_record_exists_safe requires filters parameter
            filters = {"id": "test_id"}
            result = await check_record_exists_safe("test_table", filters=filters, organization_id="org123")

            # Verify organization_id filter was applied
            mock_query.eq.assert_called_with("organization_id", "org123")
            assert result is False


class TestMissingCoverageBatch2:
    """Test scenarios to achieve 100% coverage - Batch 2: Context Manager Error Handling."""

    @pytest.mark.asyncio
    async def test_database_operation_context_manager_with_api_error(self):
        """Test database_operation context manager with API error - covers lines 352-356."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            # Fix: APIError constructor expects a dictionary, not a string
            mock_error = APIError({"message": "API Error", "code": "API_ERROR"})
            # Add status_code attribute manually
            mock_error.status_code = 500
            mock_get_client.side_effect = mock_error

            with pytest.raises(SupabaseAPIError) as exc_info:
                async with database_operation("test_operation") as supabase:
                    pass
            
            assert "Supabase API error in test_operation" in str(exc_info.value)
            assert exc_info.value.status_code == 500
    @pytest.mark.asyncio
    async def test_database_operation_context_manager_with_network_error(self):
        """Test database_operation context manager with network error - covers lines 359-363."""
        with patch('libs.shared_db.postgres_db.user_service_operations.exception_handling.get_supabase_admin_client') as mock_get_client:
            mock_error = HTTPError("Network Error")
            mock_get_client.side_effect = mock_error

            with pytest.raises(NetworkError) as exc_info:
                async with database_operation("test_operation") as supabase:
                    pass
            
            assert "Network error in test_operation" in str(exc_info.value)


class TestMissingCoverageBatch3:
    """Test scenarios to achieve 100% coverage - Batch 3: Exception Types in safe_supabase_operation."""

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_json_decode_error(self):
        """Test safe_supabase_operation with JSON decode error - covers lines 756-760."""
        async def mock_operation():
            raise json.JSONDecodeError("Invalid JSON", "doc", 0)

        with pytest.raises(SerializationError) as exc_info:
            await safe_supabase_operation(mock_operation, "test_operation")
        
        # Fix: Check the actual error message format
        assert "Invalid JSON: line 1 column 1 (char 0)" in str(exc_info.value)
        assert exc_info.value.data_type == "JSON"

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_unicode_error(self):
        """Test safe_supabase_operation with Unicode error - covers lines 756-760."""
        async def mock_operation():
            raise UnicodeError("Unicode decode error")

        with pytest.raises(SerializationError) as exc_info:
            await safe_supabase_operation(mock_operation, "test_operation")
        
        # Fix: Check the actual error message format
        assert "Unicode decode error" in str(exc_info.value)
        assert exc_info.value.data_type == "Unicode"

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_key_error(self):
        """Test safe_supabase_operation with KeyError - covers lines 762-768."""
        async def mock_operation():
            raise KeyError("missing_key")

        with pytest.raises(DataValidationError) as exc_info:
            await safe_supabase_operation(mock_operation, "test_operation")
        
        # Fix: Check the actual error message format
        assert "'missing_key'" in str(exc_info.value)
        assert exc_info.value.field == "missing_key"

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_type_error(self):
        """Test safe_supabase_operation with TypeError - covers lines 762-768."""
        async def mock_operation():
            raise TypeError("Type error")

        with pytest.raises(DataValidationError) as exc_info:
            await safe_supabase_operation(mock_operation, "test_operation")
        
        # Fix: Check the actual error message format
        assert "Type error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_safe_supabase_operation_with_value_error(self):
        """Test safe_supabase_operation with ValueError - covers lines 762-768."""
        async def mock_operation():
            raise ValueError("Value error")

        with pytest.raises(DataValidationError) as exc_info:
            await safe_supabase_operation(mock_operation, "test_operation")
        
        # Fix: Check the actual error message format
        assert "Value error" in str(exc_info.value)


class TestMissingCoverageBatch4:
    """Test scenarios to achieve 100% coverage - Batch 4: Action Inference Logic."""

    def test_create_error_messages_action_inference(self):
        """Test create_error_messages action inference - covers lines 307, 309, 311, 313, 315."""
        # Test different operation name patterns
        test_cases = [
            ("create_user", "creating"),
            ("add_record", "creating"),
            ("insert_data", "creating"),
            ("get_user", "getting"),
            ("fetch_data", "getting"),
            ("retrieve_info", "getting"),
            ("update_user", "updating"),
            ("modify_record", "updating"),
            ("edit_data", "updating"),
            ("delete_user", "deleting"),
            ("remove_record", "deleting"),
            ("destroy_data", "deleting"),
            ("check_permission", "checking"),
            ("validate_input", "checking"),
            ("verify_data", "checking"),
            ("unknown_operation", "processing")
        ]
        
        for operation_name, expected_action in test_cases:
            messages = create_error_messages(operation_name)
            assert f"{expected_action} {operation_name}" in messages['api_error']
            assert f"{expected_action} {operation_name}" in messages['network_error']
            assert f"{expected_action} {operation_name}" in messages['serialization_error']
            assert f"{expected_action} {operation_name}" in messages['validation_error']
            assert f"{expected_action} {operation_name}" in messages['unexpected_error']
