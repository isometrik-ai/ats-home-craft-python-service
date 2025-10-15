# pylint: disable=all

"""
Test module for common utilities functionality.

This module contains comprehensive tests for:
- UserContext and PerformanceTimer dataclasses
- Permission utilities and validation
- User context extraction
- UUID validation
- Pagination validation
- Exception handling decorators
- Helper functions
- Error scenarios and edge cases
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi import HTTPException, status

from apps.user_service.app.dependencies.common_utils import (
    UserContext,
    PerformanceTimer,
    format_permissions_data,
    extract_user_context,
    require_permission,
    check_permissions,
    validate_uuid_format,
    validate_pagination_params,
    handle_api_exceptions,
    format_iso_datetime,
    safe_json_loads,
    get_user_in_organization,
    set_audit_old_data_from_user,
    ROLE_TYPES,
    ORG_STATUSES,
    USER_STATUSES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    UUID_PATTERN
)
from apps.user_service.app.schemas.admin_access_management import PermissionItem
from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE


class TestUserContext:
    """Tests for UserContext dataclass."""

    def test_user_context_creation_with_all_fields(self):
        """Test UserContext creation with all fields."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id="org123",
            user_type="organization_member"
        )

        assert user_context.user_id == "user123"
        assert user_context.email == "test@example.com"
        assert user_context.organization_id == "org123"
        assert user_context.user_type == "organization_member"

    def test_user_context_creation_with_minimal_fields(self):
        """Test UserContext creation with only required fields."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com"
        )

        assert user_context.user_id == "user123"
        assert user_context.email == "test@example.com"
        assert user_context.organization_id is None
        assert user_context.user_type is None

    def test_user_context_creation_with_none_values(self):
        """Test UserContext creation with None values for optional fields."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id=None,
            user_type=None
        )

        assert user_context.user_id == "user123"
        assert user_context.email == "test@example.com"
        assert user_context.organization_id is None
        assert user_context.user_type is None


class TestPerformanceTimer:
    """Tests for PerformanceTimer dataclass."""

    def test_performance_timer_initialization(self):
        """Test PerformanceTimer initialization."""
        timer = PerformanceTimer("test_operation")

        assert timer.operation_name == "test_operation"
        assert timer.start_time is not None
        assert isinstance(timer.start_time, float)

    def test_performance_timer_checkpoint(self):
        """Test PerformanceTimer checkpoint functionality."""
        # Mock time.time to control elapsed time
        with patch('time.time') as mock_time:
            mock_time.side_effect = [1000.0, 1000.1]  # 100ms elapsed

            timer = PerformanceTimer("test_operation")

            with patch('builtins.print') as mock_print:
                elapsed = timer.checkpoint("step1")

                assert abs(elapsed - 100.0) < 0.001  # Allow small floating-point differences
                mock_print.assert_called_once_with("step1 took 100.00ms")

    def test_performance_timer_total_time(self):
        """Test PerformanceTimer total_time functionality."""
        # Mock time.time to control elapsed time
        with patch('time.time') as mock_time:
            mock_time.side_effect = [1000.0, 1000.2]  # 200ms elapsed

            timer = PerformanceTimer("test_operation")

            with patch('builtins.print') as mock_print:
                elapsed = timer.total_time()

                assert abs(elapsed - 200.0) < 0.001  # Allow small floating-point differences
                mock_print.assert_called_once_with("Total test_operation time: 200.00ms")

    def test_performance_timer_checkpoint_without_start_time(self):
        """Test PerformanceTimer checkpoint when start_time is None."""
        timer = PerformanceTimer("test_operation")
        timer.start_time = None

        with pytest.raises(AssertionError, match="Timer not initialized"):
            timer.checkpoint("step1")

    def test_performance_timer_total_time_without_start_time(self):
        """Test PerformanceTimer total_time when start_time is None."""
        timer = PerformanceTimer("test_operation")
        timer.start_time = None

        with pytest.raises(AssertionError, match="Timer not initialized"):
            timer.total_time()


