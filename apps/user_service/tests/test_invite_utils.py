"""Test cases for invite_utils.py module."""

import uuid
from datetime import datetime, timedelta

from apps.user_service.app.dependencies.invite_utils import (
    build_invite_list_item,
    hash_token,
)


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
            "updated_at": datetime.now().isoformat(),
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
        invite_data = {"id": str(uuid.uuid4()), "email": "test@example.com"}
        result = build_invite_list_item(invite_data)
        assert result["invite_id"] == invite_data["id"]
        assert result["email"] == invite_data["email"]
        assert result["role_id"] is None
        assert result["status"] is None
        assert result["invited_by"] is None
        assert result["expires_at"] is None
        assert result["created_at"] is None
        assert result["updated_at"] is None


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
