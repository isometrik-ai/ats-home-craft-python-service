"""
User Service Database Operations Package

This package contains all centralized database operations for the user service.
All SQL queries should be organized by domain in their respective modules.

Modules:
- user_operations: User-related database operations
- role_operations: Role-related database operations
- organisation_operations: Organisation-related database operations
- permission_operations: Permission-related database operations
- session_operations: Session-related database operations
- audit_operations: Audit-related database operations
- exception_handling: Standardized exception handling utilities
"""

# Import exception handling utilities
from .exception_handling import (
    # Exceptions
    DatabaseOperationError,
    SupabaseAPIError,
    NetworkError,
    DataValidationError,
    SerializationError,
    DatabaseConnectionError,

    # Decorators
    handle_database_errors,

    # Context managers
    database_operation,

    # Helper functions
    execute_safe_query,
    bulk_insert_safe,
    count_records_safe,
    check_record_exists_safe,
    create_error_messages,

    # Utility functions
    format_error_message,
    is_retryable_error,
    get_error_type,
    safe_supabase_operation,

    # Configuration
    ExceptionHandlingConfig
)

# Import all operation modules for easy access
from .user_operations import *
from .role_operations import *
from .organisation_operations import *
from .permission_operations import *
from .session_operations import *
from .audit_operations import *
from .verification_operations import *
