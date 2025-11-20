# pylint: disable=all

"""
Comprehensive test cases for verification_codes.py to increase code coverage.
Tests helper functions, validation functions, and endpoint edge cases.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from apps.user_service.app.api.verification_codes import (
    _validate_email_for_update,
    _validate_phone_for_update,
    _check_phone_exists_for_other_user,
    _check_auth_user_exists_by_phone,
    _determine_triggered_text,
    _validate_authenticated_user_input,
    get_optional_user,
    _sanitize_ip,
    get_client_ip,
    send_verification_code,
    verify_verification_code,
    router,
)
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerifyVerificationCodeRequest,
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
# Tests for _validate_email_for_update
# ============================================================================

@pytest.mark.asyncio
async def test_validate_email_for_update_same_email():
    """Test _validate_email_for_update when email is same as current."""
    with pytest.raises(HTTPException) as exc_info:
        await _validate_email_for_update("test@example.com", "user-123", "test@example.com")
    assert exc_info.value.status_code == 400
    assert "same as your current email" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_email_for_update_email_exists_for_other_user():
    """Test _validate_email_for_update when email exists for another user."""
    mock_user = MagicMock()
    mock_user.id = "other-user-456"
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email', 
               AsyncMock(return_value=mock_user)):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_email_for_update("existing@example.com", "user-123", "current@example.com")
        assert exc_info.value.status_code == 409
        assert "already registered with another account" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_email_for_update_email_exists_for_same_user():
    """Test _validate_email_for_update when email exists for same user (should pass)."""
    mock_user = MagicMock()
    mock_user.id = "user-123"
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email', 
               AsyncMock(return_value=mock_user)):
        # Should not raise
        await _validate_email_for_update("existing@example.com", "user-123", "current@example.com")


@pytest.mark.asyncio
async def test_validate_email_for_update_get_user_fails():
    """Test _validate_email_for_update when get_auth_user_by_email fails."""
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email', 
               AsyncMock(side_effect=Exception("Database error"))):
        # Should not raise, just log warning
        await _validate_email_for_update("new@example.com", "user-123", "current@example.com")


@pytest.mark.asyncio
async def test_validate_email_for_update_email_not_found():
    """Test _validate_email_for_update when email doesn't exist."""
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email', 
               AsyncMock(return_value=None)):
        # Should not raise
        await _validate_email_for_update("new@example.com", "user-123", "current@example.com")


# ============================================================================
# Tests for _check_phone_exists_for_other_user
# ============================================================================

@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_phone_exists():
    """Test _check_phone_exists_for_other_user when phone exists for another user."""
    mock_user1 = MagicMock()
    mock_user1.id = "user-123"
    mock_user1.phone = "+1234567890"
    
    mock_user2 = MagicMock()
    mock_user2.id = "other-user-456"
    mock_user2.phone = "+1234567890"
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user1, mock_user2])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        with pytest.raises(HTTPException) as exc_info:
            await _check_phone_exists_for_other_user("+1234567890", "user-123")
        assert exc_info.value.status_code == 409
        assert "already registered with another account" in exc_info.value.detail


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_phone_not_exists():
    """Test _check_phone_exists_for_other_user when phone doesn't exist for others."""
    mock_user1 = MagicMock()
    mock_user1.id = "user-123"
    mock_user1.phone = "+1234567890"
    
    mock_user2 = MagicMock()
    mock_user2.id = "other-user-456"
    mock_user2.phone = "+9876543210"
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user1, mock_user2])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        # Should not raise
        await _check_phone_exists_for_other_user("+1111111111", "user-123")


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_phone_normalized():
    """Test _check_phone_exists_for_other_user with normalized phone (no + sign)."""
    mock_user1 = MagicMock()
    mock_user1.id = "user-123"
    mock_user1.phone = "1234567890"  # No + sign
    
    mock_user2 = MagicMock()
    mock_user2.id = "other-user-456"
    mock_user2.phone = "1234567890"
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user1, mock_user2])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        with pytest.raises(HTTPException) as exc_info:
            await _check_phone_exists_for_other_user("+1234567890", "user-123")  # Input has +
        assert exc_info.value.status_code == 409