class TestFormatPermissionsData:
    """Tests for format_permissions_data function."""

    @pytest.mark.asyncio
    async def test_format_permissions_data_success(self):
        """Test successful formatting of permissions data."""
        permissions_data = [
            {
                "id": "perm1",
                "name": "Manage Users",
                "code": "users.manage",
                "category": "user_management",
                "description": "Manage user accounts",
                "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)
            },
            {
                "id": "perm2",
                "name": "View Roles",
                "code": "roles.view",
                "category": "role_management",
                "description": "View role information",
                "created_at": None
            }
        ]

        result = format_permissions_data(permissions_data)

        assert len(result) == 2
        assert isinstance(result[0], PermissionItem)
        assert isinstance(result[1], PermissionItem)

        # Check first permission
        assert result[0].id == "perm1"
        assert result[0].name == "Manage Users"
        assert result[0].code == "users.manage"
        assert result[0].category == "user_management"
        assert result[0].description == "Manage user accounts"
        assert result[0].created_at == "2024-01-01T00:00:00+00:00"

        # Check second permission
        assert result[1].id == "perm2"
        assert result[1].name == "View Roles"
        assert result[1].code == "roles.view"
        assert result[1].category == "role_management"
        assert result[1].description == "View role information"
        assert result[1].created_at == ""

    @pytest.mark.asyncio
    async def test_format_permissions_data_empty_list(self):
        """Test formatting empty permissions data."""
        result = format_permissions_data([])

        assert result == []

    def test_format_permissions_data_with_string_datetime(self):
        """Test formatting permissions data with string datetime (should work)."""
        permissions_data = [
            {
                "id": "perm1",
                "name": "Test Permission",
                "code": "test.code",
                "category": "test",
                "description": "Test description",
                "created_at": "2024-01-01T00:00:00Z"
            }
        ]
        
        result = format_permissions_data(permissions_data)
        
        assert len(result) == 1
        assert result[0].id == "perm1"
        assert result[0].created_at == "2024-01-01T00:00:00Z"


class TestExtractUserContext:
    """Tests for extract_user_context function."""

    def test_extract_user_context_success(self):
        """Test successful user context extraction."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {
                "organization_id": "org123",
                "type": "organization_member"
            }
        }

        result = extract_user_context(current_user)

        assert isinstance(result, UserContext)
        assert result.user_id == "user123"
        assert result.email == "test@example.com"
        assert result.organization_id == "org123"
        assert result.user_type == "organization_member"

    def test_extract_user_context_minimal_data(self):
        """Test user context extraction with minimal data."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {}
        }

        result = extract_user_context(current_user)

        assert isinstance(result, UserContext)
        assert result.user_id == "user123"
        assert result.email == "test@example.com"
        assert result.organization_id is None
        assert result.user_type is None

    def test_extract_user_context_missing_user_id(self):
        """Test user context extraction with missing user ID."""
        current_user = {
            "email": "test@example.com",
            "user_metadata": {}
        }

        with pytest.raises(HTTPException) as exc_info:
            extract_user_context(current_user)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid token: user ID not found" in str(exc_info.value.detail)

    def test_extract_user_context_missing_email(self):
        """Test user context extraction with missing email."""
        current_user = {
            "sub": "user123",
            "user_metadata": {}
        }

        with pytest.raises(HTTPException) as exc_info:
            extract_user_context(current_user)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid token: email not found" in str(exc_info.value.detail)

    def test_extract_user_context_empty_user_metadata(self):
        """Test user context extraction with empty user_metadata."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com"
        }

        result = extract_user_context(current_user)

        assert result.user_id == "user123"
        assert result.email == "test@example.com"
        assert result.organization_id is None
        assert result.user_type is None

    def test_extract_user_context_none_user_metadata(self):
        """Test user context extraction with None user_metadata."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": None
        }

        with pytest.raises(AttributeError):
            extract_user_context(current_user)


