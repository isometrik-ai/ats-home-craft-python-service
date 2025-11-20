# pylint: disable=all

"""
Tests to cover missing lines in verification_codes.py to increase coverage above 80%.
Focuses on simpler paths that are easier to test reliably.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException, Request
from apps.user_service.app.api.verification_codes import (
    verify_verification_code,
)
from apps.user_service.app.schemas.verification_codes import (
    VerifyVerificationCodeRequest,
    VerificationType,
    VerificationTrigger,
)


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Disable rate limiting for tests."""
    class DummyLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator
    dummy_limiter = DummyLimiter()
    with patch('apps.user_service.app.app_instance.limiter', dummy_limiter), \
         patch('apps.user_service.app.api.verification_codes.limiter', dummy_limiter):
        yield


# ============================================================================
# Tests for verify_verification_code - Response Building (lines 1232, 1234)
# ============================================================================

@pytest.mark.asyncio
async def test_verify_verification_code_email_updated_message():
    """Test verify_verification_code response message when email is updated (line 1232)."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.access_token = "test-token"
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="newemail@example.com"
    )
    
    current_user = {"sub": "user-123"}
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "newemail@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": "user-123",
        "triggered_text": VerificationTrigger.EMAIL_UPDATE.value
    }
    
    # Mock _update_email_or_phone to return email_updated=True
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               new=AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes._update_email_or_phone',
               AsyncMock(return_value=(True, False))):  # email_updated=True, phone_updated=False
        result = await verify_verification_code(mock_request, data, current_user)
        assert result.verified is True
        assert "Email has been updated" in result.message


@pytest.mark.asyncio
async def test_verify_verification_code_phone_updated_message():
    """Test verify_verification_code response message when phone is updated (line 1234)."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.access_token = "test-token"
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        phoneNumber="+1234567890"
    )
    
    current_user = {"sub": "user-123"}
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "+1234567890",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": "user-123",
        "triggered_text": VerificationTrigger.PHONE_NUMBER_UPDATE.value
    }
    
    # Mock _update_email_or_phone to return phone_updated=True
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               new=AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes._update_email_or_phone',
               AsyncMock(return_value=(False, True))):  # email_updated=False, phone_updated=True
        result = await verify_verification_code(mock_request, data, current_user)
        assert result.verified is True
        assert "Phone number has been updated" in result.message


@pytest.mark.asyncio
async def test_verify_verification_code_log_update_attempt():
    """Test verify_verification_code logging update attempt (line 1213)."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.access_token = "test-token"
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="newemail@example.com"
    )
    
    current_user = {"sub": "user-123"}
    
    verification_record = {
        "id": data.verificationId,
        "verified": False,
        "given_input": "newemail@example.com",
        "verification_code": "1234",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "user_id": "user-123",
        "triggered_text": VerificationTrigger.EMAIL_UPDATE.value
    }
    
    with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
               AsyncMock(return_value=verification_record)), \
         patch('apps.user_service.app.api.verification_codes.update_verification_code',
               new=AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes._update_email_or_phone',
               AsyncMock(return_value=(True, False))), \
         patch('apps.user_service.app.api.verification_codes.logger') as mock_logger:
        result = await verify_verification_code(mock_request, data, current_user)
        assert result.verified is True
        # Verify logger.info was called with update attempt message
        mock_logger.info.assert_any_call(
            "Attempting to update %s for user %s with triggered_text: %s",
            data.type.value,
            "user-123",
            VerificationTrigger.EMAIL_UPDATE.value
        )


@pytest.mark.asyncio
async def test_verify_verification_code_skip_update_log():
    """Test verify_verification_code logging when update is skipped (lines 1221-1227)."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    
    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verificationId=str(uuid.uuid4()),
        verificationCode="1234",
        email="test@example.com"
    )
    
    current_user = {"sub": "user-123"}
    
    # Use SIGNUP trigger which should skip update
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
               new=AsyncMock(return_value=None)), \
         patch('apps.user_service.app.api.verification_codes.logger') as mock_logger:
        result = await verify_verification_code(mock_request, data, current_user)
        assert result.verified is True
        # Verify skip log was called
        mock_logger.info.assert_any_call(
            "Skipping email/phone update - user_id: %s, triggered_text: %s, expected: %s or %s",
            "user-123",
            VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value,
            VerificationTrigger.EMAIL_UPDATE.value,
            VerificationTrigger.PHONE_NUMBER_UPDATE.value
        )
