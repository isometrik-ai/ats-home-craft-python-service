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
        def __init__(self, *args, **kwargs):
            self.enabled = False  # Disable rate limiting
            self._auto_check = False
        
        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator
        
        def __call__(self, *args, **kwargs):
            return self
        
        def hit(self, *args, **kwargs):
            return True
        
        def get_window_stats(self, *args, **kwargs):
            return (0, 0)
        
        def _check_request_limit(self, *args, **kwargs):
            # Don't check limits
            pass
        
        def _inject_headers(self, response, *args, **kwargs):
            return response

    # Mock the limiter at the source (app_instance) and in the verification_codes module
    # Also mock get_recent_verification_codes to prevent rate limiting
    # Patch slowapi middleware to bypass rate limiting
    dummy_limiter = DummyLimiter()
    
    # Patch the slowapi middleware dispatch to bypass rate limiting
    async def bypass_middleware(self, request, call_next):
        """Bypass slowapi middleware rate limiting."""
        return await call_next(request)
    
    with patch('apps.user_service.app.app_instance.limiter', dummy_limiter), \
         patch('apps.user_service.app.api.verification_codes.limiter', dummy_limiter), \
         patch('slowapi.middleware.SlowAPIMiddleware.dispatch', bypass_middleware), \
         patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
               AsyncMock(return_value=[])), \
         patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_recent_verification_codes',
               AsyncMock(return_value=[])):
        yield