class TestRequirePermission:
    """Tests for require_permission function."""

    @pytest.mark.asyncio
    async def test_require_permission_success_single(self):
        """Test successful permission check with single permission."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id="org123"
        )

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check, \
             patch('time.time', side_effect=[1000.0, 1000.1]), \
             patch('builtins.print') as mock_print:

            await require_permission("users.manage", user_context, "manage users")

            mock_check.assert_called_once_with(
                permission_code=["users.manage"],
                user_id="user123",
                organisation_id=None
            )
            mock_print.assert_called_once_with("Permission check took 100.00ms")

    @pytest.mark.asyncio
    async def test_require_permission_success_multiple(self):
        """Test successful permission check with multiple permissions."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id="org123"
        )

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check, \
             patch('time.time', side_effect=[1000.0, 1000.1]), \
             patch('builtins.print') as mock_print:

            await require_permission(["users.manage", "roles.view"], user_context, "manage users")

            mock_check.assert_called_once_with(
                permission_code=["users.manage", "roles.view"],
                user_id="user123",
                organisation_id=None
            )

    @pytest.mark.asyncio
    async def test_require_permission_denied(self):
        """Test permission check when user lacks permission."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id="org123"
        )

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=False)) as mock_check, \
             patch('time.time', side_effect=[1000.0, 1000.1]), \
             patch('builtins.print') as mock_print:

            with pytest.raises(HTTPException) as exc_info:
                await require_permission("users.manage", user_context, "manage users")

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "Insufficient permissions to manage users" in str(exc_info.value.detail)
            mock_print.assert_has_calls([
                call("Permission check took 100.00ms"),
                call("Permission denied - manage users")
            ])

    @pytest.mark.asyncio
    async def test_require_permission_without_timing(self):
        """Test permission check without timing."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id="org123"
        )

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check, \
             patch('builtins.print') as mock_print:

            await require_permission("users.manage", user_context, "manage users", with_timing=False)

            mock_check.assert_called_once()
            # Should not print timing information
            mock_print.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_permission_with_none_organization_id(self):
        """Test permission check with None organization_id."""
        user_context = UserContext(
            user_id="user123",
            email="test@example.com",
            organization_id=None
        )

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check:

            await require_permission("users.manage", user_context, "manage users")

            mock_check.assert_called_once_with(
                permission_code=["users.manage"],
                user_id="user123",
                organisation_id=None
            )


