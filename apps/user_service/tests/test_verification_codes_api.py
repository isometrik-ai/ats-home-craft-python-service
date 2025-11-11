# pylint: disable=all

"""
Test cases for verification codes API endpoints.
Tests both send and verify verification code endpoints.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException
from apps.user_service.app.api.verification_codes import router as verification_codes_router
from libs.shared_middleware.jwt_auth import get_user_from_auth


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Disable rate limiting for tests."""
    class DummyLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator
    
    with patch('apps.user_service.app.api.verification_codes.limiter', DummyLimiter()):
        yield


@pytest.fixture
def app():
    """Create FastAPI app with verification codes router for testing."""
    app = FastAPI()
    app.include_router(verification_codes_router)

    # Mock optional authentication (can return None for unauthenticated requests)
    def mock_get_optional_user():
        return None  # Optional auth, can be None

    # Override the optional user dependency
    from apps.user_service.app.api.verification_codes import get_optional_user
    app.dependency_overrides[get_optional_user] = mock_get_optional_user

    return app


@pytest.fixture
def app_with_auth():
    """Create FastAPI app with verification codes router for testing with authenticated user."""
    app = FastAPI()
    app.include_router(verification_codes_router)

    # Mock optional authentication with authenticated user
    def mock_get_optional_user():
        return {"id": "test-user-id-123", "email": "test@example.com"}  # Authenticated user

    # Override the optional user dependency
    from apps.user_service.app.api.verification_codes import get_optional_user
    app.dependency_overrides[get_optional_user] = mock_get_optional_user

    return app


@pytest.fixture
def client_with_auth(app_with_auth):
    """Test client for verification codes endpoints with authenticated user."""
    return TestClient(app_with_auth)


@pytest.fixture
def client(app):
    """Test client for verification codes endpoints."""
    return TestClient(app)


@pytest.fixture
def mock_verification_record():
    """Mock verification code record."""
    current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expiry_at = current_time_ms + (10 * 60 * 1000)  # 10 minutes from now
    
    return {
        "id": str(uuid.uuid4()),
        "type_text": "EMAIL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expiry_at": expiry_at,
        "triggered_text": "test@example.com",
        "user_id": None,
        "verification_code": "1111",
        "given_input": "test@example.com",
        "verified": False,
        "attempts": [],
        "ip_address": "127.0.0.1"
    }


@pytest.fixture
def mock_verified_record(mock_verification_record):
    """Mock verified verification code record."""
    record = mock_verification_record.copy()
    record["verified"] = True
    return record


@pytest.fixture
def mock_expired_record(mock_verification_record):
    """Mock expired verification code record."""
    record = mock_verification_record.copy()
    record["expiry_at"] = int((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000)
    return record


# ============================================================================
# SEND VERIFICATION CODE TESTS
# ============================================================================

class TestSendVerificationCode:
    """Test cases for POST /v1/verification-code/send endpoint."""

    def test_send_verification_code_email_success(self, client, mock_verification_record):
        """Test successful email verification code send."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
                   return_value=True):
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data
            assert "expiryAt" in data
            assert data["message"] == "Verification code sent successfully"

    def test_send_verification_code_phone_success(self, client, mock_verification_record):
        """Test successful phone verification code send."""
        request_data = {
            "type": "PHONE_NUMBER",
            "phoneNumber": "9558985338"
        }

        # Update mock record for phone
        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "9558985338"
        phone_record["triggered_text"] = "9558985338"

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=phone_record)):
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data
            assert "expiryAt" in data

    def test_send_verification_code_max_attempts_reached(self, client):
        """Test send verification code when max attempts reached."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        # Mock recent codes that haven't expired (5 unverified codes = max attempts reached, default MAX_ATTEMPT_VERIFICATION=5)
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        recent_codes = [
            {"verified": False, "expiry_at": current_time_ms + 60000},
            {"verified": False, "expiry_at": current_time_ms + 60000},
            {"verified": False, "expiry_at": current_time_ms + 60000},
            {"verified": False, "expiry_at": current_time_ms + 60000},
            {"verified": False, "expiry_at": current_time_ms + 60000},
        ]

        # Patch where it's used in the API module (patch both import locations)
        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_recent_verification_codes',
                   AsyncMock(return_value=recent_codes)), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=recent_codes)):
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 429
            assert "Maximum verification attempts" in response.json()["detail"]

    def test_send_verification_code_validation_error_missing_email(self, client):
        """Test send verification code with missing email for EMAIL type."""
        request_data = {
            "type": "EMAIL"
        }

        response = client.post("/v1/verification-code/send", json=request_data)
        
        assert response.status_code == 422

    def test_send_verification_code_validation_error_missing_phone(self, client):
        """Test send verification code with missing phone for PHONE_NUMBER type."""
        request_data = {
            "type": "PHONE_NUMBER"
        }

        response = client.post("/v1/verification-code/send", json=request_data)
        
        assert response.status_code == 422

    def test_send_verification_code_validation_error_wrong_field(self, client):
        """Test send verification code with wrong field (email for PHONE_NUMBER)."""
        request_data = {
            "type": "PHONE_NUMBER",
            "email": "test@example.com"
        }

        response = client.post("/v1/verification-code/send", json=request_data)
        
        assert response.status_code == 422

    def test_send_verification_code_email_sends_email(self, client, mock_verification_record):
        """Test that email is sent when type is EMAIL."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email') as mock_send_email:
            
            mock_send_email.return_value = True
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 200
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args
            assert call_args[1]["email"] == "test@example.com"
            assert call_args[1]["otp_code"] == "1111"

    def test_send_verification_code_phone_no_email_sent(self, client, mock_verification_record):
        """Test that email is not sent when type is PHONE_NUMBER."""
        request_data = {
            "type": "PHONE_NUMBER",
            "phoneNumber": "9558985338"
        }

        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "9558985338"

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=phone_record)), \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email') as mock_send_email:
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 200
            mock_send_email.assert_not_called()

    def test_send_verification_code_with_authenticated_user(self, client_with_auth, mock_verification_record):
        """Test send verification code with authenticated user (covers user_id branch)."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        # Mock record with user_id to verify it's being set
        record_with_user = mock_verification_record.copy()
        record_with_user["user_id"] = "test-user-id-123"

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=record_with_user)) as mock_create, \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
                   return_value=True):
            
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data
            assert "expiryAt" in data
            
            # Verify create_verification_code was called with user_id
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs.get("user_id") == "test-user-id-123"


