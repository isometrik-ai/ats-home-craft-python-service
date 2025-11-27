# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, status
from postgrest import APIError

from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisation_filter_message,
    create_organisation_with_super_admin,
    _save_isometrik_application_data
)
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    DatabaseOperationError,
    SupabaseAPIError,
)


class TestValidateOrganisationStatus:
    """Test cases for validate_organisation_status function."""

    def test_validate_organisation_status_valid(self):
        """Test organisation status validation with valid statuses."""
        valid_statuses = ["active", "suspended", "trial"]

        for status_val in valid_statuses:
            # Should not raise any exception
            validate_organisation_status(status_val)

    def test_validate_organisation_status_invalid(self):
        """Test organisation status validation with invalid status."""
        invalid_statuses = ["invalid", "unknown", "pending", "", "inactive"]

        for status_val in invalid_statuses:
            with pytest.raises(HTTPException) as exc_info:
                validate_organisation_status(status_val)

            assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Status must be one of: active, suspended, trial" in str(exc_info.value.detail)

    def test_validate_organisation_status_none(self):
        """Test organisation status validation with None value."""
        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_status(None)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestValidateOrganisationNameFilter:
    """Test cases for validate_organisation_name_filter function."""

    def test_validate_organisation_name_filter_valid(self):
        """Test organisation name filter validation with valid input."""
        valid_names = [
            "Test Organisation",
            "A",
            "x" * 255,  # Max length
            "  Test Org  "  # Should be trimmed
        ]

        for name in valid_names:
            result = validate_organisation_name_filter(name)
            assert result == name.strip()

    def test_validate_organisation_name_filter_empty(self):
        """Test organisation name filter validation with empty input."""
        # Test truly empty inputs (None, empty string)
        empty_inputs = ["", None]

        for name in empty_inputs:
            with pytest.raises(HTTPException) as exc_info:
                validate_organisation_name_filter(name)

            assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Name filter cannot be empty" in str(exc_info.value.detail)

    def test_validate_organisation_name_filter_whitespace_only(self):
        """Test organisation name filter validation with whitespace-only input."""
        whitespace_inputs = ["   ", "\t", "\n", " \t \n "]

        for name in whitespace_inputs:
            with pytest.raises(HTTPException) as exc_info:
                validate_organisation_name_filter(name)

            assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Name filter must be between 1 and 255 characters" in str(exc_info.value.detail)

    def test_validate_organisation_name_filter_too_long(self):
        """Test organisation name filter validation with too long input."""
        long_name = "x" * 256  # Exceeds max length

        with pytest.raises(HTTPException) as exc_info:
            validate_organisation_name_filter(long_name)

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Name filter must be between 1 and 255 characters" in str(exc_info.value.detail)

    def test_validate_organisation_name_filter_whitespace_trimming(self):
        """Test that organisation name filter trims whitespace."""
        name_with_spaces = "  Test Organisation  "
        result = validate_organisation_name_filter(name_with_spaces)
        assert result == "Test Organisation"


class TestBuildOrganisationFilterMessage:
    """Test cases for build_organisation_filter_message function."""

    def test_build_organisation_filter_message_default(self):
        """Test organisation filter message with default parameters."""
        message = build_organisation_filter_message()

        assert "All organizations retrieved successfully" in message
        assert "page_size=20" in message
        assert "page=1" not in message  # Should not appear for page 1

    def test_build_organisation_filter_message_with_name(self):
        """Test organisation filter message with name filter."""
        message = build_organisation_filter_message(name="Test Org")

        assert "All organizations retrieved successfully" in message
        assert "name='Test Org'" in message
        assert "page_size=20" in message

    def test_build_organisation_filter_message_with_status(self):
        """Test organisation filter message with status filter."""
        message = build_organisation_filter_message(org_status="active")

        assert "All organizations retrieved successfully" in message
        assert "status='active'" in message
        assert "page_size=20" in message

    def test_build_organisation_filter_message_with_pagination(self):
        """Test organisation filter message with pagination."""
        message = build_organisation_filter_message(page=3, page_size=50)

        assert "All organizations retrieved successfully" in message
        assert "page=3" in message
        assert "page_size=50" in message

    def test_build_organisation_filter_message_with_all_filters(self):
        """Test organisation filter message with all filters."""
        message = build_organisation_filter_message(
            name="Test Org",
            org_status="active",
            page=2,
            page_size=25
        )

        assert "All organizations retrieved successfully" in message
        assert "name='Test Org'" in message
        assert "status='active'" in message
        assert "page=2" in message
        assert "page_size=25" in message

    def test_build_organisation_filter_message_no_filters(self):
        """Test organisation filter message with no filters (page 1)."""
        message = build_organisation_filter_message(page=1, page_size=20)

        assert "All organizations retrieved successfully" in message
        assert "page_size=20" in message
        assert "page=1" not in message  # Should not appear for page 1


