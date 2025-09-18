"""
Standardized Exception Handling for Supabase Database Operations

This module provides a centralized exception handling system for all Supabase
database operations in the user service. It standardizes error handling,
logging, and provides utilities for common database operations.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Features:
- Standardized exception handling decorator
- Context manager for database operations
- Helper functions for common operations
- Centralized logging and error reporting
- Type-safe error handling
"""

import os
import sys
import json
import functools
from typing import Any, Callable, Dict, List, Optional, TypeVar
from contextlib import asynccontextmanager

from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException

from libs.shared_db.supabase_db.db import get_supabase_admin_client
from apps.user_service.app.dependencies.logger import get_logger

# Initialize logger
logger = get_logger("exception_handling")

# Type variables for generic functions
T = TypeVar('T')
F = TypeVar('F', bound=Callable[..., Any])


class DatabaseOperationError(Exception):
    """Base exception for database operation errors.

    This is the parent class for all database-related exceptions.
    It provides a common base for error handling and categorization.
    """

    def __init__(self, message: str, operation: str = None, context: dict = None):
        super().__init__(message)
        self.operation = operation
        self.context = context or {}

    def to_dict(self) -> dict:
        """Convert exception to dictionary for logging."""
        return {
            "error_type": self.__class__.__name__,
            "message": str(self),
            "operation": self.operation,
            "context": self.context
        }


class SupabaseAPIError(DatabaseOperationError):
    """Exception raised for Supabase API errors.

    This exception is raised when Supabase returns an API error,
    such as authentication failures, rate limiting, or invalid requests.
    """

    def __init__(self, message: str, status_code: int = None, operation: str = None):
        super().__init__(message, operation)
        self.status_code = status_code

    def is_retryable(self) -> bool:
        """Check if this error is retryable based on status code."""
        return self.status_code in [429, 500, 502, 503, 504]


class NetworkError(DatabaseOperationError):
    """Exception raised for network-related errors.

    This exception is raised when there are network connectivity issues,
    timeouts, or HTTP errors during database operations.
    """

    def __init__(self, message: str, operation: str = None, retry_after: int = None):
        super().__init__(message, operation)
        self.retry_after = retry_after

    def is_retryable(self) -> bool:
        """Network errors are generally retryable."""
        return True


class DataValidationError(DatabaseOperationError):
    """Exception raised for data validation errors.

    This exception is raised when data doesn't meet validation requirements,
    such as missing required fields, invalid data types, or constraint violations.
    """

    def __init__(self, message: str, field: str = None, operation: str = None):
        super().__init__(message, operation)
        self.field = field


class SerializationError(DatabaseOperationError):
    """Exception raised for serialization/deserialization errors.

    This exception is raised when there are issues converting data
    to/from JSON or other serialization formats.
    """

    def __init__(self, message: str, data_type: str = None, operation: str = None):
        super().__init__(message, operation)
        self.data_type = data_type


class DatabaseConnectionError(DatabaseOperationError):
    """Exception raised for database connection errors.

    This exception is raised when there are issues establishing
    or maintaining a connection to the database.
    """

    def __init__(self, message: str, operation: str = None, retry_after: int = None):
        super().__init__(message, operation)
        self.retry_after = retry_after

    def is_retryable(self) -> bool:
        """Connection errors are generally retryable."""
        return True


# ============================================================================
# EXCEPTION HANDLING DECORATOR
# ============================================================================