# ============================================================================
# Tests for _check_auth_user_exists_by_phone
# ============================================================================

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_exists():
    """Test _check_auth_user_exists_by_phone when phone exists."""
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is True


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_not_exists():
    """Test _check_auth_user_exists_by_phone when phone doesn't exist."""
    mock_user = MagicMock()
    mock_user.phone = "+9876543210"
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_exception():
    """Test _check_auth_user_exists_by_phone when exception occurs."""
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(side_effect=Exception("Database error"))):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False


# ============================================================================
# Tests for _validate_phone_for_update
# ============================================================================

@pytest.mark.asyncio
async def test_validate_phone_for_update_same_phone():
    """Test _validate_phone_for_update when phone is same as current."""
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_user_data.user = mock_user
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_phone_for_update("+1234567890", "user-123")
        assert exc_info.value.status_code == 400
        assert "same as your current phone number" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_phone_for_update_different_phone():
    """Test _validate_phone_for_update when phone is different."""
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_user_data.user = mock_user
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        # Should not raise
        await _validate_phone_for_update("+9876543210", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_get_user_fails():
    """Test _validate_phone_for_update when get_user_by_id fails."""
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(side_effect=Exception("Database error"))):
        # Should not raise, just log warning
        await _validate_phone_for_update("+9876543210", "user-123")


# ============================================================================
# Tests for _determine_triggered_text
# ============================================================================

def test_determine_triggered_text_authenticated_email():
    """Test _determine_triggered_text for authenticated email."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    current_user = {"sub": "user-123"}
    result = _determine_triggered_text(data, current_user)
    assert result == VerificationTrigger.EMAIL_UPDATE.value


def test_determine_triggered_text_authenticated_phone():
    """Test _determine_triggered_text for authenticated phone."""
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890")
    current_user = {"sub": "user-123"}
    result = _determine_triggered_text(data, current_user)
    assert result == VerificationTrigger.PHONE_NUMBER_UPDATE.value


def test_determine_triggered_text_unauthenticated_email():
    """Test _determine_triggered_text for unauthenticated email."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    result = _determine_triggered_text(data, None)
    assert result == VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value


def test_determine_triggered_text_unauthenticated_phone():
    """Test _determine_triggered_text for unauthenticated phone."""
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890")
    result = _determine_triggered_text(data, None)
    assert result == VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value


# ============================================================================
# Tests for _validate_authenticated_user_input
# ============================================================================

@pytest.mark.asyncio
async def test_validate_authenticated_user_input_email():
    """Test _validate_authenticated_user_input for email."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"sub": "user-123", "email": "current@example.com"}
    
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.email = "current@example.com"
    mock_user_data.user = mock_user
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        assert triggered_text == VerificationTrigger.EMAIL_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_phone():
    """Test _validate_authenticated_user_input for phone."""
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+9876543210")
    current_user = {"sub": "user-123"}
    
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_user_data.user = mock_user
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        assert triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_get_user_fails():
    """Test _validate_authenticated_user_input when get_user_by_id fails."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"sub": "user-123", "email": "current@example.com"}
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(side_effect=Exception("Database error"))), \
         patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        assert triggered_text == VerificationTrigger.EMAIL_UPDATE.value


# ============================================================================
# Tests for get_optional_user
# ============================================================================

def test_get_optional_user_with_user():
    """Test get_optional_user when user exists in request state."""
    mock_request = MagicMock(spec=Request)
    mock_user = {"sub": "user-123", "email": "test@example.com"}
    mock_request.state.user = mock_user
    
    with patch('apps.user_service.app.api.verification_codes.get_user_from_auth',
               return_value=mock_user):
        result = get_optional_user(mock_request)
        assert result == mock_user


def test_get_optional_user_no_user_in_state():
    """Test get_optional_user when user doesn't exist in request state."""
    mock_request = MagicMock(spec=Request)
    del mock_request.state.user
    
    result = get_optional_user(mock_request)
    assert result is None


def test_get_optional_user_auth_fails():
    """Test get_optional_user when get_user_from_auth fails."""
    mock_request = MagicMock(spec=Request)
    mock_request.state.user = {"sub": "user-123"}
    
    with patch('apps.user_service.app.api.verification_codes.get_user_from_auth',
               side_effect=Exception("Auth error")):
        result = get_optional_user(mock_request)
        assert result is None