class TestCreateOrganisationWithSuperAdmin:
    """Test cases for create_organisation_with_super_admin function."""

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_success_isometrik_disabled(self):
        """Test successful organisation creation with super admin when Isometrik is disabled."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "name": "Test Organization"
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2", "perm-3"]
        mock_member_result = {"id": str(uuid.uuid4())}

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)) as mock_create_org, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)) as mock_create_role, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)) as mock_create_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)) as mock_assign_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(return_value=mock_member_result)) as mock_add_member:

            await create_organisation_with_super_admin(org_data)

            # Verify all functions were called with correct arguments
            mock_create_org.assert_called_once_with(org_data)
            mock_create_role.assert_called_once_with(org_data["organization_id"])
            mock_create_perms.assert_called_once_with(org_data["organization_id"])
            mock_assign_perms.assert_called_once_with(mock_role_result["id"], org_data["organization_id"])

            expected_member_data = {
                "user_id": org_data["user_id"],
                "email": org_data["email"],
                "first_name": org_data["first_name"],
                "last_name": org_data["last_name"],
                "phone": org_data["phone"],
                "timezone": org_data["timezone"],
                "role_id": mock_role_result["id"],
                "status": "active"
            }
            mock_add_member.assert_called_once_with(org_data["organization_id"], expected_member_data)

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_success_isometrik_enabled(self):
        """Test successful organisation creation with super admin when Isometrik is enabled."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "name": "Test Organization"
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2", "perm-3"]
        mock_member_result = {"id": str(uuid.uuid4())}
        isometrik_response = {
            "data": {
                "projectId": "test-project-id",
                "keysetId": "test-keyset-id"
            }
        }

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", AsyncMock(return_value=isometrik_response)) as mock_isometrik_create, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)) as mock_create_org, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)) as mock_create_role, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)) as mock_create_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)) as mock_assign_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(return_value=mock_member_result)) as mock_add_member, \
             patch("apps.user_service.app.dependencies.organisation_utils._save_isometrik_application_data", AsyncMock()) as mock_save_isometrik:

            await create_organisation_with_super_admin(org_data)

            # Verify Isometrik was called first
            mock_isometrik_create.assert_called_once_with(
                organization_name="Test Organization",
                product_types=["chat", "video"],
                plan="basic"
            )
            
            # Verify all functions were called with correct arguments
            mock_create_org.assert_called_once_with(org_data)
            mock_create_role.assert_called_once_with(org_data["organization_id"])
            mock_create_perms.assert_called_once_with(org_data["organization_id"])
            mock_assign_perms.assert_called_once_with(mock_role_result["id"], org_data["organization_id"])

            expected_member_data = {
                "user_id": org_data["user_id"],
                "email": org_data["email"],
                "first_name": org_data["first_name"],
                "last_name": org_data["last_name"],
                "phone": org_data["phone"],
                "timezone": org_data["timezone"],
                "role_id": mock_role_result["id"],
                "status": "active"
            }
            mock_add_member.assert_called_once_with(org_data["organization_id"], expected_member_data)
            mock_save_isometrik.assert_called_once_with(org_data["organization_id"], isometrik_response["data"])

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_isometrik_fails(self):
        """Test organisation creation fails when Isometrik is enabled but creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "name": "Test Organization"
        }

        from libs.shared_utils.isometrik_service import IsometrikAPIError
        isometrik_error = IsometrikAPIError(
            "Isometrik API error: 409 - Conflict",
            status_code=409,
            response_text='{"status":"Conflict"}'
        )

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", AsyncMock(side_effect=isometrik_error)):

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create Isometrik application" in exc_info.value.detail
            assert "Isometrik API error: 409 - Conflict" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_isometrik_invalid_response(self):
        """Test organisation creation fails when Isometrik returns invalid response."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "name": "Test Organization"
        }

        isometrik_response = {"status": "success"}  # Missing "data" key

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=True), \
             patch("libs.shared_utils.isometrik_service.create_isometrik_application", AsyncMock(return_value=isometrik_response)):

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Invalid response from Isometrik API" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_missing_fields(self):
        """Test organisation creation with missing optional fields."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com"
            # Missing first_name, last_name, phone, timezone
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2"]
        mock_member_result = {"id": str(uuid.uuid4())}

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)), \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)), \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(return_value=mock_member_result)) as mock_add_member:

            await create_organisation_with_super_admin(org_data)

            # Verify member data uses defaults for missing fields
            expected_member_data = {
                "user_id": org_data["user_id"],
                "email": org_data["email"],
                "first_name": None,
                "last_name": None,
                "phone": None,
                "timezone": "UTC",  # Default timezone
                "role_id": mock_role_result["id"],
                "status": "active"
            }
            mock_add_member.assert_called_once_with(org_data["organization_id"], expected_member_data)

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_database_error(self):
        """Test organisation creation with database error and cleanup."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        db_error = Exception("Database connection failed")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(side_effect=db_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_cleanup_error(self):
        """Test organisation creation with database error and cleanup failure."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        db_error = Exception("Database connection failed")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(side_effect=db_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_role_creation_error(self):
        """Test organisation creation when role creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        role_error = Exception("Role creation failed")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(side_effect=role_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_permissions_error(self):
        """Test organisation creation when permissions creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        permissions_error = Exception("Permissions creation failed")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(side_effect=permissions_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_member_creation_error(self):
        """Test organisation creation when member creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC"
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2"]
        member_error = Exception("Member creation failed")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)), \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)), \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(side_effect=member_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_duplicate_slug_error(self):
        """Test organisation creation handles duplicate slug API error."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "slug": "existing-slug",
            "email": "admin@test.com",
        }

        api_error = APIError({
            "message": 'duplicate key value violates unique constraint "organizations_slug_key"',
            "code": "23505",
        })

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(side_effect=api_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "Organisation slug already exists" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_rls_violation(self):
        """Test organisation creation handles RLS policy violation from SupabaseAPIError."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "slug": "new-org",
        }

        underlying_api_error = APIError({
            "message": "new row violates row-level security policy",
            "code": "42501",
        })
        supabase_error = SupabaseAPIError("Supabase API error", operation="create_super_admin_role")
        supabase_error.__cause__ = underlying_api_error

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=None)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(side_effect=supabase_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Row-level security policy violation" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_generic_api_error(self):
        """Test organisation creation handles generic Supabase API error."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
        }

        api_error = APIError({
            "message": "foreign key constraint violation",
            "code": "12345",
        })

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=None)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(side_effect=api_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_organisation_with_super_admin_database_operation_error(self):
        """Test organisation creation handles DatabaseOperationError properly."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
        }

        db_operation_error = DatabaseOperationError("Database failure", operation="add_member")

        with patch("libs.shared_utils.isometrik_service.is_isometrik_enabled", return_value=False), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=None)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value={"id": str(uuid.uuid4())})), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=None)), \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=None)), \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(side_effect=db_operation_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account" in exc_info.value.detail
            mock_log_exception.assert_called_once()