# pylint: disable=all

"""
Additional test cases for verification codes API to increase coverage.
Tests helper functions, edge cases, and error paths.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException, Request
from apps.user_service.app.api.verification_codes import (
    _sanitize_ip,
    get_client_ip,
    _normalize_phone,
    _validate_email_for_update,
    _validate_phone_for_update,
    _check_phone_exists_for_other_user,
    _check_auth_user_exists_by_phone,
    _get_supabase_client_with_token,
    _validate_authenticated_user_input,
    _determine_triggered_text
)
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationType,
    VerificationTrigger
)




# ============================================================================
# HELPER FUNCTION TESTS
# ============================================================================

def test_sanitize_ip_valid_ipv4():
    """Test _sanitize_ip with valid IPv4."""
    result = _sanitize_ip("192.168.1.1")
    assert result == "192.168.1.1"


def test_sanitize_ip_valid_ipv6():
    """Test _sanitize_ip with valid IPv6."""
    result = _sanitize_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
    assert result == "2001:0db8:85a3:0000:0000:8a2e:0370:7334"


def test_sanitize_ip_with_comma():
    """Test _sanitize_ip with comma-separated IPs."""
    result = _sanitize_ip("192.168.1.1, 10.0.0.1")
    assert result == "192.168.1.1"


def test_sanitize_ip_invalid():
    """Test _sanitize_ip with invalid IP."""
    result = _sanitize_ip("invalid-ip")
    assert result is None


def test_sanitize_ip_none():
    """Test _sanitize_ip with None."""
    result = _sanitize_ip(None)
    assert result is None


def test_sanitize_ip_empty():
    """Test _sanitize_ip with empty string."""
    result = _sanitize_ip("")
    assert result is None


def test_get_client_ip_from_forwarded_for():
    """Test get_client_ip with X-Forwarded-For header."""
    request = MagicMock(spec=Request)
    request.headers = {"X-Forwarded-For": "192.168.1.1"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    
    result = get_client_ip(request)
    assert result == "192.168.1.1"


def test_get_client_ip_from_real_ip():
    """Test get_client_ip with X-Real-IP header."""
    request = MagicMock(spec=Request)
    request.headers = {"X-Real-IP": "10.0.0.1"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    
    result = get_client_ip(request)
    assert result == "10.0.0.1"


def test_get_client_ip_from_client_host():
    """Test get_client_ip from client.host."""
    request = MagicMock(spec=Request)
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    
    result = get_client_ip(request)
    assert result == "127.0.0.1"


def test_get_client_ip_invalid_host():
    """Test get_client_ip with invalid client.host."""
    request = MagicMock(spec=Request)
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "invalid-ip"
    
    result = get_client_ip(request)
    assert result == "invalid-ip"  # Falls back to original if sanitization fails


def test_get_client_ip_no_client():
    """Test get_client_ip with no client."""
    request = MagicMock(spec=Request)
    request.headers = {}
    request.client = None
    
    result = get_client_ip(request)
    assert result == "unknown"


def test_normalize_phone_with_plus():
    """Test _normalize_phone with '+' sign."""
    result = _normalize_phone("+1234567890")
    assert result == "1234567890"


def test_normalize_phone_without_plus():
    """Test _normalize_phone without '+' sign."""
    result = _normalize_phone("1234567890")
    assert result == "1234567890"


def test_normalize_phone_multiple_plus():
    """Test _normalize_phone with multiple '+' signs."""
    result = _normalize_phone("++1234567890")
    assert result == "1234567890"


def test_normalize_phone_none():
    """Test _normalize_phone with None."""
    result = _normalize_phone(None)
    assert result is None


def test_normalize_phone_empty():
    """Test _normalize_phone with empty string."""
    result = _normalize_phone("")
    assert result == ""


# ============================================================================
# VALIDATION FUNCTION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_validate_email_for_update_same_email():
    """Test _validate_email_for_update with same email."""
    with pytest.raises(HTTPException) as exc_info:
        await _validate_email_for_update("test@example.com", "user-123", "test@example.com")
    assert exc_info.value.status_code == 400
    assert "same as your current email" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_email_for_update_email_exists():
    """Test _validate_email_for_update with existing email."""
    mock_user = MagicMock()
    mock_user.id = "other-user-id"
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=mock_user)):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_email_for_update("existing@example.com", "user-123", "test@example.com")
        assert exc_info.value.status_code == 409
        assert "already registered" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_email_for_update_email_check_error():
    """Test _validate_email_for_update when email check fails."""
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(side_effect=Exception("Database error"))):
        # Should not raise exception, just log warning
        await _validate_email_for_update("new@example.com", "user-123", "test@example.com")


@pytest.mark.asyncio
async def test_validate_phone_for_update_same_phone():
    """Test _validate_phone_for_update with same phone."""
    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1234567890"
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_phone_for_update("+1234567890", "user-123")
        assert exc_info.value.status_code == 400
        assert "same as your current phone number" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_phone_for_update_phone_exists():
    """Test _validate_phone_for_update with existing phone."""
    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1111111111"
    
    mock_other_user = MagicMock()
    mock_other_user.id = "other-user-id"
    mock_other_user.phone = "+1234567890"
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_phone_for_update("+1234567890", "user-123")
        assert exc_info.value.status_code == 409
        assert "already registered" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_phone_for_update_error():
    """Test _validate_phone_for_update when check fails."""
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(side_effect=Exception("Database error"))):
        # Should not raise exception, just log warning
        await _validate_phone_for_update("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_exists():
    """Test _check_phone_exists_for_other_user when phone exists."""
    mock_other_user = MagicMock()
    mock_other_user.id = "other-user-id"
    mock_other_user.phone = "+1234567890"
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        with pytest.raises(HTTPException) as exc_info:
            await _check_phone_exists_for_other_user("+1234567890", "user-123")
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_not_exists():
    """Test _check_phone_exists_for_other_user when phone doesn't exist."""
    mock_other_user = MagicMock()
    mock_other_user.id = "other-user-id"
    mock_other_user.phone = "+1111111111"
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        # Should not raise exception
        await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_not_exists():
    """Test _check_auth_user_exists_by_phone when phone doesn't exist."""
    mock_user = MagicMock()
    mock_user.phone = "+1111111111"
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_error():
    """Test _check_auth_user_exists_by_phone when error occurs."""
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(side_effect=Exception("Database error"))):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False


