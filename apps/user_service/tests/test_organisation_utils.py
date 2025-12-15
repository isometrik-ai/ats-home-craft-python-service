"""Test cases for organisation utilities module.

Tests functions in organisation_utils.py module.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from postgrest import APIError

from apps.user_service.app.dependencies.organisation_utils import (
    create_organisation_with_super_admin,
    validate_organisation_status,
    validate_organization_subscription,
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


class TestCreateOrganisationWithSuperAdmin:
    """Test cases for create_organisation_with_super_admin function."""

    @pytest.mark.asyncio
    async def test_create_org_super_admin_isometrik_disabled(
        self,
    ):
        """Test successful organisation creation with super admin when Isometrik is disabled."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "name": "Test Organization",
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2", "perm-3"]
        mock_member_result = {"id": str(uuid.uuid4())}

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ) as mock_create_org,
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value=mock_role_result),
            ) as mock_create_role,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(return_value=mock_permissions),
            ) as mock_create_perms,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "assign_all_permissions_to_role"
                ),
                AsyncMock(return_value=True),
            ) as mock_assign_perms,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "add_member_to_organisation"
                ),
                AsyncMock(return_value=mock_member_result),
            ) as mock_add_member,
        ):
            await create_organisation_with_super_admin(org_data)

            # Verify all functions were called with correct arguments
            assert mock_create_org.called
            call_args = mock_create_org.call_args[0][0]
            assert call_args.get("isometrik_application_details") == {}
            mock_create_role.assert_called_once_with(org_data["organization_id"])
            mock_create_perms.assert_called_once_with(org_data["organization_id"])
            mock_assign_perms.assert_called_once_with(
                mock_role_result["id"], org_data["organization_id"]
            )

            expected_member_data = {
                "user_id": org_data["user_id"],
                "email": org_data["email"],
                "first_name": org_data["first_name"],
                "last_name": org_data["last_name"],
                "phone": org_data["phone"],
                "timezone": org_data["timezone"],
                "role_id": mock_role_result["id"],
                "status": "active",
                "role": "owner",
                "logo_url": org_data.get("logo_url", None),
            }
            mock_add_member.assert_called_once_with(
                organization_id=org_data["organization_id"],
                member_data=expected_member_data,
                isometrik_credentials={},
            )

    @pytest.mark.asyncio
    async def test_create_org_super_admin_isometrik_enabled(self):
        """Test successful organisation creation with super admin when Isometrik is enabled."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
            "name": "Test Organization",
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2", "perm-3"]
        mock_member_result = {"id": str(uuid.uuid4())}
        isometrik_response = {
            "data": {"projectId": "test-project-id", "keysetId": "test-keyset-id"}
        }

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=True,
            ),
            patch(
                "libs.shared_utils.isometrik_service.create_isometrik_application",
                AsyncMock(return_value=isometrik_response),
            ) as mock_isometrik_create,
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ) as mock_create_org,
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value=mock_role_result),
            ) as mock_create_role,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(return_value=mock_permissions),
            ) as mock_create_perms,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "assign_all_permissions_to_role"
                ),
                AsyncMock(return_value=True),
            ) as mock_assign_perms,
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "add_member_to_organisation"
                ),
                AsyncMock(return_value=mock_member_result),
            ) as mock_add_member,
        ):
            await create_organisation_with_super_admin(org_data)

            # Verify Isometrik was called first
            mock_isometrik_create.assert_called_once_with(
                organization_name="Test Organization",
                product_types=["chat", "video"],
                plan="basic",
            )

            # Verify create_new_organisation was called and check isometrik_application_details
            assert mock_create_org.called
            call_args = mock_create_org.call_args[0][0]
            assert call_args.get("isometrik_application_details") == isometrik_response["data"]

            # Verify all functions were called with correct arguments
            mock_create_role.assert_called_once_with(org_data["organization_id"])
            mock_create_perms.assert_called_once_with(org_data["organization_id"])
            mock_assign_perms.assert_called_once_with(
                mock_role_result["id"], org_data["organization_id"]
            )

            expected_member_data = {
                "user_id": org_data["user_id"],
                "email": org_data["email"],
                "first_name": org_data["first_name"],
                "last_name": org_data["last_name"],
                "phone": org_data["phone"],
                "timezone": org_data["timezone"],
                "role_id": mock_role_result["id"],
                "status": "active",
                "role": "owner",
                "logo_url": org_data.get("logo_url", None),
            }
            # Verify add_member_to_organisation was called with isometrik_credentials
            mock_add_member.assert_called_once_with(
                organization_id=org_data["organization_id"],
                member_data=expected_member_data,
                isometrik_credentials=isometrik_response["data"],
            )

    @pytest.mark.asyncio
    async def test_create_org_super_admin_isometrik_fails(self):
        """Test organisation creation fails when Isometrik is enabled but creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "name": "Test Organization",
        }

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=True,
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create Isometrik application" in exc_info.value.detail
            assert "Isometrik API error: 409 - Conflict" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_org_super_admin_isometrik_invalid(
        self,
    ):
        """Test organisation creation fails when Isometrik returns invalid response."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "name": "Test Organization",
        }

        isometrik_response = {"status": "success"}  # Missing "data" key

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=True,
            ),
            patch(
                "libs.shared_utils.isometrik_service.create_isometrik_application",
                AsyncMock(return_value=isometrik_response),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Invalid response from Isometrik API" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_org_super_admin_missing_fields(self):
        """Test organisation creation with missing optional fields."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            # Missing first_name, last_name, phone, timezone
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2"]
        mock_member_result = {"id": str(uuid.uuid4())}

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value=mock_role_result),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(return_value=mock_permissions),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "assign_all_permissions_to_role"
                ),
                AsyncMock(return_value=True),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "add_member_to_organisation"
                ),
                AsyncMock(return_value=mock_member_result),
            ) as mock_add_member,
        ):
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
                "status": "active",
                "role": "owner",
                "logo_url": org_data.get("logo_url", None),
            }
            mock_add_member.assert_called_once_with(
                organization_id=org_data["organization_id"],
                member_data=expected_member_data,
                isometrik_credentials={},
            )

    @pytest.mark.asyncio
    async def test_create_org_super_admin_db_error(self):
        """Test organisation creation with database error and cleanup."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        db_error = Exception("Database connection failed")

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(side_effect=db_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_cleanup_error(self):
        """Test organisation creation with database error and cleanup failure."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        db_error = Exception("Database connection failed")

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(side_effect=db_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_role_error(self):
        """Test organisation creation when role creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        role_error = Exception("Role creation failed")

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(side_effect=role_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_perms_error(self):
        """Test organisation creation when permissions creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        permissions_error = Exception("Permissions creation failed")

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value=mock_role_result),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(side_effect=permissions_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_member_error(self):
        """Test organisation creation when member creation fails."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "first_name": "Admin",
            "last_name": "User",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        mock_org_result = {"id": org_data["organization_id"], "name": "Test Org"}
        mock_role_result = {"id": str(uuid.uuid4()), "name": "admin"}
        mock_permissions = ["perm-1", "perm-2"]
        member_error = Exception("Member creation failed")

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=mock_org_result),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value=mock_role_result),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(return_value=mock_permissions),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "assign_all_permissions_to_role"
                ),
                AsyncMock(return_value=True),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "add_member_to_organisation"
                ),
                AsyncMock(side_effect=member_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            # Verify error handling
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account. Please try again." in str(exc_info.value.detail)

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_dup_slug(self):
        """Test organisation creation handles duplicate slug API error."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "slug": "existing-slug",
            "email": "admin@test.com",
        }

        api_error = APIError(
            {
                "message": (
                    'duplicate key value violates unique constraint "organizations_slug_key"'
                ),
                "code": "23505",
            }
        )

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(side_effect=api_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "Organisation slug already exists" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_rls_violation(self):
        """Test organisation creation handles RLS policy violation."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
            "slug": "new-org",
        }

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Row-level security policy violation" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_api_error(self):
        """Test organisation creation handles generic Supabase API error."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
        }

        api_error = APIError(
            {
                "message": "foreign key constraint violation",
                "code": "12345",
            }
        )

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=None),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(side_effect=api_error),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account" in exc_info.value.detail
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_org_super_admin_db_op_error(self):
        """Test organisation creation handles DatabaseOperationError properly."""
        org_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "email": "admin@test.com",
        }

        with (
            patch(
                "libs.shared_utils.isometrik_service.is_isometrik_enabled",
                return_value=False,
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_new_organisation"),
                AsyncMock(return_value=None),
            ),
            patch(
                ("apps.user_service.app.dependencies.organisation_utils.create_super_admin_role"),
                AsyncMock(return_value={"id": str(uuid.uuid4())}),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "create_default_permissions_for_organisation"
                ),
                AsyncMock(return_value=None),
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.organisation_utils."
                    "assign_all_permissions_to_role"
                ),
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.dependencies.organisation_utils.log_exception"
            ) as mock_log_exception,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organisation_with_super_admin(org_data)

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to create account" in exc_info.value.detail
            mock_log_exception.assert_called_once()


