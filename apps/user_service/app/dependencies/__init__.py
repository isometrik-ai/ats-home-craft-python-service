"""
Dependencies Package

This package provides specialized utility modules for different API domains
to eliminate code duplication and standardize operations across endpoints.

Modules:
- common_utils: Shared utilities for all API endpoints
- roles_utils: Specialized utilities for role management operations
- organisation_utils: Specialized utilities for organisation management operations
"""

# Import common utilities
from .common_utils import (
    # Core data classes
    UserContext,
    PerformanceTimer,
    # User context functions
    extract_user_context,
    # Permission functions
    require_permission,
    # Validation functions
    validate_uuid_format,
    validate_uuid_list,
    validate_pagination_params,
    # Exception handling
    handle_api_exceptions,
    # Helper functions
    format_iso_datetime,
    safe_json_loads,
    build_filter_message,
    # Constants
    ROLE_TYPES,
    ORG_STATUSES,
    USER_STATUSES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    UUID_PATTERN,
)

# Import roles-specific utilities
from .roles_utils import (
    # Role validation
    validate_role_type,
    # Permission validation
    validate_permissions_exist,
    # Role database operations
    check_role_exists,
    check_role_name_unique,
    check_role_usage,
    # Query builders
    build_roles_filter_query,
    build_roles_count_query,
    # Permission management
    assign_permissions_to_role,
    remove_all_permissions_from_role,
    # Response helpers
    build_role_filter_message,
)

# Import organisation-specific utilities
from .organisation_utils import (
    # Organisation validation
    validate_organisation_status,
    validate_organisation_slug,
    validate_organisation_name_filter,
    # Organisation database operations
    check_organisation_exists,
    check_organisation_slug_unique,
    check_organisation_access,
    # Query builders
    build_organisations_filter_query,
    build_organisations_count_query,
    build_organisation_detail_query,
    # Organisation creation helpers
    get_default_permissions,
    create_default_permissions_for_organisation,
    create_super_admin_role,
    assign_all_permissions_to_role,
    # Response helpers
    build_organisation_filter_message,
    build_organisation_creation_success_message,
)

__all__ = [
    # Common utilities
    "UserContext",
    "PerformanceTimer",
    "extract_user_context",
    "require_permission",
    "validate_uuid_format",
    "validate_uuid_list",
    "validate_pagination_params",
    "handle_api_exceptions",
    "format_iso_datetime",
    "safe_json_loads",
    "build_filter_message",
    "ROLE_TYPES",
    "ORG_STATUSES",
    "USER_STATUSES",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "UUID_PATTERN",
    # Roles utilities
    "validate_role_type",
    "validate_permissions_exist",
    "check_role_exists",
    "check_role_name_unique",
    "check_role_usage",
    "build_roles_filter_query",
    "build_roles_count_query",
    "assign_permissions_to_role",
    "remove_all_permissions_from_role",
    "build_role_filter_message",
    # Organisation utilities
    "validate_organisation_status",
    "validate_organisation_slug",
    "validate_organisation_name_filter",
    "check_organisation_exists",
    "check_organisation_slug_unique",
    "check_organisation_access",
    "build_organisations_filter_query",
    "build_organisations_count_query",
    "build_organisation_detail_query",
    "get_default_permissions",
    "create_default_permissions_for_organisation",
    "create_super_admin_role",
    "assign_all_permissions_to_role",
    "build_organisation_filter_message",
    "build_organisation_creation_success_message",
]
