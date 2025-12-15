"""Test module for path validators in user and organisation schemas."""

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.organisations import (
    OrganizationUpdate,
    UpdateOrganisationRequest,
)
from apps.user_service.app.schemas.users import UpdateUserRequest


class TestAvatarPathValidation:
    """Test avatar_url path validation in UpdateUserRequest and UpdateUserProfileRequest."""

    def test_valid_path_simple(self):
        """Test valid simple path."""
        request = UpdateUserRequest(avatar_url="user-123/avatar.jpg")
        assert request.avatar_url == "user-123/avatar.jpg"

    def test_valid_path_with_org(self):
        """Test valid path with organization."""
        request = UpdateUserRequest(avatar_url="house-of-apps-legal-ai/user-123/avatar.jpg")
        assert request.avatar_url == "house-of-apps-legal-ai/user-123/avatar.jpg"

    def test_valid_path_with_nested_directories(self):
        """Test valid path with nested directories."""
        request = UpdateUserRequest(avatar_url="org-456/user-123/images/avatar.png")
        assert request.avatar_url == "org-456/user-123/images/avatar.png"

    def test_valid_path_with_underscores(self):
        """Test valid path with underscores."""
        request = UpdateUserRequest(avatar_url="user_123/avatar_image.jpg")
        assert request.avatar_url == "user_123/avatar_image.jpg"

    def test_valid_path_with_hyphens(self):
        """Test valid path with hyphens."""
        request = UpdateUserRequest(avatar_url="user-123/avatar-image.jpg")
        assert request.avatar_url == "user-123/avatar-image.jpg"

    def test_none_avatar_url(self):
        """Test None avatar_url (optional field)."""
        request = UpdateUserRequest(avatar_url=None)
        assert request.avatar_url is None

    def test_empty_string_avatar_url(self):
        """Test empty string avatar_url (should be converted to None)."""
        request = UpdateUserRequest(avatar_url="")
        assert request.avatar_url is None

    def test_whitespace_only_avatar_url(self):
        """Test whitespace-only avatar_url (should be converted to None)."""
        request = UpdateUserRequest(avatar_url="   ")
        assert request.avatar_url is None

    def test_invalid_url_https(self):
        """Test that HTTPS URLs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="https://example.com/avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must be a path only, not a full url" in str(errors[0]["msg"]).lower()

    def test_invalid_url_http(self):
        """Test that HTTP URLs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="http://example.com/avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)

    def test_invalid_base64(self):
        """Test that base64 data URIs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="data:image/jpeg;base64,/9j/4AAQSkZJRg==")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must be a path only, not base64 data" in str(errors[0]["msg"]).lower()

    def test_invalid_path_no_slash(self):
        """Test invalid path without directory separator."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must be a path with at least one directory" in str(errors[0]["msg"]).lower()

    def test_invalid_path_invalid_characters(self):
        """Test invalid path with invalid characters."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="user-123/avatar@image.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "contains invalid characters" in str(errors[0]["msg"]).lower()

    def test_path_with_whitespace_trimmed(self):
        """Test path with leading/trailing whitespace is trimmed."""
        request = UpdateUserRequest(avatar_url="  user-123/avatar.jpg  ")
        assert request.avatar_url == "user-123/avatar.jpg"


class TestLogoPathValidation:
    """Test logo_url path validation in OrganizationUpdate and UpdateOrganisationRequest."""

    def test_valid_path_simple(self):
        """Test valid simple path."""
        org = OrganizationUpdate(logo_url="org-123/logo.png")
        assert org.logo_url == "org-123/logo.png"

    def test_valid_path_with_nested_directories(self):
        """Test valid path with nested directories."""
        org = OrganizationUpdate(logo_url="house-of-apps-legal-ai/org-123/branding/logo.svg")
        assert org.logo_url == "house-of-apps-legal-ai/org-123/branding/logo.svg"

    def test_none_logo_url(self):
        """Test None logo_url (optional field)."""
        org = OrganizationUpdate(logo_url=None)
        assert org.logo_url is None

    def test_empty_string_logo_url(self):
        """Test empty string logo_url (should be converted to None)."""
        org = OrganizationUpdate(logo_url="")
        assert org.logo_url is None

    def test_whitespace_only_logo_url(self):
        """Test whitespace-only logo_url (should be converted to None)."""
        org = OrganizationUpdate(logo_url="   ")
        assert org.logo_url is None

    def test_invalid_url_https(self):
        """Test that HTTPS URLs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="https://example.com/logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must be a path only, not a full url" in str(errors[0]["msg"]).lower()

    def test_invalid_url_http(self):
        """Test that HTTP URLs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="http://example.com/logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)

    def test_invalid_base64(self):
        """Test that base64 data URIs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must be a path only, not base64 data" in str(errors[0]["msg"]).lower()

    def test_invalid_path_no_slash(self):
        """Test invalid path without directory separator."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must be a path with at least one directory" in str(errors[0]["msg"]).lower()

    def test_invalid_path_invalid_characters(self):
        """Test invalid path with invalid characters."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="org-123/logo@image.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "contains invalid characters" in str(errors[0]["msg"]).lower()

    def test_update_organisation_request_valid_path(self):
        """Test UpdateOrganisationRequest with valid path."""
        org = UpdateOrganisationRequest(logo_url="house-of-apps-legal-ai/org-123/org-logo.png")
        assert org.logo_url == "house-of-apps-legal-ai/org-123/org-logo.png"

    def test_update_organisation_request_invalid_url(self):
        """Test UpdateOrganisationRequest with invalid URL."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateOrganisationRequest(logo_url="https://example.com/logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)

    def test_update_organisation_request_none_url(self):
        """Test UpdateOrganisationRequest with None logo URL."""
        org = UpdateOrganisationRequest(logo_url=None)
        assert org.logo_url is None

    def test_logo_path_with_whitespace_trimmed(self):
        """Test logo path with leading/trailing whitespace is trimmed."""
        org = OrganizationUpdate(logo_url="  org-123/logo.png  ")
        assert org.logo_url == "org-123/logo.png"

    def test_organization_update_with_multiple_fields(self):
        """Test OrganizationUpdate with multiple fields including valid logo_path."""
        org = OrganizationUpdate(
            name="Test Org",
            logo_url="house-of-apps-legal-ai/org-123/logo.png",
            industry="Technology",
        )
        assert org.logo_url == "house-of-apps-legal-ai/org-123/logo.png"
        assert org.name == "Test Org"

    def test_organization_update_with_invalid_logo_url(self):
        """Test OrganizationUpdate with multiple fields including invalid logo_url."""
        with pytest.raises(ValidationError):
            OrganizationUpdate(
                name="Test Org",
                logo_url="https://example.com/logo.png",
                industry="Technology",
            )


class TestPathValidationEdgeCases:
    """Test edge cases for path validation."""

    def test_path_with_uuid(self):
        """Test path with UUID."""
        request = UpdateUserRequest(
            avatar_url="house-of-apps-legal-ai/0abb3450-2cc8-416a-8ff7-e7de77f2825b/women_4.jpg"
        )
        assert (
            request.avatar_url
            == "house-of-apps-legal-ai/0abb3450-2cc8-416a-8ff7-e7de77f2825b/women_4.jpg"
        )

    def test_path_with_dots(self):
        """Test path with dots in filename."""
        request = UpdateUserRequest(avatar_url="user-123/image.v2.jpg")
        assert request.avatar_url == "user-123/image.v2.jpg"

    def test_path_with_multiple_slashes(self):
        """Test path with multiple directory levels."""
        request = UpdateUserRequest(avatar_url="org-123/user-456/images/avatars/profile.jpg")
        assert request.avatar_url == "org-123/user-456/images/avatars/profile.jpg"

    def test_multiple_fields_with_valid_path(self):
        """Test UpdateUserRequest with multiple fields including valid avatar_path."""
        request = UpdateUserRequest(
            full_name="Test User",
            avatar_url="house-of-apps-legal-ai/user-123/avatar.jpg",
            timezone="UTC",
        )
        assert request.avatar_url == "house-of-apps-legal-ai/user-123/avatar.jpg"
        assert request.full_name == "Test User"

    def test_multiple_fields_with_invalid_url(self):
        """Test UpdateUserRequest with multiple fields including invalid URL."""
        with pytest.raises(ValidationError):
            UpdateUserRequest(
                full_name="Test User",
                avatar_url="https://example.com/avatar.jpg",
                timezone="UTC",
            )
