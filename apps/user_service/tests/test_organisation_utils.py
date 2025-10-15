# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException, status

from apps.user_service.app.dependencies.organisation_utils import (
    validate_organisation_status,
    validate_organisation_name_filter,
    build_organisation_filter_message,
    create_organisation_with_super_admin
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
    async def test_create_organisation_with_super_admin_success(self):
        """Test successful organisation creation with super admin."""
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
        mock_permissions = ["perm-1", "perm-2", "perm-3"]
        mock_member_result = {"id": str(uuid.uuid4())}

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)) as mock_create_org, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)) as mock_create_role, \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)) as mock_create_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)) as mock_assign_perms, \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(return_value=mock_member_result)) as mock_add_member:

            result = await create_organisation_with_super_admin(org_data)

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

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
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

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(side_effect=db_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.delete_auth_user", AsyncMock(return_value=True)) as mock_delete_user, \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            # Verify cleanup was attempted
            mock_delete_user.assert_called_once_with(org_data["user_id"])
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
        cleanup_error = Exception("Cleanup failed")

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(side_effect=db_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.delete_auth_user", AsyncMock(side_effect=cleanup_error)) as mock_delete_user, \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            # Verify cleanup was attempted
            mock_delete_user.assert_called_once_with(org_data["user_id"])
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

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(side_effect=role_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.delete_auth_user", AsyncMock(return_value=True)) as mock_delete_user, \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            # Verify cleanup was attempted
            mock_delete_user.assert_called_once_with(org_data["user_id"])
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

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(side_effect=permissions_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.delete_auth_user", AsyncMock(return_value=True)) as mock_delete_user, \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            # Verify cleanup was attempted
            mock_delete_user.assert_called_once_with(org_data["user_id"])
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

        with patch("apps.user_service.app.dependencies.organisation_utils.create_new_organisation", AsyncMock(return_value=mock_org_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role", AsyncMock(return_value=mock_role_result)), \
             patch("apps.user_service.app.dependencies.organisation_utils.create_default_permissions_for_organisation", AsyncMock(return_value=mock_permissions)), \
             patch("apps.user_service.app.dependencies.organisation_utils.assign_all_permissions_to_role", AsyncMock(return_value=True)), \
             patch("apps.user_service.app.dependencies.organisation_utils.add_member_to_organisation", AsyncMock(side_effect=member_error)), \
             patch("apps.user_service.app.dependencies.organisation_utils.delete_auth_user", AsyncMock(return_value=True)) as mock_delete_user, \
             patch("apps.user_service.app.dependencies.organisation_utils.log_exception") as mock_log_exception:

            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            # Verify cleanup was attempted
            mock_delete_user.assert_called_once_with(org_data["user_id"])
            mock_log_exception.assert_called_once()
