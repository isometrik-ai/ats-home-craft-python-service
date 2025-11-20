# pylint: disable=all

"""
Test cases for helper functions in verification_codes.py.
Tests utility functions for IP sanitization, phone normalization, 
verification record validation, and ownership checking.
"""

import pytest
import uuid
import jwt
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from apps.user_service.app.api.verification_codes import (
    _update_email_or_phone,
    _get_supabase_client_with_token,
    _validate_verification_record,
    _check_verification_code_ownership,
    _verify_code_and_update_record,
    get_client_ip,
    _sanitize_ip,
    _normalize_phone,
)
from apps.user_service.app.schemas.verification_codes import (
    VerificationType,
    VerificationTrigger,
)


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Disable rate limiting for tests."""
    class DummyLimiter:
        def __init__(self, *args, **kwargs):
            self.enabled = False
            self._auto_check = False

        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

    dummy_limiter = DummyLimiter()
    with patch('apps.user_service.app.app_instance.limiter', dummy_limiter), \
         patch('apps.user_service.app.api.verification_codes.limiter', dummy_limiter):
        yield


# ============================================================================
# Helper function tests
# ============================================================================

def test_sanitize_ip_with_comma_and_spaces():
    """Test _sanitize_ip with comma and spaces - covers edge case."""
    result = _sanitize_ip("  192.168.1.1  ,  10.0.0.1  ")
    assert result == "192.168.1.1"


def test_get_client_ip_sanitized_host_fails():
    """Test get_client_ip when sanitized_host fails - covers line 122-123."""
    from fastapi import Request
    from unittest.mock import MagicMock
    
    request = MagicMock(spec=Request)
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "invalid-ip-address"
    
    result = get_client_ip(request)
    # Should return original host if sanitization fails
    assert result == "invalid-ip-address"


def test_validate_verification_record_phone_number():
    """Test _validate_verification_record with PHONE_NUMBER type - covers line 336."""
    from apps.user_service.app.schemas.verification_codes import VerifyVerificationCodeRequest
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verificationId=str(uuid.uuid4()),
        verificationCode="1111",
        phoneNumber="+1234567890"
    )
    
    record = {
        "verified": False,
        "given_input": "+1234567890",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000)
    }
    
    result = _validate_verification_record(record, data)
    assert result == "+1234567890"


def test_check_verification_code_ownership_no_user_id_in_token():
    """Test _check_verification_code_ownership when current_user has no 'sub' - covers line 377-378."""
    record = {"user_id": "user-123"}
    current_user = {"email": "test@example.com"}  # No 'sub'
    
    # Should not raise, just log warning
    _check_verification_code_ownership(record, current_user, "verification-id")


def test_check_verification_code_ownership_no_stored_user_id():
    """Test _check_verification_code_ownership when record has no user_id - covers early return."""
    record = {"user_id": None}
    current_user = {"sub": "user-123"}
    
    # Should not raise
    _check_verification_code_ownership(record, current_user, "verification-id")


def test_check_verification_code_ownership_no_current_user_id():
    """Test _check_verification_code_ownership when current_user_id is None - covers line 377."""
    record = {"user_id": "user-123"}
    current_user = {"sub": None}  # sub is None
    
    # Should not raise
    _check_verification_code_ownership(record, current_user, "verification-id")


def test_normalize_phone_with_plus():
    """Test _normalize_phone with '+' sign - covers line 181."""
    result = _normalize_phone("+1234567890")
    assert result == "1234567890"


def test_normalize_phone_without_plus():
    """Test _normalize_phone without '+' sign."""
    result = _normalize_phone("1234567890")
    assert result == "1234567890"


def test_normalize_phone_none():
    """Test _normalize_phone with None - covers line 178-179."""
    result = _normalize_phone(None)
    assert result is None


def test_normalize_phone_empty():
    """Test _normalize_phone with empty string - covers line 178-179."""
    result = _normalize_phone("")
    assert result == ""

