# pylint: disable=all

"""
Test module for URL validators in user and organisation schemas.

This module contains comprehensive tests for URL validation in:
- UpdateUserRequest.avatar_url
- UpdateUserProfileRequest.avatar_url
- OrganizationUpdate.logo_url
- UpdateOrganisationRequest.logo_url

Author: AI Assistant
Date: 2025-01-14
"""

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.users import UpdateUserRequest
from apps.user_service.app.api.admin_management.users.update_user import UpdateUserProfileRequest
from apps.user_service.app.schemas.organisations import OrganizationUpdate, UpdateOrganisationRequest


class TestAvatarUrlValidation:
    """Test avatar_url validation in UpdateUserRequest and UpdateUserProfileRequest."""

    def test_valid_https_url(self):
        """Test valid HTTPS URL."""
        request = UpdateUserRequest(avatar_url="https://example.com/avatar.jpg")
        assert request.avatar_url == "https://example.com/avatar.jpg"

    def test_valid_http_url(self):
        """Test valid HTTP URL."""
        request = UpdateUserRequest(avatar_url="http://example.com/avatar.jpg")
        assert request.avatar_url == "http://example.com/avatar.jpg"

    def test_valid_url_with_path(self):
        """Test valid URL with path."""
        request = UpdateUserRequest(avatar_url="https://cdn.example.com/images/user/avatar.png")
        assert request.avatar_url == "https://cdn.example.com/images/user/avatar.png"

    def test_valid_url_with_query_params(self):
        """Test valid URL with query parameters."""
        request = UpdateUserRequest(avatar_url="https://example.com/avatar.jpg?size=large&v=1")
        assert request.avatar_url == "https://example.com/avatar.jpg?size=large&v=1"

    def test_valid_url_with_port(self):
        """Test valid URL with port number."""
        request = UpdateUserRequest(avatar_url="https://example.com:8080/avatar.jpg")
        assert request.avatar_url == "https://example.com:8080/avatar.jpg"

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

    def test_invalid_url_missing_scheme(self):
        """Test invalid URL without http:// or https://."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="example.com/avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must start with http:// or https://" in str(errors[0]["msg"]).lower()

    def test_invalid_url_just_filename(self):
        """Test invalid URL that's just a filename."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)

    def test_invalid_url_ftp_scheme(self):
        """Test invalid URL with FTP scheme (not allowed)."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="ftp://example.com/avatar.jpg")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must start with http:// or https://" in str(errors[0]["msg"]).lower()

    def test_invalid_url_no_netloc(self):
        """Test invalid URL without domain/host."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserRequest(avatar_url="https://")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)
        assert "must contain a valid domain or host" in str(errors[0]["msg"]).lower()

    def test_update_user_profile_request_valid_url(self):
        """Test UpdateUserProfileRequest with valid URL."""
        request = UpdateUserProfileRequest(avatar_url="https://example.com/profile.jpg")
        assert request.avatar_url == "https://example.com/profile.jpg"

    def test_update_user_profile_request_invalid_url(self):
        """Test UpdateUserProfileRequest with invalid URL."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateUserProfileRequest(avatar_url="invalid-url")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("avatar_url",)

    def test_update_user_profile_request_none_url(self):
        """Test UpdateUserProfileRequest with None URL."""
        request = UpdateUserProfileRequest(avatar_url=None)
        assert request.avatar_url is None

    def test_url_with_whitespace_trimmed(self):
        """Test URL with leading/trailing whitespace is trimmed."""
        request = UpdateUserRequest(avatar_url="  https://example.com/avatar.jpg  ")
        assert request.avatar_url == "https://example.com/avatar.jpg"


class TestLogoUrlValidation:
    """Test logo_url validation in OrganizationUpdate and UpdateOrganisationRequest."""

    def test_valid_https_logo_url(self):
        """Test valid HTTPS logo URL."""
        org = OrganizationUpdate(logo_url="https://example.com/logo.png")
        assert org.logo_url == "https://example.com/logo.png"

    def test_valid_http_logo_url(self):
        """Test valid HTTP logo URL."""
        org = OrganizationUpdate(logo_url="http://example.com/logo.png")
        assert org.logo_url == "http://example.com/logo.png"

    def test_valid_logo_url_with_path(self):
        """Test valid logo URL with path."""
        org = OrganizationUpdate(logo_url="https://cdn.example.com/branding/logo.svg")
        assert org.logo_url == "https://cdn.example.com/branding/logo.svg"

    def test_valid_logo_url_with_query_params(self):
        """Test valid logo URL with query parameters."""
        org = OrganizationUpdate(logo_url="https://example.com/logo.png?w=200&h=200")
        assert org.logo_url == "https://example.com/logo.png?w=200&h=200"

    def test_valid_logo_url_with_port(self):
        """Test valid logo URL with port number."""
        org = OrganizationUpdate(logo_url="https://example.com:443/logo.png")
        assert org.logo_url == "https://example.com:443/logo.png"

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

    def test_invalid_logo_url_missing_scheme(self):
        """Test invalid logo URL without http:// or https://."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="example.com/logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must start with http:// or https://" in str(errors[0]["msg"]).lower()

    def test_invalid_logo_url_just_filename(self):
        """Test invalid logo URL that's just a filename."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)

    def test_invalid_logo_url_ftp_scheme(self):
        """Test invalid logo URL with FTP scheme (not allowed)."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="ftp://example.com/logo.png")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must start with http:// or https://" in str(errors[0]["msg"]).lower()

    def test_invalid_logo_url_no_netloc(self):
        """Test invalid logo URL without domain/host."""
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(logo_url="https://")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)
        assert "must contain a valid domain or host" in str(errors[0]["msg"]).lower()

    def test_update_organisation_request_valid_url(self):
        """Test UpdateOrganisationRequest with valid logo URL."""
        org = UpdateOrganisationRequest(logo_url="https://example.com/org-logo.png")
        assert org.logo_url == "https://example.com/org-logo.png"

    def test_update_organisation_request_invalid_url(self):
        """Test UpdateOrganisationRequest with invalid logo URL."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateOrganisationRequest(logo_url="invalid-url")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("logo_url",)

    def test_update_organisation_request_none_url(self):
        """Test UpdateOrganisationRequest with None logo URL."""
        org = UpdateOrganisationRequest(logo_url=None)
        assert org.logo_url is None

    def test_logo_url_with_whitespace_trimmed(self):
        """Test logo URL with leading/trailing whitespace is trimmed."""
        org = OrganizationUpdate(logo_url="  https://example.com/logo.png  ")
        assert org.logo_url == "https://example.com/logo.png"

    def test_organization_update_with_multiple_fields(self):
        """Test OrganizationUpdate with multiple fields including valid logo_url."""
        org = OrganizationUpdate(
            name="Test Org",
            logo_url="https://example.com/logo.png",
            industry="Technology"
        )
        assert org.logo_url == "https://example.com/logo.png"
        assert org.name == "Test Org"

    def test_organization_update_with_invalid_logo_url(self):
        """Test OrganizationUpdate with multiple fields including invalid logo_url."""
        with pytest.raises(ValidationError):
            OrganizationUpdate(
                name="Test Org",
                logo_url="invalid-url",
                industry="Technology"
            )


class TestUrlValidationEdgeCases:
    """Test edge cases for URL validation."""

    def test_url_with_localhost(self):
        """Test URL with localhost (should be valid)."""
        request = UpdateUserRequest(avatar_url="http://localhost:3000/avatar.jpg")
        assert request.avatar_url == "http://localhost:3000/avatar.jpg"

    def test_url_with_ip_address(self):
        """Test URL with IP address (should be valid)."""
        request = UpdateUserRequest(avatar_url="https://192.168.1.1/avatar.jpg")
        assert request.avatar_url == "https://192.168.1.1/avatar.jpg"

    def test_url_with_subdomain(self):
        """Test URL with subdomain."""
        request = UpdateUserRequest(avatar_url="https://cdn.example.com/avatar.jpg")
        assert request.avatar_url == "https://cdn.example.com/avatar.jpg"

    def test_url_with_multiple_subdomains(self):
        """Test URL with multiple subdomains."""
        request = UpdateUserRequest(avatar_url="https://static.cdn.example.com/avatar.jpg")
        assert request.avatar_url == "https://static.cdn.example.com/avatar.jpg"

    def test_url_with_hash_fragment(self):
        """Test URL with hash fragment."""
        request = UpdateUserRequest(avatar_url="https://example.com/avatar.jpg#section")
        assert request.avatar_url == "https://example.com/avatar.jpg#section"

    def test_url_with_encoded_characters(self):
        """Test URL with URL-encoded characters."""
        request = UpdateUserRequest(avatar_url="https://example.com/avatar%20image.jpg")
        assert request.avatar_url == "https://example.com/avatar%20image.jpg"

    def test_url_with_unicode_characters(self):
        """Test URL with unicode characters in domain."""
        request = UpdateUserRequest(avatar_url="https://example.com/avatar.jpg")
        assert request.avatar_url == "https://example.com/avatar.jpg"

    def test_multiple_fields_with_valid_url(self):
        """Test UpdateUserRequest with multiple fields including valid avatar_url."""
        request = UpdateUserRequest(
            full_name="Test User",
            avatar_url="https://example.com/avatar.jpg",
            timezone="UTC"
        )
        assert request.avatar_url == "https://example.com/avatar.jpg"
        assert request.full_name == "Test User"

    def test_multiple_fields_with_invalid_url(self):
        """Test UpdateUserRequest with multiple fields including invalid avatar_url."""
        with pytest.raises(ValidationError):
            UpdateUserRequest(
                full_name="Test User",
                avatar_url="invalid-url",
                timezone="UTC"
            )