@pytest.fixture
def app(mock_rate_limiter):
    """Create FastAPI app with verification codes router for testing."""
    app = FastAPI()
    
    # Set a dummy limiter in app.state to prevent slowapi middleware from checking
    class DummyLimiter:
        enabled = False
        _auto_check = False
        def limit(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
    
    app.state.limiter = DummyLimiter()
    app.include_router(verification_codes_router)

    # Mock optional authentication (can return None for unauthenticated requests)
    def mock_get_optional_user():
        return None  # Optional auth, can be None

    # Override the optional user dependency
    from apps.user_service.app.api.verification_codes import get_optional_user
    app.dependency_overrides[get_optional_user] = mock_get_optional_user

    return app


@pytest.fixture
def app_with_auth(mock_rate_limiter):
    """Create FastAPI app with verification codes router for testing with authenticated user."""
    app = FastAPI()
    
    # Set a dummy limiter in app.state to prevent slowapi middleware from checking
    class DummyLimiter:
        enabled = False
        _auto_check = False
        def limit(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
    
    app.state.limiter = DummyLimiter()
    app.include_router(verification_codes_router)

    # Mock optional authentication with authenticated user
    def mock_get_optional_user():
        return {"sub": "test-user-id-123", "email": "current@example.com"}  # Authenticated user

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

        with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
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

        with patch('apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone',
                   AsyncMock(return_value=False)), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=phone_record)):

            response = client.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data
            assert "expiryAt" in data

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

        with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
                   AsyncMock(return_value=None)), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
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

        with patch('apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone',
                   AsyncMock(return_value=False)), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
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
            "email": "newemail@example.com"  # Different from current user's email
        }

        # Mock record with user_id to verify it's being set
        record_with_user = mock_verification_record.copy()
        record_with_user["user_id"] = "test-user-id-123"

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=record_with_user)) as mock_create, \
             patch('apps.user_service.app.api.verification_codes.send_verification_code_email',
                   return_value=True), \
             patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
                   AsyncMock(return_value=None)):

            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data
            assert "expiryAt" in data

            # Verify create_verification_code was called with user_id
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs.get("user_id") == "test-user-id-123"

    def test_send_verification_code_authenticated_user_same_email(self, client_with_auth):
        """Test send verification code with authenticated user using same email (should fail)."""
        request_data = {
            "type": "EMAIL",
            "email": "current@example.com"  # Same as current user's email
        }

        response = client_with_auth.post("/v1/verification-code/send", json=request_data)

        assert response.status_code == 400
        assert "same as your current email" in response.json()["detail"]

    def test_send_verification_code_authenticated_user_email_already_exists(self, client_with_auth):
        """Test send verification code with authenticated user using email that exists for another user."""
        request_data = {
            "type": "EMAIL",
            "email": "existing@example.com"
        }

        # Mock existing user with different ID
        mock_existing_user = MagicMock()
        mock_existing_user.id = "different-user-id"

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
                   AsyncMock(return_value=mock_existing_user)):

            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 409
            assert "already registered with another account" in response.json()["detail"]

    def test_send_verification_code_authenticated_user_phone_update(self, client_with_auth, mock_verification_record):
        """Test send verification code with authenticated user for phone update."""
        request_data = {
            "type": "PHONE_NUMBER",
            "phoneNumber": "1234567890"
        }

        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "1234567890"
        phone_record["triggered_text"] = "PHONE_NUMBER_UPDATE"

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}  # No phone set

        mock_supabase = MagicMock()
        mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])

        # Patch both locations to ensure it works
        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.create_verification_code',
                   AsyncMock(return_value=phone_record)), \
             patch('apps.user_service.app.api.verification_codes.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('libs.shared_db.supabase_db.db.get_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verificationId" in data

    def test_send_verification_code_authenticated_user_same_phone(self, client_with_auth):
        """Test send verification code with authenticated user using same phone (should fail)."""
        request_data = {
            "type": "PHONE_NUMBER",
            "phoneNumber": "1234567890"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {"phone": "1234567890"}  # Same phone

        # Patch both locations to ensure it works
        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)):

            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 400
            assert "same as your current phone number" in response.json()["detail"]

    def test_send_verification_code_authenticated_user_phone_already_exists(self, client_with_auth):
        """Test send verification code with authenticated user using phone that exists for another user."""
        request_data = {
            "type": "PHONE_NUMBER",
            "phoneNumber": "9876543210"
        }

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}  # No phone set

        # Mock another user with the same phone
        mock_other_user = MagicMock()
        mock_other_user.id = "other-user-id"
        mock_other_user.user_metadata = {"phone": "9876543210"}

        mock_supabase = MagicMock()
        mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])

        # Patch both locations to ensure it works
        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_user_by_id',
                   AsyncMock(return_value=mock_user_data)), \
             patch('libs.shared_db.supabase_db.db.get_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 409
            assert "already registered with another account" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_send_verification_code_unauthenticated_email_already_exists(self):
        """Test send verification code without token when email already exists in auth.users."""
        from apps.user_service.app.api.verification_codes import send_verification_code
        from apps.user_service.app.schemas.verification_codes import SendVerificationCodeRequest, VerificationType
        from fastapi import Request
        from fastapi.exceptions import HTTPException
        
        request_data = SendVerificationCodeRequest(
            type=VerificationType.EMAIL,
            email="existing@example.com"
        )

        mock_existing_user = MagicMock()
        mock_existing_user.email = "existing@example.com"
        
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"
        mock_request.state.user = None

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
                   AsyncMock(return_value=mock_existing_user)):

            with pytest.raises(HTTPException) as exc_info:
                await send_verification_code(mock_request, request_data, current_user=None)

            assert exc_info.value.status_code == 400
            assert "already registered" in exc_info.value.detail
            assert "login instead of signing up" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_send_verification_code_unauthenticated_phone_already_exists(self):
        """Test send verification code without token when phone already exists in auth.users."""
        from apps.user_service.app.api.verification_codes import send_verification_code
        from apps.user_service.app.schemas.verification_codes import SendVerificationCodeRequest, VerificationType
        from fastapi import Request
        from fastapi.exceptions import HTTPException
        
        request_data = SendVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            phoneNumber="9876543210"
        )
        
        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"
        mock_request.state.user = None

        with patch('apps.user_service.app.api.verification_codes.get_recent_verification_codes',
                   AsyncMock(return_value=[])), \
             patch('apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone',
                   AsyncMock(return_value=True)):

            with pytest.raises(HTTPException) as exc_info:
                await send_verification_code(mock_request, request_data, current_user=None)

            assert exc_info.value.status_code == 400
            assert "already registered" in exc_info.value.detail
            assert "login instead of signing up" in exc_info.value.detail


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
            assert "Please try again" in response.json()["detail"]

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
            assert "Please try again" in response.json()["detail"]

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

    def test_verify_verification_code_authenticated_user_no_sub_in_token(self, client_with_auth, mock_verification_record):
        """Test verify with authenticated user but no 'sub' in token - covers line 282-283."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verificationId": verification_id,
            "verificationCode": "1111",
            "email": "test@example.com"
        }

        # Override to return user without 'sub'
        from apps.user_service.app.api.verification_codes import get_optional_user
        from fastapi import FastAPI
        
        app = FastAPI()
        
        # Set a dummy limiter in app.state to prevent slowapi middleware from checking
        class DummyLimiter:
            enabled = False
            _auto_check = False
            def limit(self, *args, **kwargs):
                def decorator(func):
                    return func
                return decorator
        
        app.state.limiter = DummyLimiter()
        app.include_router(verification_codes_router)
        
        def mock_get_optional_user_no_sub():
            return {"email": "test@example.com"}  # No 'sub' field
        
        app.dependency_overrides[get_optional_user] = mock_get_optional_user_no_sub
        client = TestClient(app)

        with patch('apps.user_service.app.api.verification_codes.get_verification_code_by_id',
                   AsyncMock(return_value=mock_verification_record)), \
             patch('apps.user_service.app.api.verification_codes.update_verification_code',
                   AsyncMock(return_value=mock_verification_record)):
            response = client.post("/v1/verification-code/verify", json=request_data)
            # Should still work, just with warning logged
            assert response.status_code == 200



# ============================================================================
# ADDITIONAL COVERAGE TESTS - NEW CODE
# ============================================================================

def test_get_optional_user_no_user_in_state():
    """Test get_optional_user when no user in request.state - covers line 74."""
    from apps.user_service.app.api.verification_codes import get_optional_user
    from fastapi import Request
    from unittest.mock import MagicMock
    
    # Create a mock request without user in state
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = None  # No user
    
    result = get_optional_user(mock_request)
    # Should return None when no user in state
    assert result is None


def test_get_optional_user_exception_handling():
    """Test get_optional_user when get_user_from_auth raises exception - covers lines 77-81."""
    from apps.user_service.app.api.verification_codes import get_optional_user
    from fastapi import Request
    from unittest.mock import MagicMock, patch
    
    # Create a mock request with user in state
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = {"sub": "test-user-id"}
    
    # Patch get_user_from_auth to raise an exception
    with patch('apps.user_service.app.api.verification_codes.get_user_from_auth',
               side_effect=Exception("Token validation failed")):
        result = get_optional_user(mock_request)
        # Should return None when exception occurs
        assert result is None


def test_get_client_ip_with_forwarded_for():
    """Test get_client_ip with X-Forwarded-For header - covers lines 91-94."""
    from apps.user_service.app.api.verification_codes import get_client_ip
    from fastapi import Request
    from unittest.mock import MagicMock
    
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}
    
    ip = get_client_ip(mock_request)
    assert ip == "192.168.1.1"


def test_get_client_ip_with_real_ip():
    """Test get_client_ip with X-Real-IP header - covers lines 97-99."""
    from apps.user_service.app.api.verification_codes import get_client_ip
    from fastapi import Request
    from unittest.mock import MagicMock
    
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"X-Real-IP": "203.0.113.1"}
    mock_request.client = None
    
    ip = get_client_ip(mock_request)
    assert ip == "203.0.113.1"


# ============================================================================
# UNIT TESTS FOR HELPER FUNCTIONS - Direct testing to avoid rate limiting
# ============================================================================

@pytest.mark.asyncio
async def test_validate_email_for_update_generic_exception():
    """Test _validate_email_for_update with generic exception - covers lines 138-140."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(side_effect=Exception("Database connection timeout"))):
        # Should not raise, just log warning
        await _validate_email_for_update("new@example.com", "user-123", "old@example.com")