# ============================================================================
# Tests for _sanitize_ip
# ============================================================================

def test_sanitize_ip_valid_ipv4():
    """Test _sanitize_ip with valid IPv4."""
    result = _sanitize_ip("192.168.1.1")
    assert result == "192.168.1.1"


def test_sanitize_ip_valid_ipv6():
    """Test _sanitize_ip with valid IPv6."""
    result = _sanitize_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
    assert result == "2001:0db8:85a3:0000:0000:8a2e:0370:7334"


def test_sanitize_ip_none():
    """Test _sanitize_ip with None."""
    result = _sanitize_ip(None)
    assert result is None


def test_sanitize_ip_empty_string():
    """Test _sanitize_ip with empty string."""
    result = _sanitize_ip("")
    assert result is None


def test_sanitize_ip_invalid_ip():
    """Test _sanitize_ip with invalid IP."""
    result = _sanitize_ip("invalid-ip")
    assert result is None


# ============================================================================
# Tests for get_client_ip
# ============================================================================

def test_get_client_ip_from_x_forwarded_for():
    """Test get_client_ip when X-Forwarded-For header exists."""
    mock_request = MagicMock(spec=Request)
    # Mock headers.get to return the value for X-Forwarded-For
    def header_get(key, default=None):
        if key == "X-Forwarded-For":
            return "192.168.1.1"
        return default
    mock_request.headers.get = header_get
    mock_request.client = None
    
    result = get_client_ip(mock_request)
    assert result == "192.168.1.1"


def test_get_client_ip_from_x_real_ip():
    """Test get_client_ip when X-Real-IP header exists."""
    mock_request = MagicMock(spec=Request)
    # Mock headers.get to return None for X-Forwarded-For, then value for X-Real-IP
    def header_get(key, default=None):
        if key == "X-Forwarded-For":
            return None
        if key == "X-Real-IP":
            return "10.0.0.1"
        return default
    mock_request.headers.get = header_get
    mock_request.client = None
    
    result = get_client_ip(mock_request)
    assert result == "10.0.0.1"


def test_get_client_ip_from_client_host():
    """Test get_client_ip when using client.host."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_client = MagicMock()
    mock_client.host = "192.168.1.1"
    mock_request.client = mock_client
    
    result = get_client_ip(mock_request)
    assert result == "192.168.1.1"


def test_get_client_ip_no_client():
    """Test get_client_ip when client is None."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = None
    
    result = get_client_ip(mock_request)
    assert result == "unknown"


# ============================================================================
# Tests for send_verification_code endpoint
# ============================================================================

@pytest.mark.asyncio
async def test_send_verification_code_unauthenticated_phone_exists():
    """Test send_verification_code unauthenticated when phone already exists."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890")
    
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('apps.user_service.app.api.verification_codes.create_verification_code',
               AsyncMock(return_value={"id": "verification-123", "expiry_at": 1234567890})):
        with pytest.raises(HTTPException) as exc_info:
            await send_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        assert "already registered" in exc_info.value.detail


@pytest.mark.asyncio
async def test_send_verification_code_unauthenticated_email_exists():
    """Test send_verification_code unauthenticated when email already exists."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="existing@example.com")
    
    mock_user = MagicMock()
    mock_user.email = "existing@example.com"
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=mock_user)):
        with pytest.raises(HTTPException) as exc_info:
            await send_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        assert "already registered" in exc_info.value.detail


@pytest.mark.asyncio
async def test_send_verification_code_authenticated_phone():
    """Test send_verification_code authenticated for phone update."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+9876543210")
    current_user = {"sub": "user-123"}
    
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_user_data.user = mock_user
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('apps.user_service.app.api.verification_codes.create_verification_code',
               AsyncMock(return_value={"id": "verification-123", "expiry_at": 1234567890, "verification_code": "1234"})):
        result = await send_verification_code(mock_request, data, current_user)
        assert result.verificationId == "verification-123"


@pytest.mark.asyncio
async def test_send_verification_code_max_attempts_reached():
    """Test send_verification_code when max attempts reached."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    
    # Create 5 unverified codes (max attempts)
    recent_codes = [{"verified": False} for _ in range(5)]
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=recent_codes)):
        with pytest.raises(HTTPException) as exc_info:
            await send_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 429
        assert "Maximum send OTP attempts" in exc_info.value.detail