class TestCheckPermissions:
    """Tests for check_permissions function."""

    @pytest.mark.asyncio
    async def test_check_permissions_success(self):
        """Test successful permission check."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"}
        }

        with patch('apps.user_service.app.dependencies.common_utils.extract_user_context') as mock_extract, \
             patch('apps.user_service.app.dependencies.common_utils.require_permission') as mock_require:

            mock_user_context = UserContext("user123", "test@example.com", "org123")
            mock_extract.return_value = mock_user_context

            result = await check_permissions(current_user, "users.manage", "manage users")

            mock_extract.assert_called_once_with(current_user)
            mock_require.assert_called_once_with(
                permission_code="users.manage",
                user_context=mock_user_context,
                action_description="manage users",
                organization_id="org123"
            )
            assert result == mock_user_context

    @pytest.mark.asyncio
    async def test_check_permissions_with_list(self):
        """Test permission check with list of permissions."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"}
        }

        with patch('apps.user_service.app.dependencies.common_utils.extract_user_context') as mock_extract, \
             patch('apps.user_service.app.dependencies.common_utils.require_permission') as mock_require:

            mock_user_context = UserContext("user123", "test@example.com", "org123")
            mock_extract.return_value = mock_user_context

            result = await check_permissions(current_user, ["users.manage", "roles.view"])

            mock_require.assert_called_once_with(
                permission_code=["users.manage", "roles.view"],
                user_context=mock_user_context,
                action_description="access role details",
                organization_id="org123"
            )
            assert result == mock_user_context

    @pytest.mark.asyncio
    async def test_check_permissions_default_action_description(self):
        """Test permission check with default action description."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"}
        }

        with patch('apps.user_service.app.dependencies.common_utils.extract_user_context') as mock_extract, \
             patch('apps.user_service.app.dependencies.common_utils.require_permission') as mock_require:

            mock_user_context = UserContext("user123", "test@example.com", "org123")
            mock_extract.return_value = mock_user_context

            await check_permissions(current_user, "users.manage")

            mock_require.assert_called_once_with(
                permission_code="users.manage",
                user_context=mock_user_context,
                action_description="access role details",
                organization_id="org123"
            )


class TestValidateUuidFormat:
    """Tests for validate_uuid_format function."""

    @pytest.mark.asyncio
    async def test_validate_uuid_format_valid(self):
        """Test UUID validation with valid UUID."""
        valid_uuid = str(uuid.uuid4())

        # Should not raise any exception
        validate_uuid_format(valid_uuid)

    @pytest.mark.asyncio
    async def test_validate_uuid_format_valid_with_custom_field_name(self):
        """Test UUID validation with custom field name."""
        valid_uuid = str(uuid.uuid4())

        # Should not raise any exception
        validate_uuid_format(valid_uuid, "role ID")

    @pytest.mark.asyncio
    async def test_validate_uuid_format_invalid(self):
        """Test UUID validation with invalid UUID."""
        invalid_uuid = "not-a-uuid"

        with pytest.raises(HTTPException) as exc_info:
            validate_uuid_format(invalid_uuid)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid ID format" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_validate_uuid_format_invalid_with_custom_field_name(self):
        """Test UUID validation with invalid UUID and custom field name."""
        invalid_uuid = "not-a-uuid"

        with pytest.raises(HTTPException) as exc_info:
            validate_uuid_format(invalid_uuid, "user ID")

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid user ID format" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_validate_uuid_format_empty_string(self):
        """Test UUID validation with empty string."""
        with pytest.raises(HTTPException) as exc_info:
            validate_uuid_format("")

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid ID format" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_validate_uuid_format_none(self):
        """Test UUID validation with None."""
        # uuid.UUID(None) raises TypeError, not ValueError, so it's not caught
        with pytest.raises(TypeError):
            validate_uuid_format(None)


class TestValidatePaginationParams:
    """Tests for validate_pagination_params function."""

    def test_validate_pagination_params_valid(self):
        """Test valid pagination parameters."""
        page, page_size, offset = validate_pagination_params(page=2, page_size=10)

        assert page == 2
        assert page_size == 10
        assert offset == 10  # (2-1) * 10

    def test_validate_pagination_params_defaults(self):
        """Test pagination parameters with defaults."""
        page, page_size, offset = validate_pagination_params()

        assert page == 1
        assert page_size == 20
        assert offset == 0

    def test_validate_pagination_params_custom_max(self):
        """Test pagination parameters with custom max page size."""
        page, page_size, offset = validate_pagination_params(page=1, page_size=50, max_page_size=200)

        assert page == 1
        assert page_size == 50
        assert offset == 0

    def test_validate_pagination_params_page_zero(self):
        """Test pagination parameters with page=0."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page=0)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page must be a positive integer" in str(exc_info.value.detail)

    def test_validate_pagination_params_page_negative(self):
        """Test pagination parameters with negative page."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page=-1)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page must be a positive integer" in str(exc_info.value.detail)

    def test_validate_pagination_params_page_size_zero(self):
        """Test pagination parameters with page_size=0."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page_size=0)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page size must be between 1 and 100" in str(exc_info.value.detail)

    def test_validate_pagination_params_page_size_negative(self):
        """Test pagination parameters with negative page_size."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page_size=-1)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page size must be between 1 and 100" in str(exc_info.value.detail)

    def test_validate_pagination_params_page_size_too_large(self):
        """Test pagination parameters with page_size exceeding max."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page_size=101)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page size must be between 1 and 100" in str(exc_info.value.detail)

    def test_validate_pagination_params_page_size_at_max(self):
        """Test pagination parameters with page_size at maximum."""
        page, page_size, offset = validate_pagination_params(page_size=100)

        assert page == 1
        assert page_size == 100
        assert offset == 0

    def test_validate_pagination_params_custom_max_exceeded(self):
        """Test pagination parameters exceeding custom max."""
        with pytest.raises(HTTPException) as exc_info:
            validate_pagination_params(page_size=150, max_page_size=100)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Page size must be between 1 and 100" in str(exc_info.value.detail)