# ============================================================================
# VERIFY VERIFICATION CODE TESTS
# ============================================================================

class TestVerifyVerificationCode:
    """Test cases for POST /v1/verification-code/verify endpoint."""

    def test_verify_verification_code_success(self, client, mock_verification_record):
        """Test successful verification code verification."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(return_value=mock_verification_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is True
            assert "Verification code verified successfully" in data["message"]

    def test_verify_verification_code_not_found(self, client):
        """Test verify with non-existent verification ID."""
        request_data = {
            "type": "EMAIL",
            "verificationId": str(uuid.uuid4()),
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=None)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 404
            assert "Verification code not found" in response.json()["detail"]

    def test_verify_verification_code_already_verified(self, client, mock_verified_record):
        """Test verify with already verified code."""
        verification_id = mock_verified_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verified_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 400
            assert "already been verified" in response.json()["detail"]

    def test_verify_verification_code_expired(self, client, mock_expired_record):
        """Test verify with expired code."""
        verification_id = mock_expired_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_expired_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 400
            assert "expired" in response.json()["detail"]

    def test_verify_verification_code_invalid_code(self, client, mock_verification_record):
        """Test verify with invalid code."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "9999",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(return_value=mock_verification_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 400
            assert "Invalid verification code" in response.json()["detail"]
            assert "Attempt 1" in response.json()["detail"]

    def test_verify_verification_code_email_mismatch(self, client, mock_verification_record):
        """Test verify with email that doesn't match verification record."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "different@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verification_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 400
            assert "does not match" in response.json()["detail"]

    def test_verify_verification_code_multiple_attempts(self, client, mock_verification_record):
        """Test verify with multiple failed attempts."""
        verification_id = mock_verification_record["id"]
        
        # Add existing attempts
        record_with_attempts = mock_verification_record.copy()
        record_with_attempts["attempts"] = [
            {"entered_value": "2222", "matched": False, "success": False, "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000)},
            {"entered_value": "3333", "matched": False, "success": False, "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000)}
        ]

        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "9999",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=record_with_attempts)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(return_value=record_with_attempts)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 400
            assert "Invalid verification code" in response.json()["detail"]
            assert "Attempt 3" in response.json()["detail"]

    def test_verify_verification_code_phone_success(self, client, mock_verification_record):
        """Test successful phone verification."""
        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "9558985338"
        phone_record["triggered_text"] = "9558985338"

        verification_id = phone_record["id"]
        request_data = {
            "type": "PHONE_NUMBER",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "phoneNumber": "9558985338"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=phone_record)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(return_value=phone_record)):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is True

    def test_verify_verification_code_validation_error_missing_fields(self, client):
        """Test verify with missing required fields."""
        request_data = {
            "type": "EMAIL"
        }

        response = client.post("/v1/verification-code/verify", json=request_data)
        
        assert response.status_code == 422

    def test_verify_verification_code_validation_error_wrong_type(self, client):
        """Test verify with wrong type (email for PHONE_NUMBER)."""
        request_data = {
            "type": "PHONE_NUMBER",
            "verificationId": str(uuid.uuid4()),
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        response = client.post("/v1/verification-code/verify", json=request_data)
        
        assert response.status_code == 422

    def test_verify_verification_code_database_error(self, client, mock_verification_record):
        """Test verify when database update fails."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(side_effect=Exception("Database error"))):
            
            response = client.post("/v1/verification-code/verify", json=request_data)
            
            # Should handle error gracefully (500 or handled by exception middleware)
            assert response.status_code in [500, 400]

    def test_send_verification_code_email_failure_does_not_block(self, client, mock_verification_record):
        """Test that email sending failure doesn't block code creation."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
                   return_value=False):  # Email fails
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            # Should still succeed even if email fails
            assert response.status_code == 200
            assert "verificationId" in response.json()

    def test_send_verification_code_email_exception_does_not_block(self, client, mock_verification_record):
        """Test that email sending exception doesn't block code creation."""
        request_data = {
            "type": "EMAIL",
            "email": "test@example.com"
        }

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
                   side_effect=Exception("Email service error")):
            
            response = client.post("/v1/verification-code/send", json=request_data)
            
            # Should still succeed even if email throws exception
            assert response.status_code == 200
            assert "verificationId" in response.json()

