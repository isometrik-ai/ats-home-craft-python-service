"""Test cases for user_utils.py module.

Tests the create_user_profile_data function with various scenarios.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from apps.user_service.app.schemas.users import PermissionInfo, RoleInfoWithDescription
from apps.user_service.app.utils.user_utils import create_user_profile_data


class TestCreateUserProfileData:
    """Test cases for create_user_profile_data function."""

    def test_create_user_profile_data_basic(self):
        """Test basic user profile data creation."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": "https://example.com/avatar.jpg",
            "phone": "+1234567890",
            "timezone": "America/New_York",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": datetime.now(timezone.utc),
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile)

        assert result.user_id == str(user_profile["user_id"])
        assert result.email == "test@example.com"
        assert result.full_name == "Test User"
        assert result.timezone == "America/New_York"
        assert result.status == "active"
        assert isinstance(result.joined_at, str)

    def test_create_user_profile_verification_preference(self):
        """Test user profile with verification preference."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
            "verification_preference": {"enabled": True, "type": "email"},
        }

        result = create_user_profile_data(user_profile)

        assert result.verification_preference is not None
        assert result.verification_preference.two_fa_enabled is True
        assert result.verification_preference.verification_method == "email"
        assert result.timezone == "UTC"  # Default when None

    def test_create_user_profile_verif_pref_invalid(self):
        """Test user profile with invalid verification preference (exception path)."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
            "verification_preference": {
                "enabled": "invalid",  # Invalid type to trigger exception
                "type": None,
            },
        }

        with patch("apps.user_service.app.utils.user_utils.logger") as mock_logger:
            result = create_user_profile_data(user_profile)

            # Should log warning and set verification_preference to None
            mock_logger.warning.assert_called_once()
            assert result.verification_preference is None

    def test_create_user_profile_verif_pref_not_dict(self):
        """Test user profile with verification preference that's not a dict."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
            "verification_preference": "not a dict",
        }

        result = create_user_profile_data(user_profile)

        # Should skip parsing and set to None
        assert result.verification_preference is None

    def test_create_user_profile_verif_pref_none(self):
        """Test user profile with None verification preference."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
            "verification_preference": None,
        }

        result = create_user_profile_data(user_profile)

        assert result.verification_preference is None

    def test_create_user_profile_joined_at_not_datetime(self):
        """Test user profile with joined_at that's not a datetime."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": "2024-01-01T00:00:00Z",  # String, not datetime
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile)

        # Should use datetime.now() when joined_at is not a datetime
        assert isinstance(result.joined_at, str)
        assert result.joined_at != "2024-01-01T00:00:00Z"

    def test_create_user_profile_data_joined_at_none(self):
        """Test user profile with None joined_at."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": None,
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile)

        # Should use datetime.now() when joined_at is None
        assert isinstance(result.joined_at, str)

    def test_create_user_profile_last_active_not_datetime(self):
        """Test user profile with last_active_at that's not a datetime."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": None,
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": "2024-01-01T00:00:00Z",  # String, not datetime
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile)

        # Should keep the string value when not a datetime
        assert result.last_active_at == "2024-01-01T00:00:00Z"

    def test_create_user_profile_role_and_permissions(self):
        """Test user profile with role and permissions."""
        role_info = RoleInfoWithDescription(role_id=str(uuid.uuid4()), description="Administrator")

        permissions = [
            PermissionInfo(
                permission_id=str(uuid.uuid4()),
                permission_code="read:users",
                permission_name="Read Users",
            )
        ]

        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": datetime.now(timezone.utc),
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(
            user_profile, role_info=role_info, permissions=permissions
        )

        assert result.role == role_info
        assert len(result.permissions) == 1
        assert result.permissions[0].permission_code == "read:users"

    def test_create_user_profile_data_no_permissions(self):
        """Test user profile with no permissions (should default to empty list)."""
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile)

        assert result.permissions == []

    def test_create_user_profile_data_custom_user_type(self):
        """Test user profile with custom user type.

        user_type parameter is passed but not stored in model.
        """
        user_profile = {
            "user_id": str(uuid.uuid4()),
            "email": "test@example.com",
            "full_name": "Test User",
            "first_name": "Test",
            "last_name": "User",
            "avatar_url": None,
            "phone": None,
            "timezone": "UTC",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
            "last_active_at": None,
            "organization_id": str(uuid.uuid4()),
            "identities": [],
            "salutation": None,
        }

        result = create_user_profile_data(user_profile, user_type="super_admin")

        # user_type is passed to function but UserProfileData model doesn't have this field
        # The function accepts it but it's not part of the returned model
        assert result.user_id == str(user_profile["user_id"])
        assert result.email == "test@example.com"