class TestHandleApiExceptions:
    """Tests for handle_api_exceptions decorator."""

    @pytest.mark.asyncio
    async def test_handle_api_exceptions_success(self):
        """Test decorator with successful function execution."""
        @handle_api_exceptions("test operation")
        async def test_func():
            return "success"

        result = await test_func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_handle_api_exceptions_http_exception_passthrough(self):
        """Test decorator passes through HTTPException."""
        @handle_api_exceptions("test operation")
        async def test_func():
            raise HTTPException(status_code=404, detail="Not found")

        with pytest.raises(HTTPException) as exc_info:
            await test_func()

        assert exc_info.value.status_code == 404
        assert "Not found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_handle_api_exceptions_database_operation_error(self):
        """Test decorator handles DatabaseOperationError."""
        @handle_api_exceptions("test operation")
        async def test_func():
            raise DatabaseOperationError("Database connection failed")

        with patch('builtins.print') as mock_print:
            with pytest.raises(HTTPException) as exc_info:
                await test_func()

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Database error during test operation" in str(exc_info.value.detail)
        mock_print.assert_called_once_with("Database error in test operation: Database connection failed")

    @pytest.mark.asyncio
    async def test_handle_api_exceptions_generic_exception(self):
        """Test decorator handles generic exceptions."""
        @handle_api_exceptions("test operation")
        async def test_func():
            raise ValueError("Something went wrong")

        with patch('builtins.print') as mock_print:
            with pytest.raises(HTTPException) as exc_info:
                await test_func()

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Value error during test operation" in str(exc_info.value.detail)
        mock_print.assert_called_once_with("Value error in test operation: Something went wrong")

    @pytest.mark.asyncio
    async def test_handle_api_exceptions_with_args_and_kwargs(self):
        """Test decorator preserves function arguments."""
        @handle_api_exceptions("test operation")
        async def test_func(arg1, arg2, kwarg1=None, kwarg2=None):
            return f"{arg1}-{arg2}-{kwarg1}-{kwarg2}"

        result = await test_func("a", "b", kwarg1="c", kwarg2="d")
        assert result == "a-b-c-d"


class TestFormatIsoDatetime:
    """Tests for format_iso_datetime function."""

    def test_format_iso_datetime_with_datetime(self):
        """Test formatting datetime object."""
        dt = datetime(2024, 1, 1, 12, 30, 45, tzinfo=timezone.utc)
        result = format_iso_datetime(dt)

        assert result == "2024-01-01T12:30:45+00:00"

    def test_format_iso_datetime_with_none(self):
        """Test formatting None datetime."""
        result = format_iso_datetime(None)

        assert result is None

    def test_format_iso_datetime_with_string(self):
        """Test formatting string datetime (should return as-is)."""
        dt_str = "2024-01-01T12:30:45Z"
        
        result = format_iso_datetime(dt_str)
        assert result == dt_str

    def test_format_iso_datetime_with_naive_datetime(self):
        """Test formatting naive datetime."""
        dt = datetime(2024, 1, 1, 12, 30, 45)
        result = format_iso_datetime(dt)

        assert result == "2024-01-01T12:30:45"


class TestSafeJsonLoads:
    """Tests for safe_json_loads function."""

    def test_safe_json_loads_valid_json_string(self):
        """Test parsing valid JSON string."""
        json_str = '{"key": "value", "number": 123}'
        result = safe_json_loads(json_str)

        assert result == {"key": "value", "number": 123}

    def test_safe_json_loads_valid_json_string_with_default(self):
        """Test parsing valid JSON string with default."""
        json_str = '{"key": "value"}'
        result = safe_json_loads(json_str, default={})

        assert result == {"key": "value"}

    def test_safe_json_loads_invalid_json_string(self):
        """Test parsing invalid JSON string."""
        json_str = '{"key": "value", "number": 123'  # Missing closing brace
        result = safe_json_loads(json_str)

        assert result is None

    def test_safe_json_loads_invalid_json_string_with_default(self):
        """Test parsing invalid JSON string with default."""
        json_str = '{"key": "value", "number": 123'  # Missing closing brace
        result = safe_json_loads(json_str, default={})

        assert result == {}

    def test_safe_json_loads_empty_string(self):
        """Test parsing empty string."""
        result = safe_json_loads("")

        assert result is None

    def test_safe_json_loads_empty_string_with_default(self):
        """Test parsing empty string with default."""
        result = safe_json_loads("", default=[])

        assert result == []

    def test_safe_json_loads_none(self):
        """Test parsing None."""
        result = safe_json_loads(None)

        assert result is None

    def test_safe_json_loads_none_with_default(self):
        """Test parsing None with default."""
        result = safe_json_loads(None, default={})

        assert result == {}

    def test_safe_json_loads_already_parsed(self):
        """Test parsing already parsed JSON."""
        data = {"key": "value", "number": 123}
        result = safe_json_loads(data)

        assert result == data

    def test_safe_json_loads_type_error(self):
        """Test parsing with non-string input (returns as-is)."""
        result = safe_json_loads(123)  # Number instead of string

        assert result == 123  # Returns the input as-is

    def test_safe_json_loads_type_error_with_default(self):
        """Test parsing with non-string input with default (returns as-is)."""
        result = safe_json_loads(123, default=[])

        assert result == 123  # Returns the input as-is, default is ignored