@pytest.mark.asyncio
async def test_validate_phone_for_update_generic_exception():
    """Test _validate_phone_for_update with generic exception - covers lines 195-196."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update
    
    with patch('apps.user_service.app.api.verification_codes.get_user_by_id',
               AsyncMock(side_effect=Exception("Network timeout"))):
        # Should not raise, just log warning
        await _validate_phone_for_update("1234567890", "user-123")


def test_check_verification_code_ownership_mismatch():
    """Test _check_verification_code_ownership with mismatch - covers lines 287-294."""
    from apps.user_service.app.api.verification_codes import _check_verification_code_ownership
    from fastapi import HTTPException
    
    verification_record = {"user_id": "different-user-id"}
    current_user = {"sub": "test-user-id-123"}
    
    with pytest.raises(HTTPException) as exc_info:
        _check_verification_code_ownership(verification_record, current_user, "verification-id")
    
    assert exc_info.value.status_code == 403
    assert "You can only verify your own verification codes" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_code_and_update_record_attempts_not_list():
    """Test _verify_code_and_update_record with attempts not a list - covers line 319."""
    from apps.user_service.app.api.verification_codes import _verify_code_and_update_record
    
    verification_record = {
        "id": "test-id",
        "verification_code": "1111",
        "attempts": "invalid"  # Not a list
    }
    
    with patch('apps.user_service.app.api.verification_codes.update_verification_code',
               AsyncMock(return_value=verification_record)):
        result = await _verify_code_and_update_record(verification_record, "1111", "test-id")
        assert result is True  # Code matches


@pytest.mark.asyncio
async def test_update_email_or_phone_email_success():
    """Test _update_email_or_phone for email update success - covers lines 379-405."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone
    from apps.user_service.app.schemas.verification_codes import VerificationTrigger
    
    with patch('apps.user_service.app.api.verification_codes.update_email_of_user',
               AsyncMock(return_value=True)):
        email_updated, phone_updated = await _update_email_or_phone(
            "user-123", "new@example.com", VerificationTrigger.EMAIL_UPDATE.value
        )
        assert email_updated is True
        assert phone_updated is False