def handle_database_errors(
    operation_name: str,
    log_level: str = "error",
    reraise: bool = True,
    return_default: Any = None,
    custom_messages: Optional[Dict[str, str]] = None
):
    """
    Decorator for standardized database operation exception handling with custom error messages.

    Args:
        operation_name: Name of the operation for logging purposes
        log_level: Logging level ('error', 'warning', 'info', 'debug')
        reraise: Whether to reraise the exception after logging
        return_default: Default value to return if reraise is False
        custom_messages: Optional dict with custom error messages for specific exception types
                        Keys: 'api_error',
                              'network_error',
                              'serialization_error',
                              'validation_error',
                              'unexpected_error'
                        Values: Custom error message templates
                              (use {e} for exception, {operation} for operation name)

    Usage:
        @handle_database_errors(
            "create_user",
            custom_messages={
                'api_error': 'Supabase API error creating user: {e}',
                'network_error': 'Network error creating user: {e}',
                'validation_error': 'Data validation error creating user: {e}'
            }
        )
        async def create_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
            # Your database operation here
            pass
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)

            except APIError as e:
                if custom_messages and 'api_error' in custom_messages:
                    error_msg = custom_messages['api_error'].format(
                        e=e, operation=operation_name)
                else:
                    error_msg = f"Supabase API error in {operation_name}: {e}"
                _log_error(error_msg, e, log_level, operation_name)
                if reraise:
                    # Extract status code if available
                    status_code = getattr(e, 'status_code', None)
                    raise SupabaseAPIError(
                        error_msg,
                        status_code=status_code,
                        operation=operation_name) from e
                return return_default

            except (HTTPError, RequestError, TimeoutException) as e:
                if custom_messages and 'network_error' in custom_messages:
                    error_msg = custom_messages['network_error'].format(
                        e=e, operation=operation_name)
                else:
                    error_msg = f"Network error in {operation_name}: {e}"
                _log_error(error_msg, e, log_level, operation_name)
                if reraise:
                    # Extract retry_after if available
                    retry_after = getattr(e, 'retry_after', None)
                    raise NetworkError(
                        error_msg,
                        operation=operation_name,
                        retry_after=retry_after) from e
                return return_default

            except (json.JSONDecodeError, UnicodeError) as e:
                if custom_messages and 'serialization_error' in custom_messages:
                    error_msg = custom_messages['serialization_error'].format(
                        e=e, operation=operation_name)
                else:
                    error_msg = f"Serialization error in {operation_name}: {e}"
                _log_error(error_msg, e, log_level, operation_name)
                if reraise:
                    # Determine data type from error context
                    data_type = "JSON" if isinstance(e, json.JSONDecodeError) else "Unicode"
                    raise SerializationError(
                        error_msg,
                        data_type=data_type,
                        operation=operation_name) from e
                return return_default

            except (KeyError, TypeError, ValueError) as e:
                if custom_messages and 'validation_error' in custom_messages:
                    error_msg = custom_messages['validation_error'].format(
                        e=e, operation=operation_name)
                else:
                    error_msg = f"Data validation error in {operation_name}: {e}"
                _log_error(error_msg, e, log_level, operation_name)
                if reraise:
                    # Try to extract field name from error context
                    field = None
                    if isinstance(e, KeyError):
                        field = str(e).strip("'\"")
                    raise DataValidationError(
                        error_msg,
                        field=field,
                        operation=operation_name) from e
                return return_default

            except Exception as e:
                if custom_messages and 'unexpected_error' in custom_messages:
                    error_msg = custom_messages['unexpected_error'].format(
                        e=e, operation=operation_name)
                else:
                    error_msg = f"Unexpected error in {operation_name}: {e}"
                _log_error(error_msg, e, log_level, operation_name)
                if reraise:
                    context = {"exception_type": type(e).__name__, "args": str(e.args)}
                    raise DatabaseOperationError(
                        error_msg,
                        operation=operation_name,
                        context=context) from e
                return return_default

        return wrapper
    return decorator


def _log_error(
    message: str,
    exception: Exception,
    log_level: str,
    operation_name: str = None
) -> None:
    """Log error with appropriate level and exception info."""
    log_func = getattr(logger, log_level, logger.error)
    log_func(message, exc_info=True)

    # Add detailed exception logging using the enhanced log_exception function
    log_exception(
        operation=operation_name or getattr(exception, 'operation', 'unknown'),
        context=f"Database operation failed: {message}"
    )


def create_error_messages(operation_name: str, action: str = None) -> Dict[str, str]:
    """
    Helper function to create standardized error messages for common operations.

    Args:
        operation_name: Name of the operation (e.g., "create_user", "get_role")
        action: Optional action description (e.g., "creating", "getting", "updating")

    Returns:
        Dictionary with custom error messages for the decorator

    Usage:
        @handle_database_errors(
            "create_user",
            custom_messages=create_error_messages("create_user", "creating")
        )
        async def create_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
            # Your database operation here
            pass
    """
    if action is None:
        # Try to infer action from operation name
        if operation_name.startswith(('create_', 'add_', 'insert_')):
            action = "creating"
        elif operation_name.startswith(('get_', 'fetch_', 'retrieve_')):
            action = "getting"
        elif operation_name.startswith(('update_', 'modify_', 'edit_')):
            action = "updating"
        elif operation_name.startswith(('delete_', 'remove_', 'destroy_')):
            action = "deleting"
        elif operation_name.startswith(('check_', 'validate_', 'verify_')):
            action = "checking"
        else:
            action = "processing"

    return {
        'api_error': f'Supabase API error {action} {operation_name}: {{e}}',
        'network_error': f'Network error {action} {operation_name}: {{e}}',
        'serialization_error': f'Serialization error {action} {operation_name}: {{e}}',
        'validation_error': f'Data validation error {action} {operation_name}: {{e}}',
        'unexpected_error': f'Unexpected error {action} {operation_name}: {{e}}'
    }


# ============================================================================
# CONTEXT MANAGER FOR DATABASE OPERATIONS
# ============================================================================

@asynccontextmanager
async def database_operation(operation_name: str):
    """
    Context manager for database operations with automatic error handling.

    Args:
        operation_name: Name of the operation for logging purposes

    Usage:
        async with database_operation("create_user") as supabase:
            result = await supabase.table("users").insert(data).execute()
    """
    supabase = None
    try:
        supabase = await get_supabase_admin_client()
        logger.debug("Starting database operation: %s", operation_name)
        yield supabase
        logger.debug("Completed database operation: %s", operation_name)

    except APIError as e:
        error_msg = f"Supabase API error in {operation_name}: {e}"
        logger.error(error_msg, exc_info=True)
        log_exception(operation=operation_name, context=f"Supabase API error: {error_msg}")
        status_code = getattr(e, 'status_code', None)
        raise SupabaseAPIError(error_msg, status_code=status_code, operation=operation_name) from e

    except (HTTPError, RequestError, TimeoutException) as e:
        error_msg = f"Network error in {operation_name}: {e}"
        logger.error(error_msg, exc_info=True)
        log_exception(operation=operation_name, context=f"Network error: {error_msg}")
        retry_after = getattr(e, 'retry_after', None)
        raise NetworkError(error_msg, operation=operation_name, retry_after=retry_after) from e

    except Exception as e:
        error_msg = f"Unexpected error in {operation_name}: {e}"
        logger.error(error_msg, exc_info=True)
        log_exception(operation=operation_name, context=f"Unexpected error: {error_msg}")
        context = {"exception_type": type(e).__name__, "args": str(e.args)}
        raise DatabaseOperationError(error_msg, operation=operation_name, context=context) from e


# ============================================================================
# HELPER FUNCTIONS FOR COMMON OPERATIONS
# ============================================================================

@handle_database_errors("execute_query")
async def execute_safe_query(
    table_name: str,
    operation: str,
    data: Optional[Dict[str, Any]] = None,
    organization_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a safe database query with standardized error handling.

    Args:
        table_name: Name of the table to query
        operation: Operation type ('insert', 'select', 'update', 'delete')
        data: Data to insert/update
        organization_id: Organization ID for filtering

    Returns:
        Query result data
    """
    supabase = await get_supabase_admin_client()

    if operation == "insert":
        if not data:
            raise DataValidationError("Data is required for insert operation")
        result = await supabase.table(table_name).insert(data).execute()
        return {"data": result.data, "count": len(result.data) if result.data else 0}

    elif operation == "select":
        query = supabase.table(table_name)
        query = query.select("*")

        if organization_id:
            query = query.eq("organization_id", organization_id)

        result = await query.execute()
        return {"data": result.data, "count": len(result.data) if result.data else 0}

    elif operation == "update":
        if not data:
            raise DataValidationError("Data is required for update operation")


        query = supabase.table(table_name).update(data)

        result = await query.execute()
        return {"data": result.data, "count": len(result.data) if result.data else 0}

    elif operation == "delete":
        query = supabase.table(table_name).delete()
        result = await query.execute()
        return {"data": result.data, "count": len(result.data) if result.data else 0}

    else:
        raise DataValidationError(f"Unsupported operation: {operation}")