def _build_subscription(max_users=10, days_until_expiry=30):
    """Build a subscription dict for testing."""
    return {
        "max_users": max_users,
        "plan_type": "starter",
        "end_date": (datetime.now(timezone.utc) + timedelta(days=days_until_expiry)).isoformat(),
    }


class TestValidateOrganizationSubscription:
    """Test cases for validate_organization_subscription function."""

    @pytest.mark.asyncio
    async def test_validate_org_subscription_within_limit(self):
        """Test capacity check when within limit."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(max_users=10),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=5),
        ):
            # Should not raise exception when within limit
            try:
                await validate_organization_subscription(org_data)
            except HTTPException:
                pytest.fail("validate_organization_subscription raised HTTPException unexpectedly")

    @pytest.mark.asyncio
    async def test_validate_organization_subscription_at_limit(self):
        """Test capacity check when at limit."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(max_users=10),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=10),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "maximum user capacity" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_organization_subscription_over_limit(self):
        """Test capacity check when over limit."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(max_users=10),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=15),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "maximum user capacity" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_zero_max(self):
        """Test capacity check with zero max users."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(max_users=0),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=0),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_409_CONFLICT
            assert "maximum user capacity" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_missing_fields(self):
        """Test capacity check with missing fields."""
        org_data = {
            "id": "org-123",
            "subscription": {},
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=0),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
            assert "Unable To Check Organization Capacity" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_missing_end_date(self):
        """Ensure we raise when subscription end date is absent."""
        org_data = {
            "id": "org-123",
            "subscription": {
                "plan_type": "starter",
                "max_users": 5,
            },
        }
        with pytest.raises(HTTPException) as exc_info:
            await validate_organization_subscription(org_data)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "subscription end date" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_expired(self):
        """Expired subscriptions should block new members."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(max_users=5, days_until_expiry=-1),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=1),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "subscription has expired" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_missing_org_id(self):
        """Missing org id should trigger KeyError handler."""
        org_data = {
            "subscription": _build_subscription(),
        }
        with pytest.raises(HTTPException) as exc_info:
            await validate_organization_subscription(org_data)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Organization data is incomplete" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_invalid_date(self):
        """Invalid end date format should raise validation error."""
        org_data = {
            "id": "org-123",
            "subscription": {
                "max_users": 5,
                "plan_type": "starter",
                "end_date": "not-a-date",
            },
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(return_value=1),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
            assert "Invalid subscription data" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_validate_org_subscription_unexpected_error(self):
        """Unexpected exceptions should map to 500."""
        org_data = {
            "id": "org-123",
            "subscription": _build_subscription(),
        }
        with patch(
            "apps.user_service.app.dependencies.organisation_utils.get_organisation_members_count",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await validate_organization_subscription(org_data)
            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Unable to verify organization capacity" in exc_info.value.detail