@pytest.mark.asyncio
async def test_update_email_or_phone_phone_success():
    """Test _update_email_or_phone for phone update success - covers lines 379-405."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone
    from apps.user_service.app.schemas.verification_codes import VerificationTrigger
    
    with patch('apps.user_service.app.api.verification_codes.update_phone_of_user',
               AsyncMock(return_value=True)):
        email_updated, phone_updated = await _update_email_or_phone(
            "user-123", "1234567890", VerificationTrigger.PHONE_NUMBER_UPDATE.value
        )
        assert email_updated is False
        assert phone_updated is True


@pytest.mark.asyncio
async def test_update_email_or_phone_email_failure():
    """Test _update_email_or_phone for email update failure - covers lines 388-392."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone
    from apps.user_service.app.schemas.verification_codes import VerificationTrigger
    
    with patch('apps.user_service.app.api.verification_codes.update_email_of_user',
               AsyncMock(return_value=False)):
        email_updated, phone_updated = await _update_email_or_phone(
            "user-123", "new@example.com", VerificationTrigger.EMAIL_UPDATE.value
        )
        assert email_updated is False
        assert phone_updated is False


@pytest.mark.asyncio
async def test_update_email_or_phone_email_exception():
    """Test _update_email_or_phone for email update exception - covers lines 390-392."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone
    from apps.user_service.app.schemas.verification_codes import VerificationTrigger
    
    with patch('apps.user_service.app.api.verification_codes.update_email_of_user',
               AsyncMock(side_effect=Exception("Update failed"))):
        email_updated, phone_updated = await _update_email_or_phone(
            "user-123", "new@example.com", VerificationTrigger.EMAIL_UPDATE.value
        )
        assert email_updated is False
        assert phone_updated is False


@pytest.mark.asyncio
async def test_update_email_or_phone_phone_exception():
    """Test _update_email_or_phone for phone update exception - covers lines 401-403."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone
    from apps.user_service.app.schemas.verification_codes import VerificationTrigger
    
    with patch('apps.user_service.app.api.verification_codes.update_phone_of_user',
               AsyncMock(side_effect=Exception("Update failed"))):
        email_updated, phone_updated = await _update_email_or_phone(
            "user-123", "1234567890", VerificationTrigger.PHONE_NUMBER_UPDATE.value
        )
        assert email_updated is False
        assert phone_updated is False


def test_determine_triggered_text_authenticated_phone():
    """Test _determine_triggered_text for authenticated user phone - covers lines 421-424."""
    from apps.user_service.app.api.verification_codes import _determine_triggered_text
    from apps.user_service.app.schemas.verification_codes import SendVerificationCodeRequest, VerificationType, VerificationTrigger
    
    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="1234567890")
    current_user = {"sub": "user-123"}
    
    result = _determine_triggered_text(data, current_user)
    assert result == VerificationTrigger.PHONE_NUMBER_UPDATE.value


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_success():
    """Test _check_auth_user_exists_by_phone when phone exists - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    mock_user = MagicMock()
    mock_user.user_metadata = {"phone": "9876543210"}
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is True

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_not_found():
    """Test _check_auth_user_exists_by_phone when phone doesn't exist - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    mock_user = MagicMock()
    mock_user.user_metadata = {"phone": "1111111111"}  # Different phone
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_metadata():
    """Test _check_auth_user_exists_by_phone when user has no metadata - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    mock_user = MagicMock()
    mock_user.user_metadata = None
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_exception():
    """Test _check_auth_user_exists_by_phone when exception occurs - covers lines 189-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(side_effect=Exception("Database error"))):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_empty_list():
    """Test _check_auth_user_exists_by_phone when user list is empty - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False

@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_phone_in_metadata():
    """Test _check_auth_user_exists_by_phone when user_metadata exists but no phone field - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import _check_auth_user_exists_by_phone
    
    mock_user = MagicMock()
    mock_user.user_metadata = {"email": "test@example.com"}  # No phone field
    
    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])
    
    with patch('apps.user_service.app.api.verification_codes.get_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False

@pytest.mark.asyncio
async def test_validate_authenticated_user_input_no_user_id():
    """Test _validate_authenticated_user_input with no user_id - covers line 452."""
    from apps.user_service.app.api.verification_codes import _validate_authenticated_user_input
    from apps.user_service.app.schemas.verification_codes import SendVerificationCodeRequest, VerificationType
    
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"email": "old@example.com"}  # No 'sub' field
    
    with patch('apps.user_service.app.api.verification_codes.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        user_id, triggered_text = await _validate_authenticated_user_input(data, current_user)
        # Should still work, just with warning logged
        assert user_id is None or user_id == ""