@handle_database_errors("bulk_insert")
async def bulk_insert_safe(
    table_name: str,
    records: List[Dict[str, Any]],
    organization_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Safely insert multiple records with error handling.

    Args:
        table_name: Name of the table
        records: List of records to insert
        organization_id: Organization ID to add to each record

    Returns:
        Insert result data
    """
    if not records:
        return {"data": [], "count": 0}

    # Add organization_id to each record if provided
    if organization_id:
        for record in records:
            record["organization_id"] = organization_id

    supabase = await get_supabase_admin_client()
    result = await supabase.table(table_name).insert(records).execute()

    return {"data": result.data, "count": len(result.data) if result.data else 0}


@handle_database_errors("count_records")
async def count_records_safe(
    table_name: str,
    filters: Optional[Dict[str, Any]] = None,
    organization_id: Optional[str] = None
) -> int:
    """
    Safely count records with error handling.

    Args:
        table_name: Name of the table
        filters: Filters to apply
        organization_id: Organization ID for filtering

    Returns:
        Number of records
    """
    supabase = await get_supabase_admin_client()

    query = supabase.table(table_name).select("id", count="exact")

    if filters:
        for key, value in filters.items():
            if isinstance(value, list):
                query = query.in_(key, value)
            else:
                query = query.eq(key, value)

    if organization_id:
        query = query.eq("organization_id", organization_id)

    result = await query.execute()
    return result.count if result.count is not None else 0


@handle_database_errors("check_exists")
async def check_record_exists_safe(
    table_name: str,
    filters: Dict[str, Any],
    organization_id: Optional[str] = None
) -> bool:
    """
    Safely check if a record exists with error handling.

    Args:
        table_name: Name of the table
        filters: Filters to apply
        organization_id: Organization ID for filtering

    Returns:
        True if record exists, False otherwise
    """
    supabase = await get_supabase_admin_client()

    query = supabase.table(table_name).select("id")

    for key, value in filters.items():
        if isinstance(value, list):
            query = query.in_(key, value)
        else:
            query = query.eq(key, value)

    if organization_id:
        query = query.eq("organization_id", organization_id)

    result = await query.limit(1).execute()
    return len(result.data) > 0 if result.data else False


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def log_exception(operation: str = None, context: str = None):
    """
    Log detailed exception information with operation context.

    Args:
        operation: Name of the operation that failed
        context: Additional context about what was being done
    """
    exc_type, exc_value, exc_tb = sys.exc_info()

    if exc_tb is None:
        logger.error("No exception traceback available")
        return

    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    line_no = exc_tb.tb_lineno

    # Build detailed error message
    error_parts = [f"Error: {exc_type.__name__}", f"File: {fname}", f"Line: {line_no}"]

    if operation:
        error_parts.insert(0, f"Operation: {operation}")

    if context:
        error_parts.append(f"Context: {context}")

    # Add exception value if available
    if exc_value:
        error_parts.append(f"Details: {str(exc_value)}")

    logger.error(" | ".join(error_parts))


def log_exception_with_retry(operation: str, context: str = None, retry_count: int = 0):
    """
    Enhanced logging for operations that might be retried.

    Args:
        operation: Name of the operation that failed
        context: Additional context about what was being done
        retry_count: Number of retries attempted
    """
    exc_type, exc_value, exc_tb = sys.exc_info()

    if exc_tb is None:
        logger.error("No exception traceback available")
        return

    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    line_no = exc_tb.tb_lineno

    # Build detailed error message with retry info
    error_parts = [
        f"Operation: {operation}",
        f"Error: {exc_type.__name__}",
        f"File: {fname}",
        f"Line: {line_no}"
    ]

    if context:
        error_parts.append(f"Context: {context}")

    if retry_count > 0:
        error_parts.append(f"Retry: {retry_count}")

    # Add exception value if available
    if exc_value:
        error_parts.append(f"Details: {str(exc_value)}")

    logger.error(" | ".join(error_parts))


def log_database_operation_error(operation: str, table: str = None, record_id: str = None):
    """
    Specialized logging for database operation errors.

    Args:
        operation: Name of the database operation
        table: Database table being accessed
        record_id: ID of the record being operated on
    """
    exc_type, exc_value, exc_tb = sys.exc_info()

    if exc_tb is None:
        logger.error("No exception traceback available")
        return

    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    line_no = exc_tb.tb_lineno

    # Build database-specific error message
    error_parts = [
        f"DB Operation: {operation}",
        f"Error: {exc_type.__name__}",
        f"File: {fname}",
        f"Line: {line_no}"
    ]

    if table:
        error_parts.append(f"Table: {table}")

    if record_id:
        error_parts.append(f"Record ID: {record_id}")

    # Add exception value if available
    if exc_value:
        error_parts.append(f"Details: {str(exc_value)}")

    logger.error(" | ".join(error_parts))


def format_error_message(
    operation: str,
    error: Exception,
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Format a standardized error message.

    Args:
        operation: Name of the operation
        error: The exception that occurred
        context: Additional context information

    Returns:
        Formatted error message
    """
    base_msg = f"Error in {operation}: {str(error)}"

    if context:
        context_str = ", ".join([f"{k}={v}" for k, v in context.items()])
        base_msg += f" (Context: {context_str})"

    return base_msg


def is_retryable_error(error: Exception) -> bool:
    """
    Check if an error is retryable.

    Args:
        error: The exception to check

    Returns:
        True if the error is retryable, False otherwise
    """
    retryable_errors = (
        TimeoutException,
        RequestError,
        HTTPError
    )

    return isinstance(error, retryable_errors)


def get_error_type(error: Exception) -> str:
    """
    Get the type of error for categorization.

    Args:
        error: The exception to categorize

    Returns:
        Error type string
    """
    match error:
        case APIError():
            return "supabase_api"
        case HTTPError() | RequestError() | TimeoutException():
            return "network"
        case json.JSONDecodeError() | UnicodeError():
            return "serialization"
        case KeyError() | TypeError() | ValueError():
            return "validation"
        case _:
            return "unknown"


# ============================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# ============================================================================

async def safe_supabase_operation(
    operation: Callable,
    operation_name: str,
    *args,
    **kwargs
) -> Any:
    """
    Legacy function for backward compatibility.

    Args:
        operation: The Supabase operation to execute
        operation_name: Name of the operation for logging
        *args: Positional arguments for the operation
        **kwargs: Keyword arguments for the operation

    Returns:
        Operation result
    """
    try:
        return await operation(*args, **kwargs)

    except APIError as e:
        logger.error("Supabase API error in %s: %s", operation_name, str(e), exc_info=True)
        log_exception(operation=operation_name, context=f"Supabase API error: {str(e)}")
        status_code = getattr(e, 'status_code', None)
        raise SupabaseAPIError(str(e), status_code=status_code, operation=operation_name) from e

    except (HTTPError, RequestError, TimeoutException) as e:
        logger.error("Network error in %s: %s", operation_name, str(e), exc_info=True)
        log_exception(operation=operation_name, context=f"Network error: {str(e)}")
        retry_after = getattr(e, 'retry_after', None)
        raise NetworkError(str(e), operation=operation_name, retry_after=retry_after) from e

    except (json.JSONDecodeError, UnicodeError) as e:
        logger.error("Serialization error in %s: %s", operation_name, str(e), exc_info=True)
        log_exception(operation=operation_name, context=f"Serialization error: {str(e)}")
        data_type = "JSON" if isinstance(e, json.JSONDecodeError) else "Unicode"
        raise SerializationError(str(e), data_type=data_type, operation=operation_name) from e

    except (KeyError, TypeError, ValueError) as e:
        logger.error("Data validation error in %s: %s", operation_name, str(e), exc_info=True)
        log_exception(operation=operation_name, context=f"Data validation error: {str(e)}")
        field = None
        if isinstance(e, KeyError):
            field = str(e).strip("'\"")
        raise DataValidationError(str(e), field=field, operation=operation_name) from e


# ============================================================================
# CONFIGURATION
# ============================================================================

class ExceptionHandlingConfig:
    """Configuration for exception handling behavior."""

    DEFAULT_LOG_LEVEL = "error"
    DEFAULT_RERAISE = True
    DEFAULT_RETURN_VALUE = None
    ENABLE_DETAILED_LOGGING = True
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 1


# Export commonly used functions and classes
__all__ = [
    # Exceptions
    "DatabaseOperationError",
    "SupabaseAPIError",
    "NetworkError",
    "DataValidationError",
    "SerializationError",
    "DatabaseConnectionError",

    # Decorators
    "handle_database_errors",

    # Context managers
    "database_operation",

    # Helper functions
    "execute_safe_query",
    "bulk_insert_safe",
    "count_records_safe",
    "check_record_exists_safe",

    # Utility functions
    "format_error_message",
    "is_retryable_error",
    "get_error_type",
    "safe_supabase_operation",

    # Configuration
    "ExceptionHandlingConfig"
]