class TestGetUserInOrganization:
    """Tests for get_user_in_organization function."""

    @pytest.mark.asyncio
    async def test_get_user_in_organization_success(self):
        """Test successful user retrieval."""
        user_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User"
        }

        with patch('apps.user_service.app.dependencies.common_utils.get_user_profile_by_id',
                  AsyncMock(return_value=user_data)) as mock_get_profile:

            result = await get_user_in_organization("user123", "org123")

            mock_get_profile.assert_called_once_with("user123", "org123")
            assert result == user_data

    @pytest.mark.asyncio
    async def test_get_user_in_organization_user_not_found(self):
        """Test user not found in organization."""
        with patch('apps.user_service.app.dependencies.common_utils.get_user_profile_by_id',
                  AsyncMock(return_value=None)) as mock_get_profile:

            with pytest.raises(HTTPException) as exc_info:
                await get_user_in_organization("user123", "org123")

            assert exc_info.value.status_code == 404
            assert USER_NOT_FOUND_MESSAGE in str(exc_info.value.detail)
            mock_get_profile.assert_called_once_with("user123", "org123")

    @pytest.mark.asyncio
    async def test_get_user_in_organization_empty_user_data(self):
        """Test empty user data returned."""
        with patch('apps.user_service.app.dependencies.common_utils.get_user_profile_by_id',
                  AsyncMock(return_value={})) as mock_get_profile:

            with pytest.raises(HTTPException) as exc_info:
                await get_user_in_organization("user123", "org123")

            assert exc_info.value.status_code == 404
            assert USER_NOT_FOUND_MESSAGE in str(exc_info.value.detail)


class TestSetAuditOldDataFromUser:
    """Tests for set_audit_old_data_from_user function."""

    def test_set_audit_old_data_from_user_complete_data(self):
        """Test setting audit data with complete user data."""
        request = MagicMock()
        request.state = MagicMock()

        current_user_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "avatar_url": "https://example.com/avatar.jpg",
            "status": "active",
            "role_id": "role123",
            "organization_id": "org123",
            "joined_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "last_active_at": datetime(2024, 1, 2, tzinfo=timezone.utc)
        }

        set_audit_old_data_from_user(request, current_user_data)

        expected_audit_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "avatar_url": "https://example.com/avatar.jpg",
            "status": "active",
            "role_id": "role123",
            "organization_id": "org123",
            "joined_at": "2024-01-01T00:00:00+00:00",
            "last_active_at": "2024-01-02T00:00:00+00:00"
        }

        assert request.state.raw_audit_old_data == expected_audit_data

    def test_set_audit_old_data_from_user_minimal_data(self):
        """Test setting audit data with minimal user data."""
        request = MagicMock()
        request.state = MagicMock()

        current_user_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "organization_id": "org123"
        }

        set_audit_old_data_from_user(request, current_user_data)

        expected_audit_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": None,
            "last_name": None,
            "phone": None,
            "timezone": None,
            "avatar_url": None,
            "status": None,
            "role_id": "",
            "organization_id": "org123"
        }

        assert request.state.raw_audit_old_data == expected_audit_data

    def test_set_audit_old_data_from_user_with_none_role_id(self):
        """Test setting audit data with None role_id."""
        request = MagicMock()
        request.state = MagicMock()

        current_user_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "organization_id": "org123",
            "role_id": None
        }

        set_audit_old_data_from_user(request, current_user_data)

        assert request.state.raw_audit_old_data["role_id"] == "None"  # str(None) = "None"

    def test_set_audit_old_data_from_user_without_timestamps(self):
        """Test setting audit data without timestamp fields."""
        request = MagicMock()
        request.state = MagicMock()

        current_user_data = {
            "user_id": "user123",
            "email": "test@example.com",
            "full_name": "Test User",
            "organization_id": "org123"
        }

        set_audit_old_data_from_user(request, current_user_data)

        audit_data = request.state.raw_audit_old_data
        assert "joined_at" not in audit_data
        assert "last_active_at" not in audit_data


