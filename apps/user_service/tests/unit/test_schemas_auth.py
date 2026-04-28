"""Unit tests for auth schemas."""

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.auth import ResetPasswordRequest, SignupRequest


def test_signup_request_password_too_short():
    """Test that signup request validates password length."""
    with pytest.raises(ValidationError):
        SignupRequest(
            email="user@example.com",
            password="123",
            first_name="Test",
            last_name="User",
            verification_id="vid",
            verification_code="123456",
        )


def test_signup_request_valid():
    """Test that valid signup request is accepted."""
    obj = SignupRequest(
        email="user@example.com",
        password="StrongPass123!",
        first_name="Test",
        last_name="User",
        verification_id="vid",
        verification_code="123456",
    )
    assert obj.email == "user@example.com"


def test_reset_password_min_length():
    """Test that reset password request accepts minimum length password."""
    obj = ResetPasswordRequest(access_token="atk", refresh_token="rtk", new_password="123456")
    assert obj.new_password == "123456"


def test_reset_password_valid():
    """Test that valid reset password request is accepted."""
    obj = ResetPasswordRequest(
        access_token="atk",
        refresh_token="rtk",
        new_password="StrongPass123!",
    )
    assert obj.access_token == "atk"
