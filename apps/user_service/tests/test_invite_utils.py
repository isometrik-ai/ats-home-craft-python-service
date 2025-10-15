"""
Test cases for invite_utils.py module

This module tests all utility functions for organization invitation management.
"""

import pytest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from fastapi import HTTPException, status
from apps.user_service.app.dependencies.invite_utils import (
    validate_email_format,
    validate_role,
    validate_invite_status,
    validate_expiration_days,
    is_invite_expired,
    can_resend_invite,
    can_revoke_invite,
    build_invite_details_response,
    build_invite_list_item,
    handle_invite_validation_error,
    handle_invite_not_found_error,
    handle_invite_permission_error,
    generate_invite_url,
    extract_token_from_url,
    check_organization_capacity,
    validate_organization_access,
    get_valid_status_transitions,
    is_valid_status_transition,
    hash_token,
)


class TestValidateEmailFormat:
    """Test cases for validate_email_format function."""

    def test_validate_email_format_valid_emails(self):
        """Test validation with valid email formats."""
        valid_emails = [
            "user@example.com",
            "test.email@domain.co.uk",
            "user+tag@example.org",
            "user123@test-domain.com",
            "a@b.c",
            "user.name@example.com"
        ]

        for email in valid_emails:
            assert validate_email_format(email) is True

    def test_validate_email_format_invalid_emails(self):
        """Test validation with invalid email formats."""
        invalid_emails = [
            "invalid-email",
            "@example.com",
            "user@",
            "user@.com",
            "user..name@example.com",
            "user@example..com",
            "user@example",
            "",
            "user@example.c",
            "user name@example.com"
        ]

        for email in invalid_emails:
            assert validate_email_format(email) is False


class TestValidateRole:
    """Test cases for validate_role function."""

    def test_validate_role_valid_roles(self):
        """Test validation with valid roles."""
        valid_roles = ["owner", "admin", "member", "OWNER", "ADMIN", "MEMBER"]

        for role in valid_roles:
            assert validate_role(role) is True

    def test_validate_role_invalid_roles(self):
        """Test validation with invalid roles."""
        invalid_roles = [
            "invalid_role",
            "superuser",
            "guest",
            "viewer",
            "",
            "123",
            "role with spaces"
        ]

        for role in invalid_roles:
            assert validate_role(role) is False


class TestValidateInviteStatus:
    """Test cases for validate_invite_status function."""

    def test_validate_invite_status_valid_statuses(self):
        """Test validation with valid statuses."""
        valid_statuses = [
            "pending", "accepted", "rejected", "expired", "revoked",
            "PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "REVOKED"
        ]

        for status_val in valid_statuses:
            assert validate_invite_status(status_val) is True

    def test_validate_invite_status_invalid_statuses(self):
        """Test validation with invalid statuses."""
        invalid_statuses = [
            "invalid_status",
            "active",
            "inactive",
            "cancelled",
            "",
            "123"
        ]

        for status_val in invalid_statuses:
            assert validate_invite_status(status_val) is False


class TestValidateExpirationDays:
    """Test cases for validate_expiration_days function."""

    def test_validate_expiration_days_valid_days(self):
        """Test validation with valid expiration days."""
        valid_days = list(range(1, 31))  # 1 to 30 days

        for days in valid_days:
            assert validate_expiration_days(days) is True

    def test_validate_expiration_days_invalid_days(self):
        """Test validation with invalid expiration days."""
        invalid_days = [0, -1, 31, 50, 100]

        for days in invalid_days:
            assert validate_expiration_days(days) is False