class TestConstants:
    """Tests for module constants."""

    def test_role_types_constant(self):
        """Test ROLE_TYPES constant."""
        assert ROLE_TYPES == ["system", "custom"]

    def test_org_statuses_constant(self):
        """Test ORG_STATUSES constant."""
        assert ORG_STATUSES == ["active", "suspended", "trial"]

    def test_user_statuses_constant(self):
        """Test USER_STATUSES constant."""
        assert USER_STATUSES == ["active", "inactive", "pending", "invited"]

    def test_default_page_size_constant(self):
        """Test DEFAULT_PAGE_SIZE constant."""
        assert DEFAULT_PAGE_SIZE == 20

    def test_max_page_size_constant(self):
        """Test MAX_PAGE_SIZE constant."""
        assert MAX_PAGE_SIZE == 100

    def test_uuid_pattern_constant(self):
        """Test UUID_PATTERN constant."""
        import re
        valid_uuid = str(uuid.uuid4())
        invalid_uuid = "not-a-uuid"

        assert re.match(UUID_PATTERN, valid_uuid) is not None
        assert re.match(UUID_PATTERN, invalid_uuid) is None


class TestIntegration:
    """Integration tests for common utilities."""

    @pytest.mark.asyncio
    async def test_complete_permission_workflow(self):
        """Test complete permission checking workflow."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"}
        }

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check:

            # Test the complete workflow
            user_context = extract_user_context(current_user)
            await require_permission("users.manage", user_context, "manage users")

            assert user_context.user_id == "user123"
            assert user_context.email == "test@example.com"
            assert user_context.organization_id == "org123"
            mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_permission_workflow_with_check_permissions(self):
        """Test permission workflow using check_permissions function."""
        current_user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"}
        }

        with patch('apps.user_service.app.dependencies.common_utils.check_user_access_async',
                  AsyncMock(return_value=True)) as mock_check:

            result = await check_permissions(current_user, "users.manage", "manage users")

            assert isinstance(result, UserContext)
            assert result.user_id == "user123"
            mock_check.assert_called_once()

    def test_pagination_workflow(self):
        """Test pagination parameter validation workflow."""
        # Test various pagination scenarios
        page, page_size, offset = validate_pagination_params(page=3, page_size=25)
        assert page == 3
        assert page_size == 25
        assert offset == 50  # (3-1) * 25

    def test_json_parsing_workflow(self):
        """Test JSON parsing workflow."""
        # Test various JSON scenarios
        valid_json = '{"users": [{"id": "123", "name": "Test"}]}'
        invalid_json = '{"users": [{"id": "123", "name": "Test"}'  # Missing closing brace

        result1 = safe_json_loads(valid_json, default={})
        result2 = safe_json_loads(invalid_json, default={})

        assert result1 == {"users": [{"id": "123", "name": "Test"}]}
        assert result2 == {}

    @pytest.mark.asyncio
    async def test_uuid_validation_workflow(self):
        """Test UUID validation workflow."""
        valid_uuid = str(uuid.uuid4())
        invalid_uuid = "not-a-uuid"

        # Should not raise exception
        validate_uuid_format(valid_uuid, "user ID")

        # Should raise exception
        with pytest.raises(HTTPException):
            validate_uuid_format(invalid_uuid, "user ID")