@pytest.mark.asyncio
async def test_send_verification_code_email_send_fails():
    """Test send_verification_code when email sending fails."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('apps.user_service.app.api.verification_codes.create_verification_code',
               AsyncMock(return_value={"id": "verification-123", "expiry_at": 1234567890, "verification_code": "1234"})), \
         patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
               return_value=False):
        result = await send_verification_code(mock_request, data, None)
        # Should still succeed even if email fails
        assert result.verificationId == "verification-123"


# ============================================================================
# Tests for verify_verification_code endpoint
# ============================================================================

@pytest.mark.asyncio
async def test_verify_verification_code_unauthenticated():
    """Test verify_verification_code unauthenticated (signup flow)."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": None,
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await verify_verification_code(mock_request, data, None)
        assert result.verified is True


@pytest.mark.asyncio
async def test_verify_verification_code_authenticated_no_update_trigger():
    """Test verify_verification_code authenticated but not update trigger."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    current_user = {"sub": "user-123"}
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": "user-123",
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value  # Not update trigger
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await verify_verification_code(mock_request, data, current_user)
        assert result.verified is True
        assert "updated" not in result.message.lower()


@pytest.mark.asyncio
async def test_verify_verification_code_no_access_token():
    """Test verify_verification_code when access token is missing."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    del mock_request.state.access_token
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    current_user = {"sub": "user-123"}
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": "user-123",
        "triggered_text": VerificationTrigger.EMAIL_UPDATE.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, current_user)
        assert exc_info.value.status_code == 401
        assert "Access token required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_verification_code_wrong_code():
    """Test verify_verification_code with wrong verification code."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="9999",  # Wrong code
        email="test@example.com"
    )
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",
        "verification_code": "1234",  # Correct code
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": None,
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    }
    
    # Patch update_verification_code where it's used in the module
    # The function is imported, so we patch it in the verification_codes module namespace
    # Make sure it returns None without raising any exceptions
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        # Check for either error message variant
        assert "Invalid verification code" in exc_info.value.detail or "Please try again" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_verification_code_expired():
    """Test verify_verification_code with expired code."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    # Expired code (expiry in the past)
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp() * 1000),
        "user_id": None,
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_verification_code_already_verified():
    """Test verify_verification_code with already verified code."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    verification_record = {
        "id": data.verificationId,
        "verified": True,  # Already verified
        "given_input": "test@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": None,
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        assert "already been verified" in exc_info.value.detail.lower()


# ============================================================================
# Tests for _verify_code_and_update_record
# ============================================================================

@pytest.mark.asyncio
async def test_verify_code_and_update_record_correct_code():
    """Test _verify_code_and_update_record with correct code."""
    from apps.user_service.app.api.verification_codes import _verify_code_and_update_record
    
    verification_record = {
        "id": "verification-123",
        "verification_code": "1234",
        "attempts": []
    }
    
    with patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await _verify_code_and_update_record(verification_record, "1234", "verification-123")
        assert result is True


@pytest.mark.asyncio
async def test_verify_code_and_update_record_wrong_code():
    """Test _verify_code_and_update_record with wrong code."""
    from apps.user_service.app.api.verification_codes import _verify_code_and_update_record
    
    verification_record = {
        "id": "verification-123",
        "verification_code": "1234",
        "attempts": []
    }
    
    with patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        with pytest.raises(HTTPException) as exc_info:
            await _verify_code_and_update_record(verification_record, "9999", "verification-123")
        assert exc_info.value.status_code == 400
        assert "Invalid verification code" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_code_and_update_record_existing_attempts():
    """Test _verify_code_and_update_record with existing attempts."""
    from apps.user_service.app.api.verification_codes import _verify_code_and_update_record
    
    verification_record = {
        "id": "verification-123",
        "verification_code": "1234",
        "attempts": [{"entered_value": "1111", "matched": False}]
    }
    
    with patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await _verify_code_and_update_record(verification_record, "1234", "verification-123")
        assert result is True


@pytest.mark.asyncio
async def test_verify_code_and_update_record_attempts_not_list():
    """Test _verify_code_and_update_record when attempts is not a list."""
    from apps.user_service.app.api.verification_codes import _verify_code_and_update_record
    
    verification_record = {
        "id": "verification-123",
        "verification_code": "1234",
        "attempts": "not-a-list"  # Invalid format
    }
    
    with patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await _verify_code_and_update_record(verification_record, "1234", "verification-123")
        assert result is True


# ============================================================================
# Additional edge case tests
# ============================================================================

@pytest.mark.asyncio
async def test_send_verification_code_authenticated_email():
    """Test send_verification_code authenticated for email update."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"sub": "user-123", "email": "current@example.com"}
    
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.email = "current@example.com"
    mock_user_data.user = mock_user
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('apps.user_service.app.api.verification_codes.create_verification_code',
               AsyncMock(return_value={"id": "verification-123", "expiry_at": 1234567890, "verification_code": "1234"})), \
         patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
               return_value=True):
        result = await send_verification_code(mock_request, data, current_user)
        assert result.verificationId == "verification-123"