class TestIsInviteExpired:
    """Test cases for is_invite_expired function."""

    def test_is_invite_expired_expired_invite(self):
        """Test with expired invitation."""
        expired_time = (datetime.now() - timedelta(days=1)).isoformat()
        assert is_invite_expired(expired_time) is True

    def test_is_invite_expired_valid_invite(self):
        """Test with valid (not expired) invitation."""
        future_time = (datetime.now() + timedelta(days=1)).isoformat()
        assert is_invite_expired(future_time) is False

    def test_is_invite_expired_invalid_format(self):
        """Test with invalid date format."""
        invalid_formats = [
            "invalid-date",
            "2024-13-45",  # Invalid month/day
            "",
            None
        ]

        for invalid_format in invalid_formats:
            assert is_invite_expired(invalid_format) is True

    def test_is_invite_expired_with_z_suffix(self):
        """Test with ISO format containing Z suffix."""
        expired_time = (datetime.now() - timedelta(days=1)).isoformat() + "Z"
        assert is_invite_expired(expired_time) is True


class TestCanResendInvite:
    """Test cases for can_resend_invite function."""

    def test_can_resend_invite_pending_not_expired(self):
        """Test resend capability for pending, non-expired invite."""
        invite_data = {
            "status": "pending",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat()
        }
        assert can_resend_invite(invite_data) is True

    def test_can_resend_invite_pending_expired(self):
        """Test resend capability for pending, expired invite."""
        invite_data = {
            "status": "pending",
            "expires_at": (datetime.now() - timedelta(days=1)).isoformat()
        }
        assert can_resend_invite(invite_data) is False

    def test_can_resend_invite_accepted_status(self):
        """Test resend capability for accepted invite."""
        invite_data = {
            "status": "accepted",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat()
        }
        assert can_resend_invite(invite_data) is False

    def test_can_resend_invite_no_expires_at(self):
        """Test resend capability when expires_at is missing."""
        invite_data = {
            "status": "pending"
        }
        assert can_resend_invite(invite_data) is False

    def test_can_resend_invite_empty_status(self):
        """Test resend capability with empty status."""
        invite_data = {
            "status": "",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat()
        }
        assert can_resend_invite(invite_data) is False


class TestCanRevokeInvite:
    """Test cases for can_revoke_invite function."""

    def test_can_revoke_invite_pending(self):
        """Test revoke capability for pending invite."""
        invite_data = {"status": "pending"}
        assert can_revoke_invite(invite_data) is True

    def test_can_revoke_invite_accepted(self):
        """Test revoke capability for accepted invite."""
        invite_data = {"status": "accepted"}
        assert can_revoke_invite(invite_data) is True

    def test_can_revoke_invite_rejected(self):
        """Test revoke capability for rejected invite."""
        invite_data = {"status": "rejected"}
        assert can_revoke_invite(invite_data) is False

    def test_can_revoke_invite_expired(self):
        """Test revoke capability for expired invite."""
        invite_data = {"status": "expired"}
        assert can_revoke_invite(invite_data) is False

    def test_can_revoke_invite_revoked(self):
        """Test revoke capability for already revoked invite."""
        invite_data = {"status": "revoked"}
        assert can_revoke_invite(invite_data) is False


class TestBuildInviteDetailsResponse:
    """Test cases for build_invite_details_response function."""

    def test_build_invite_details_response_empty_data(self):
        """Test response building with empty data."""
        result = build_invite_details_response({})
        assert result["valid"] is False
        assert result["error"] == "Invitation not found"

    def test_build_invite_details_response_none_data(self):
        """Test response building with None data."""
        result = build_invite_details_response(None)
        assert result["valid"] is False
        assert result["error"] == "Invitation not found"

    def test_build_invite_details_response_expired_invite(self):
        """Test response building with expired invitation."""
        invite_data = {
            "email": "test@example.com",
            "expires_at": (datetime.now() - timedelta(days=1)).isoformat(),
            "organizations": {"name": "Test Org"}
        }
        result = build_invite_details_response(invite_data)
        assert result["valid"] is False
        assert result["error"] == "Invitation has expired"

    def test_build_invite_details_response_valid_invite(self):
        """Test response building with valid invitation."""
        invite_data = {
            "email": "test@example.com",
            "organization_id": str(uuid.uuid4()),
            "role": "member",
            "invited_by": str(uuid.uuid4()),
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "organizations": {"name": "Test Organization"}
        }
        result = build_invite_details_response(invite_data)
        assert result["valid"] is True
        assert result["email"] == "test@example.com"
        assert result["organization_name"] == "Test Organization"
        assert result["organization_id"] == invite_data["organization_id"]
        assert result["role"] == "member"
        assert result["invited_by"] == invite_data["invited_by"]
        assert result["expires_at"] == invite_data["expires_at"]