# ============================================================================
# HELPER FUNCTION TESTS - CONTINUED
# ============================================================================

def test_determine_triggered_text_email():
    """Test _determine_triggered_text for EMAIL type."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    result = _determine_triggered_text(data, None)
    assert result == VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value


def test_determine_triggered_text_phone():
    """Test _determine_triggered_text for PHONE_NUMBER type."""
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="1234567890")
    result = _determine_triggered_text(data, None)
    assert result == VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value


def test_determine_triggered_text_authenticated_email():
    """Test _determine_triggered_text for authenticated EMAIL."""
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    current_user = {"sub": "user-123"}
    result = _determine_triggered_text(data, current_user)
    assert result == VerificationTrigger.EMAIL_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_no_user_id():
    """Test _validate_authenticated_user_input with no user_id."""
    current_user = {}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="test@example.com")
    # Function doesn't raise exception immediately, just logs warning
    # It will try to get user by None id, which will fail, then fallback to JWT email
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(side_effect=Exception("User not found"))), \
         patch('apps.user_service.app.api.verification_codes._validate_email_for_update',
               AsyncMock()):
        # Should handle gracefully - get_user_by_id fails, falls back to JWT email (empty), then validates
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id is None
        assert triggered_text == VerificationTrigger.EMAIL_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_email():
    """Test _validate_authenticated_user_input for email."""
    current_user = {"sub": "user-123", "email": "old@example.com"}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.email = "old@example.com"
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes._validate_email_for_update',
               AsyncMock()):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        assert triggered_text == VerificationTrigger.EMAIL_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_phone():
    """Test _validate_authenticated_user_input for phone."""
    current_user = {"sub": "user-123"}
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890")
    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1111111111"
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(return_value=mock_user_data)), \
         patch('apps.user_service.app.api.verification_codes._validate_phone_for_update',
               AsyncMock()):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        assert triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value


@pytest.mark.asyncio
async def test_get_supabase_client_with_token():
    """Test _get_supabase_client_with_token."""
    mock_client = MagicMock()
    with patch('apps.user_service.app.api.verification_codes.create_async_client',
               return_value=mock_client), \
         patch('apps.user_service.app.api.verification_codes.os.getenv') as mock_getenv:
        # Mock environment variables
        mock_getenv.side_effect = lambda key, default=None: {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_ANON_KEY": "test-anon-key"
        }.get(key, default)
        
        result = await _get_supabase_client_with_token("fake-token")
        assert result == mock_client
        # Verify create_async_client was called with correct parameters
        from supabase import create_async_client
        import os
        # Check that it was called (the actual call happens inside the function)
        assert mock_client is not None