@pytest.mark.asyncio
async def test_send_verification_code_email_exception():
    """Test send_verification_code when email sending raises exception."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('apps.user_service.app.api.verification_codes.create_verification_code',
               AsyncMock(return_value={"id": "verification-123", "expiry_at": 1234567890, "verification_code": "1234"})), \
         patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
               side_effect=Exception("Email service error")):
        result = await send_verification_code(mock_request, data, None)
        # Should still succeed even if email fails
        assert result.verificationId == "verification-123"


@pytest.mark.asyncio
async def test_send_verification_code_generic_exception():
    """Test send_verification_code when generic exception occurs."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "192.168.1.1"
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(side_effect=Exception("Database error"))):
        with pytest.raises(HTTPException) as exc_info:
            await send_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_verify_verification_code_not_found():
    """Test verify_verification_code when verification code not found."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_verification_code_input_mismatch():
    """Test verify_verification_code when input doesn't match."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="different@example.com"  # Different from stored
    )
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "test@example.com",  # Different
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": None,
        "triggered_text": VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 400
        assert "does not match" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_verification_code_generic_exception():
    """Test verify_verification_code when generic exception occurs."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(side_effect=Exception("Database error"))):
        with pytest.raises(HTTPException) as exc_info:
            await verify_verification_code(mock_request, data, None)
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_no_phone_attr():
    """Test _check_phone_exists_for_other_user when user has no phone attribute."""
    mock_user1 = MagicMock()
    mock_user1.id = "user-123"
    # No phone attribute
    
    mock_user2 = MagicMock()
    mock_user2.id = "other-user-456"
    # No phone attribute
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user1, mock_user2])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        # Should not raise
        await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_phone_attr():
    """Test _check_auth_user_exists_by_phone when user has no phone attribute."""
    mock_user = MagicMock()
    # No phone attribute
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_user_data():
    """Test _validate_phone_for_update when user_data is None."""
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=None)):
        # Should not raise, just log warning
        await _validate_phone_for_update("+9876543210", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_user_attr():
    """Test _validate_phone_for_update when user_data has no user attribute."""
    mock_user_data = MagicMock()
    # No user attribute
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        # Should not raise
        await _validate_phone_for_update("+9876543210", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_phone_field():
    """Test _validate_phone_for_update when user has no phone field."""
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    # No phone attribute
    mock_user_data.user = mock_user
    
    mock_admin_client = MagicMock()
    mock_admin_client.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)):
        # Should not raise
        await _validate_phone_for_update("+9876543210", "user-123")


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_no_sub():
    """Test _validate_authenticated_user_input when user has no 'sub'."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"email": "current@example.com"}  # No 'sub'
    
    mock_user_data = MagicMock()
    mock_user = MagicMock()
    mock_user.email = "current@example.com"
    mock_user_data.user = mock_user
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id is None
        assert triggered_text == VerificationTrigger.EMAIL_UPDATE.value