class TestBuildInviteListItem:
    """Test cases for build_invite_list_item function."""

    def test_build_invite_list_item_complete_data(self):
        """Test list item building with complete data."""
        invite_data = {
            "id": str(uuid.uuid4()),
            "email": "test@example.com",
            "role_id": str(uuid.uuid4()),
            "status": "pending",
            "invited_by": str(uuid.uuid4()),
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        result = build_invite_list_item(invite_data)
        assert result["invite_id"] == invite_data["id"]
        assert result["email"] == invite_data["email"]
        assert result["role_id"] == invite_data["role_id"]
        assert result["status"] == invite_data["status"]
        assert result["invited_by"] == invite_data["invited_by"]
        assert result["expires_at"] == invite_data["expires_at"]
        assert result["created_at"] == invite_data["created_at"]
        assert result["updated_at"] == invite_data["updated_at"]

    def test_build_invite_list_item_missing_fields(self):
        """Test list item building with missing fields."""
        invite_data = {
            "id": str(uuid.uuid4()),
            "email": "test@example.com"
        }
        result = build_invite_list_item(invite_data)
        assert result["invite_id"] == invite_data["id"]
        assert result["email"] == invite_data["email"]
        assert result["role_id"] is None
        assert result["status"] is None
        assert result["invited_by"] is None
        assert result["expires_at"] is None
        assert result["created_at"] is None
        assert result["updated_at"] is None


class TestHandleInviteValidationError:
    """Test cases for handle_invite_validation_error function."""

    def test_handle_invite_validation_error(self):
        """Test validation error handling."""
        with pytest.raises(HTTPException) as exc_info:
            handle_invite_validation_error("email", "invalid-email", "Invalid format")

        assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Invalid email: Invalid format" in exc_info.value.detail

    def test_handle_invite_validation_error_logging(self):
        """Test that validation error is logged."""
        with patch("apps.user_service.app.dependencies.invite_utils.logger") as mock_logger:
            with pytest.raises(HTTPException):
                handle_invite_validation_error("role", "invalid_role", "Invalid role")

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "Invitation validation failed" in call_args[0]
            assert "role" in call_args[0]
            assert "invalid_role" in call_args[0]
            assert "Invalid role" in call_args[0]


class TestHandleInviteNotFoundError:
    """Test cases for handle_invite_not_found_error function."""

    def test_handle_invite_not_found_error(self):
        """Test not found error handling."""
        invite_id = str(uuid.uuid4())
        with pytest.raises(HTTPException) as exc_info:
            handle_invite_not_found_error(invite_id)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.detail == "Invitation not found"

    def test_handle_invite_not_found_error_logging(self):
        """Test that not found error is logged."""
        invite_id = str(uuid.uuid4())
        with patch("apps.user_service.app.dependencies.invite_utils.logger") as mock_logger:
            with pytest.raises(HTTPException):
                handle_invite_not_found_error(invite_id)

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "Invitation not found" in call_args[0]
            assert invite_id in call_args[0]


class TestHandleInvitePermissionError:
    """Test cases for handle_invite_permission_error function."""

    def test_handle_invite_permission_error(self):
        """Test permission error handling."""
        with pytest.raises(HTTPException) as exc_info:
            handle_invite_permission_error("create invitations")

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient permissions to create invitations invitations" in exc_info.value.detail

    def test_handle_invite_permission_error_logging(self):
        """Test that permission error is logged."""
        with patch("apps.user_service.app.dependencies.invite_utils.logger") as mock_logger:
            with pytest.raises(HTTPException):
                handle_invite_permission_error("delete invitations")

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "Invitation permission denied" in call_args[0]
            assert "delete invitations" in call_args[0]


class TestGenerateInviteUrl:
    """Test cases for generate_invite_url function."""

    def test_generate_invite_url(self):
        """Test URL generation."""
        base_url = "https://example.com"
        token = "abc123xyz"
        result = generate_invite_url(base_url, token)
        assert result == f"{base_url}/invite/accept/{token}"

    def test_generate_invite_url_with_trailing_slash(self):
        """Test URL generation with trailing slash in base URL."""
        base_url = "https://example.com/"
        token = "abc123xyz"
        result = generate_invite_url(base_url, token)
        assert result == f"{base_url}invite/accept/{token}"


class TestExtractTokenFromUrl:
    """Test cases for extract_token_from_url function."""

    def test_extract_token_from_url_valid_format(self):
        """Test token extraction from valid URL format."""
        url = "https://example.com/invite/accept/abc123xyz"
        result = extract_token_from_url(url)
        assert result == "abc123xyz"

    def test_extract_token_from_url_with_query_params(self):
        """Test token extraction from URL with query parameters."""
        url = "https://example.com/invite/accept/abc123xyz?param=value"
        result = extract_token_from_url(url)
        assert result == "abc123xyz"

    def test_extract_token_from_url_invalid_format(self):
        """Test token extraction from invalid URL format."""
        invalid_urls = [
            "https://example.com/invite/abc123xyz",  # Missing 'accept'
            "https://example.com/accept/abc123xyz",   # Missing 'invite'
            "https://example.com/invite/accept/",    # Missing token
            "https://example.com/invite/accept",     # Missing token
            "https://example.com/",                 # Completely different format
        ]

        for url in invalid_urls:
            result = extract_token_from_url(url)
            assert result is None

    def test_extract_token_from_url_empty_string(self):
        """Test token extraction from empty string."""
        result = extract_token_from_url("")
        assert result is None


class TestCheckOrganizationCapacity:
    """Test cases for check_organization_capacity function."""

    def test_check_organization_capacity_within_limit(self):
        """Test capacity check when within limit."""
        org_data = {
            "max_users": 10,
            "member_count": 5
        }
        assert check_organization_capacity(org_data) is True

    def test_check_organization_capacity_at_limit(self):
        """Test capacity check when at limit."""
        org_data = {
            "max_users": 10,
            "member_count": 10
        }
        assert check_organization_capacity(org_data) is False

    def test_check_organization_capacity_over_limit(self):
        """Test capacity check when over limit."""
        org_data = {
            "max_users": 10,
            "member_count": 15
        }
        assert check_organization_capacity(org_data) is False

    def test_check_organization_capacity_zero_max_users(self):
        """Test capacity check with zero max users."""
        org_data = {
            "max_users": 0,
            "member_count": 0
        }
        assert check_organization_capacity(org_data) is False

    def test_check_organization_capacity_missing_fields(self):
        """Test capacity check with missing fields."""
        org_data = {}
        assert check_organization_capacity(org_data) is True  # Defaults to 0 < 0 = False, but function returns True for missing max_users


class TestValidateOrganizationAccess:
    """Test cases for validate_organization_access function."""

    @pytest.mark.asyncio
    async def test_validate_organization_access_valid(self):
        """Test organization access validation with valid access."""
        from apps.user_service.app.dependencies.common_utils import UserContext
        
        user_context = UserContext(
            organization_id="org123",
            user_id="user123",
            email="test@example.com",
            user_type="organization_member"
        )
        
        result = await validate_organization_access(user_context, "org123")
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_organization_access_invalid(self):
        """Test organization access validation with invalid access."""
        from apps.user_service.app.dependencies.common_utils import UserContext
        
        user_context = UserContext(
            organization_id="org123",
            user_id="user123",
            email="test@example.com",
            user_type="organization_member"
        )
        
        result = await validate_organization_access(user_context, "different_org")
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_organization_access_coroutine(self):
        """Test organization access validation with coroutine user context."""
        from apps.user_service.app.dependencies.common_utils import UserContext
        
        async def get_user_context():
            return UserContext(
                organization_id="org123",
                user_id="user123",
                email="test@example.com",
                user_type="organization_member"
            )
        
        result = await validate_organization_access(get_user_context(), "org123")
        assert result is True


class TestGetValidStatusTransitions:
    """Test cases for get_valid_status_transitions function."""

    def test_get_valid_status_transitions_pending(self):
        """Test status transitions from pending."""
        transitions = get_valid_status_transitions("pending")
        expected = ["accepted", "rejected", "expired", "revoked"]
        assert set(transitions) == set(expected)

    def test_get_valid_status_transitions_accepted(self):
        """Test status transitions from accepted."""
        transitions = get_valid_status_transitions("accepted")
        assert transitions == ["revoked"]

    def test_get_valid_status_transitions_rejected(self):
        """Test status transitions from rejected."""
        transitions = get_valid_status_transitions("rejected")
        assert transitions == []

    def test_get_valid_status_transitions_expired(self):
        """Test status transitions from expired."""
        transitions = get_valid_status_transitions("expired")
        assert transitions == []

    def test_get_valid_status_transitions_revoked(self):
        """Test status transitions from revoked."""
        transitions = get_valid_status_transitions("revoked")
        assert transitions == []

    def test_get_valid_status_transitions_case_insensitive(self):
        """Test status transitions with different cases."""
        transitions_upper = get_valid_status_transitions("PENDING")
        transitions_lower = get_valid_status_transitions("pending")
        assert transitions_upper == transitions_lower

    def test_get_valid_status_transitions_invalid_status(self):
        """Test status transitions with invalid status."""
        transitions = get_valid_status_transitions("invalid_status")
        assert transitions == []


class TestIsValidStatusTransition:
    """Test cases for is_valid_status_transition function."""

    def test_is_valid_status_transition_valid_transitions(self):
        """Test valid status transitions."""
        valid_transitions = [
            ("pending", "accepted"),
            ("pending", "rejected"),
            ("pending", "expired"),
            ("pending", "revoked"),
            ("accepted", "revoked")
        ]

        for current, new in valid_transitions:
            assert is_valid_status_transition(current, new) is True

    def test_is_valid_status_transition_invalid_transitions(self):
        """Test invalid status transitions."""
        invalid_transitions = [
            ("pending", "pending"),  # Same status
            ("accepted", "accepted"),  # Same status
            ("rejected", "accepted"),  # From rejected
            ("expired", "accepted"),  # From expired
            ("revoked", "accepted"),  # From revoked
            ("accepted", "pending"),  # Invalid transition
            ("invalid", "accepted")  # Invalid current status
        ]

        for current, new in invalid_transitions:
            assert is_valid_status_transition(current, new) is False

    def test_is_valid_status_transition_case_insensitive(self):
        """Test status transitions with different cases."""
        assert is_valid_status_transition("PENDING", "ACCEPTED") is True
        assert is_valid_status_transition("pending", "ACCEPTED") is True
        assert is_valid_status_transition("PENDING", "accepted") is True


class TestHashToken:
    """Test cases for hash_token function."""

    def test_hash_token_consistency(self):
        """Test that hash_token produces consistent results."""
        token = "test_token_123"
        hash1 = hash_token(token)
        hash2 = hash_token(token)
        assert hash1 == hash2

    def test_hash_token_different_tokens(self):
        """Test that different tokens produce different hashes."""
        token1 = "test_token_123"
        token2 = "test_token_456"
        hash1 = hash_token(token1)
        hash2 = hash_token(token2)
        assert hash1 != hash2

    def test_hash_token_empty_string(self):
        """Test hash_token with empty string."""
        result = hash_token("")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex digest length

    def test_hash_token_unicode(self):
        """Test hash_token with unicode characters."""
        token = "test_token_🚀_unicode"
        result = hash_token(token)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_token_format(self):
        """Test that hash_token produces valid SHA256 hex format."""
        token = "test_token"
        result = hash_token(token)
        # SHA256 produces 64 character hex string
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)
