"""Test cases for verification codes API endpoints."""

# pylint: disable=too-many-lines

import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.user_service.app.api.verification_codes import (
    get_optional_user,
)
from apps.user_service.app.api.verification_codes import (
    router as verification_codes_router,
)
from apps.user_service.app.api.verification_codes import (
    send_verification_code,
)
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationTrigger,
    VerificationType,
    VerifyVerificationCodeRequest,
)


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Disable rate limiting for tests."""

    class DummyLimiter:
        """Dummy limiter class for testing."""

        def __init__(self, *_args, **_kwargs):
            """Initialize dummy limiter."""
            self.enabled = False  # Disable rate limiting
            self._auto_check = False

        def limit(self, *_args, **_kwargs):
            """Limit decorator that does nothing."""

            def decorator(func):
                return func

            return decorator

        def __call__(self, *_args, **_kwargs):
            """Call limiter."""
            return self

        def hit(self, *_args, **_kwargs):
            """Hit limiter."""
            return True

        def get_window_stats(self, *_args, **_kwargs):
            """Get window stats."""
            return (0, 0)

        def _check_request_limit(self, *_args, **_kwargs):
            """Don't check limits."""

        def _inject_headers(self, response, *_args, **_kwargs):
            """Inject headers."""
            return response

    # Mock the limiter at the source (app_instance) and in the verification_codes module
    # Also mock get_recent_verification_codes to prevent rate limiting
    # Patch slowapi middleware to bypass rate limiting
    dummy_limiter = DummyLimiter()

    # Patch the slowapi middleware dispatch to bypass rate limiting
    async def bypass_middleware(_self, request, call_next):
        """Bypass slowapi middleware rate limiting."""
        return await call_next(request)

    with (
        patch("apps.user_service.app.app_instance.limiter", dummy_limiter),
        patch("apps.user_service.app.api.verification_codes.limiter", dummy_limiter),
        patch("slowapi.middleware.SlowAPIMiddleware.dispatch", bypass_middleware),
        patch(
            "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
            AsyncMock(return_value=[]),
        ),
        patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "verification_operations.get_recent_verification_codes"
            ),
            AsyncMock(return_value=[]),
        ),
    ):
        yield


@pytest.fixture
def app():
    """Create FastAPI app with verification codes router for testing."""
    app = FastAPI()

    # Set a dummy limiter in app.state to prevent slowapi middleware from checking
    class DummyLimiter:
        """Dummy limiter for testing."""

        enabled = False
        _auto_check = False

        def limit(self, *_args, **_kwargs):
            """Limit decorator that does nothing."""

            def decorator(func):
                return func

            return decorator

    app.state.limiter = DummyLimiter()
    app.include_router(verification_codes_router)

    # Mock optional authentication (can return None for unauthenticated requests)
    def mock_get_optional_user():
        return None  # Optional auth, can be None

    app.dependency_overrides[get_optional_user] = mock_get_optional_user

    return app


@pytest.fixture
def app_with_auth():
    """Create FastAPI app with verification codes router for testing with authenticated user."""
    app = FastAPI()

    # Set a dummy limiter in app.state to prevent slowapi middleware from checking
    class DummyLimiter:
        """Dummy limiter for testing."""

        enabled = False
        _auto_check = False

        def limit(self, *_args, **_kwargs):
            """Limit decorator that does nothing."""

            def decorator(func):
                return func

            return decorator

    app.state.limiter = DummyLimiter()
    app.include_router(verification_codes_router)

    # Mock optional authentication with authenticated user
    def mock_get_optional_user():
        return {
            "sub": "test-user-id-123",
            "email": "current@example.com",
        }  # Authenticated user

    # Override the optional user dependency
    app.dependency_overrides[get_optional_user] = mock_get_optional_user

    return app


@pytest.fixture
def client_with_auth():
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
        "ip_address": "127.0.0.1",
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
    record["expiry_at"] = int(
        (datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp() * 1000
    )
    return record


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _build_admin_client():
    """Create a mock Supabase admin client with nested auth.admin helpers."""
    client = MagicMock()
    auth = MagicMock()
    admin_api = MagicMock()
    auth.admin = admin_api
    client.auth = auth
    return client, admin_api


# ============================================================================
# SEND VERIFICATION CODE TESTS
# ============================================================================


class TestSendVerificationCode:
    """Test cases for POST /v1/verification-code/send endpoint."""

    def test_send_ver_code_email_success(self, client, mock_verification_record):
        """Test successful email verification code send."""
        request_data = {"type": "EMAIL", "email": "test@example.com"}

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email",
                return_value=True,
            ),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verification_id" in data
            assert "expiryAt" in data
            assert data["message"] == "Verification code sent successfully"

    def test_send_ver_code_phone_success(self, client, mock_verification_record):
        """Test successful phone verification code send."""
        request_data = {"type": "PHONE_NUMBER", "phoneNumber": "9558985338"}

        # Update mock record for phone
        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "9558985338"
        phone_record["triggered_text"] = "9558985338"

        with (
            patch(
                "apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone",
                AsyncMock(return_value=False),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=phone_record),
            ),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verification_id" in data
            assert "expiryAt" in data

    def test_send_ver_code_validation_error_missing_email(self, client):
        """Test send verification code with missing email for EMAIL type."""
        request_data = {"type": "EMAIL"}

        response = client.post("/v1/verification-code/send", json=request_data)

        assert response.status_code == 422

    def test_send_ver_code_validation_error_missing_phone(self, client):
        """Test send verification code with missing phone for PHONE_NUMBER type."""
        request_data = {"type": "PHONE_NUMBER"}

        response = client.post("/v1/verification-code/send", json=request_data)

        assert response.status_code == 422

    def test_send_ver_code_validation_error_wrong_field(self, client):
        """Test send verification code with wrong field (email for PHONE_NUMBER)."""
        request_data = {"type": "PHONE_NUMBER", "email": "test@example.com"}

        response = client.post("/v1/verification-code/send", json=request_data)

        assert response.status_code == 422

    def test_send_ver_code_email_sends_email(self, client, mock_verification_record):
        """Test that email is sent when type is EMAIL."""
        request_data = {"type": "EMAIL", "email": "test@example.com"}

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email"
            ) as mock_send_email,
        ):
            mock_send_email.return_value = True

            response = client.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args
            assert call_args[1]["email"] == "test@example.com"
            assert call_args[1]["otp_code"] == "1111"

    def test_send_ver_code_phone_no_email_sent(self, client, mock_verification_record):
        """Test that email is not sent when type is PHONE_NUMBER."""
        request_data = {"type": "PHONE_NUMBER", "phoneNumber": "9558985338"}

        phone_record = mock_verification_record.copy()
        phone_record["type_text"] = "PHONE_NUMBER"
        phone_record["given_input"] = "9558985338"

        with (
            patch(
                "apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone",
                AsyncMock(return_value=False),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=phone_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email"
            ) as mock_send_email,
        ):
            response = client.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            mock_send_email.assert_not_called()

    def test_send_ver_code_rate_limit_exceeded(self, client):
        """Should return 429 if too many attempts exist."""
        from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
            MAX_ATTEMPT_VERIFICATION,
        )

        request_data = {"type": "EMAIL", "email": "test@example.com"}
        # Create enough unverified codes to exceed the limit
        recent_codes = [{"verified": False}] * MAX_ATTEMPT_VERIFICATION

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=recent_codes),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_client_ip",
                return_value="127.0.0.1",
            ),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)
        assert response.status_code == 429
        assert "Maximum send OTP attempts" in response.json()["detail"]

    def test_send_ver_code_email_send_failure(self, client, mock_verification_record):
        """Email failures should not break endpoint."""
        request_data = {"type": "EMAIL", "email": "test@example.com"}

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email",
                return_value=False,
            ),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)
        assert response.status_code == 200
        assert response.json()["message"] == "Verification code sent successfully"

    def test_send_ver_code_email_send_exception(self, client, mock_verification_record):
        """Exceptions during email send should be swallowed."""
        request_data = {"type": "EMAIL", "email": "test@example.com"}

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email",
                side_effect=Exception("SMTP error"),
            ),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)
        assert response.status_code == 200

    def test_send_ver_code_generic_exception(self, client):
        """Generic exceptions should surface as 500."""
        request_data = {"type": "EMAIL", "email": "test@example.com"}

        with patch(
            "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
            AsyncMock(side_effect=Exception("db failure")),
        ):
            response = client.post("/v1/verification-code/send", json=request_data)
        assert response.status_code == 500

    def test_send_ver_code_with_authenticated_user(
        self, client_with_auth, mock_verification_record
    ):
        """Test send verification code with authenticated user (covers user_id branch)."""
        request_data = {
            "type": "EMAIL",
            "email": "newemail@example.com",  # Different from current user's email
        }

        # Mock record with user_id to verify it's being set
        record_with_user = mock_verification_record.copy()
        record_with_user["user_id"] = "test-user-id-123"

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=record_with_user),
            ) as mock_create,
            patch(
                "apps.user_service.app.api.verification_codes.send_verification_code_email",
                return_value=True,
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=None),
            ),
        ):
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verification_id" in data
            assert "expiryAt" in data

            # Verify create_verification_code was called with user_id
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs.get("user_id") == "test-user-id-123"

    def test_send_ver_code_authenticated_user_same_email(self, client_with_auth):
        """Test send verification code with authenticated user using same email (should fail)."""
        request_data = {
            "type": "EMAIL",
            "email": "current@example.com",  # Same as current user's email
        }

        response = client_with_auth.post("/v1/verification-code/send", json=request_data)

        assert response.status_code == 400
        assert "same as your current email" in response.json()["detail"]

    def test_send_ver_code_auth_user_email_exists(self, client_with_auth):
        """Test send verification code with authenticated."""
        request_data = {"type": "EMAIL", "email": "existing@example.com"}

        # Mock existing user with different ID
        mock_existing_user = MagicMock()
        mock_existing_user.id = "different-user-id"

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=mock_existing_user),
            ),
        ):
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 409
            assert "already registered with another account" in response.json()["detail"]

    def test_send_ver_code_auth_user_phone_update(self, client_with_auth, mock_verification_record):
        """Test send verification code with authenticated user for phone update."""
        request_data = {"type": "PHONE_NUMBER", "phoneNumber": "1234567890"}

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
        with (
            patch(
                (
                    "libs.shared_db.postgres_db.user_service_operations."
                    "verification_operations.get_recent_verification_codes"
                ),
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.create_verification_code",
                AsyncMock(return_value=phone_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
                AsyncMock(return_value=mock_supabase),
            ),
            patch(
                "libs.shared_db.supabase_db.db.get_supabase_admin_client",
                AsyncMock(return_value=mock_supabase),
            ),
        ):
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert "verification_id" in data

    def test_send_ver_code_auth_user_same_phone(self, client_with_auth):
        """Test send verification code with authenticated user using same phone (should fail)."""
        request_data = {"type": "PHONE_NUMBER", "phoneNumber": "1234567890"}

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        # Set phone field (not user_metadata) since we check the actual phone field
        mock_user_data.user.phone = "1234567890"  # Same phone
        mock_user_data.user.user_metadata = {}

        # Patch both locations to ensure it works
        with (
            patch(
                (
                    "libs.shared_db.postgres_db.user_service_operations."
                    "verification_operations.get_recent_verification_codes"
                ),
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_client_ip",
                return_value="127.0.0.1",
            ),
        ):
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 400
            assert "same as your current phone number" in response.json()["detail"]

    def test_send_ver_code_auth_user_phone_exists(self, client_with_auth):
        """Test send verification code with authenticated."""
        request_data = {"type": "PHONE_NUMBER", "phoneNumber": "9876543210"}

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}  # No phone set

        # Mock another user with the same phone - set phone field, not user_metadata
        mock_other_user = MagicMock()
        mock_other_user.id = "other-user-id"
        mock_other_user.phone = "9876543210"  # Set phone field
        mock_other_user.user_metadata = {}

        mock_supabase = MagicMock()
        mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])

        # Patch both locations to ensure it works
        with (
            patch(
                (
                    "libs.shared_db.postgres_db.user_service_operations."
                    "verification_operations.get_recent_verification_codes"
                ),
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
                AsyncMock(return_value=mock_supabase),
            ),
            patch(
                "libs.shared_db.supabase_db.db.get_supabase_admin_client",
                AsyncMock(return_value=mock_supabase),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_client_ip",
                return_value="127.0.0.1",
            ),
        ):
            response = client_with_auth.post("/v1/verification-code/send", json=request_data)

            assert response.status_code == 409
            assert "already registered with another account" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_send_ver_code_unauthenticated_email_exists(self):
        """Test send verification code without token when email already exists in auth.users."""

        request_data = SendVerificationCodeRequest(
            type=VerificationType.EMAIL, email="existing@example.com"
        )

        mock_existing_user = MagicMock()
        mock_existing_user.email = "existing@example.com"

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"
        mock_request.state.user = None

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
                AsyncMock(return_value=mock_existing_user),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_verification_code(mock_request, request_data, current_user=None)

            assert exc_info.value.status_code == 400
            assert "already registered" in exc_info.value.detail
            assert "login instead of signing up" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_send_ver_code_unauthenticated_phone_exists(self):
        """Test send verification code without token when phone already exists in auth.users."""

        request_data = SendVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER, phoneNumber="9876543210"
        )

        mock_request = MagicMock(spec=Request)
        mock_request.client.host = "127.0.0.1"
        mock_request.state.user = None

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone",
                AsyncMock(return_value=True),
            ),
        ):
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
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "test@example.com",
        }

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is True
            assert "Verification code verified successfully" in data["message"]

    def test_verify_verification_code_not_found(self, client):
        """Test verify with non-existent verification ID."""
        request_data = {
            "type": "EMAIL",
            "verification_id": str(uuid.uuid4()),
            "verification_code": "1111",
            "email": "test@example.com",
        }

        with patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=None),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 404
            assert "Verification code not found" in response.json()["detail"]

    def test_verify_verification_code_already_verified(self, client, mock_verified_record):
        """Test verify with already verified code."""
        verification_id = mock_verified_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "test@example.com",
        }

        with patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_verified_record),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 400
            assert "already been verified" in response.json()["detail"]

    def test_verify_verification_code_expired(self, client, mock_expired_record):
        """Test verify with expired code."""
        verification_id = mock_expired_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "test@example.com",
        }

        with patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_expired_record),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 400
            assert "expired" in response.json()["detail"]

    def test_verify_verification_code_invalid_code(self, client, mock_verification_record):
        """Test verify with invalid code."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "9999",
            "email": "test@example.com",
        }

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 400
            assert "Invalid verification code" in response.json()["detail"]
            assert "Please try again" in response.json()["detail"]

    def test_verify_verification_code_email_mismatch(self, client, mock_verification_record):
        """Test verify with email that doesn't match verification record."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "different@example.com",
        }

        with patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 400
            assert "does not match" in response.json()["detail"]

    def test_verify_verification_code_multiple_attempts(self, client, mock_verification_record):
        """Test verify with multiple failed attempts."""
        verification_id = mock_verification_record["id"]

        # Add existing attempts
        record_with_attempts = mock_verification_record.copy()
        record_with_attempts["attempts"] = [
            {
                "entered_value": "2222",
                "matched": False,
                "success": False,
                "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
            },
            {
                "entered_value": "3333",
                "matched": False,
                "success": False,
                "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
            },
        ]

        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "9999",
            "email": "test@example.com",
        }

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=record_with_attempts),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(return_value=record_with_attempts),
            ),
        ):
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
            "verification_id": verification_id,
            "verification_code": "1111",
            "phoneNumber": "9558985338",
        }

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=phone_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(return_value=phone_record),
            ),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            assert response.status_code == 200
            data = response.json()
            assert data["verified"] is True

    def test_verify_ver_code_validation_missing_fields(self, client):
        """Test verify with missing required fields."""
        request_data = {"type": "EMAIL"}

        response = client.post("/v1/verification-code/verify", json=request_data)

        assert response.status_code == 422

    def test_verify_ver_code_validation_error_wrong_type(self, client):
        """Test verify with wrong type (email for PHONE_NUMBER)."""
        request_data = {
            "type": "PHONE_NUMBER",
            "verification_id": str(uuid.uuid4()),
            "verification_code": "1111",
            "email": "test@example.com",
        }

        response = client.post("/v1/verification-code/verify", json=request_data)

        assert response.status_code == 422

    def test_verify_verification_code_database_error(self, client, mock_verification_record):
        """Test verify when database update fails."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "test@example.com",
        }

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(side_effect=Exception("Database error")),
            ),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)

            # Should handle error gracefully (500 or handled by exception middleware)
            assert response.status_code in [500, 400]

    def test_verify_ver_code_auth_user_no_sub_in_token(
        self, _client_with_auth, mock_verification_record
    ):
        """Test verify with authenticated user but no 'sub' in token - covers line 282-283."""
        verification_id = mock_verification_record["id"]
        request_data = {
            "type": "EMAIL",
            "verification_id": verification_id,
            "verification_code": "1111",
            "email": "test@example.com",
        }

        # Override to return user without 'sub'

        app = FastAPI()

        # Set a dummy limiter in app.state to prevent slowapi middleware from checking
        class DummyLimiter:
            """Dummy limiter for testing."""

            enabled = False
            _auto_check = False

            def limit(self, *_args, **_kwargs):
                """Limit decorator that does nothing."""

                def decorator(func):
                    return func

                return decorator

        app.state.limiter = DummyLimiter()
        app.include_router(verification_codes_router)

        def mock_get_optional_user_no_sub():
            return {"email": "test@example.com"}  # No 'sub' field

        app.dependency_overrides[get_optional_user] = mock_get_optional_user_no_sub
        client = TestClient(app)

        with (
            patch(
                "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
                AsyncMock(return_value=mock_verification_record),
            ),
            patch(
                "apps.user_service.app.api.verification_codes.update_verification_code",
                AsyncMock(return_value=mock_verification_record),
            ),
        ):
            response = client.post("/v1/verification-code/verify", json=request_data)
            # Should still work, just with warning logged
            assert response.status_code == 200


# ============================================================================
# ADDITIONAL COVERAGE TESTS - NEW CODE
# ============================================================================


def test_get_optional_user_no_user_in_state():
    """Test get_optional_user when no user in request.state - covers line 74."""

    # Create a mock request without user in state
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = None  # No user

    result = get_optional_user(mock_request)
    # Should return None when no user in state
    assert result is None


def test_get_optional_user_exception_handling():
    """Test get_optional_user when get_user_from_auth raises exception - covers lines 77-81."""

    # Create a mock request with user in state
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = {"sub": "test-user-id"}

    # Patch get_user_from_auth to raise an exception
    with patch(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        side_effect=Exception("Token validation failed"),
    ):
        result = get_optional_user(mock_request)
        # Should return None when exception occurs
        assert result is None


def test_get_client_ip_with_forwarded_for():
    """Test get_client_ip with X-Forwarded-For header - covers lines 91-94."""
    from apps.user_service.app.api.verification_codes import get_client_ip

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}

    ip = get_client_ip(mock_request)
    assert ip == "192.168.1.1"


def test_get_client_ip_with_real_ip():
    """Test get_client_ip with X-Real-IP header - covers lines 97-99."""
    from apps.user_service.app.api.verification_codes import get_client_ip

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

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(side_effect=Exception("Database connection timeout")),
    ):
        # Should not raise, just log warning
        await _validate_email_for_update("new@example.com", "user-123", "old@example.com")


@pytest.mark.asyncio
async def test_validate_phone_for_update_generic_exception():
    """Test _validate_phone_for_update with generic exception - covers lines 195-196."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_by_id",
        AsyncMock(side_effect=Exception("Network timeout")),
    ):
        # Should not raise, just log warning
        await _validate_phone_for_update("1234567890", "user-123")


def test_check_verification_code_ownership_mismatch():
    """Test _check_verification_code_ownership with mismatch - covers lines 287-294."""
    from apps.user_service.app.api.verification_codes import (
        _check_verification_code_ownership,
    )

    verification_record = {"user_id": "different-user-id"}
    current_user = {"sub": "test-user-id-123"}

    with pytest.raises(HTTPException) as exc_info:
        _check_verification_code_ownership(verification_record, current_user)

    assert exc_info.value.status_code == 403
    assert "You can only verify your own verification codes" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_code_update_record_attempts_not_list():
    """Test _verify_code_and_update_record with attempts not a list - covers line 319."""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    verification_record = {
        "id": "test-id",
        "verification_code": "1111",
        "attempts": "invalid",  # Not a list
    }

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(return_value=verification_record),
    ):
        result = await _verify_code_and_update_record(verification_record, "1111", "test-id")
        assert result is True  # Code matches


@pytest.mark.asyncio
async def test_update_email_or_phone_email_failure():
    """Test _update_email_or_phone for email update failure."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    # Mock the admin API calls to return None (failure)
    mock_admin_supabase, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(user_metadata={}))
    )
    mock_admin_api.update_user_by_id = AsyncMock(return_value=MagicMock(user=None))  # Update fails

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_email_exception():
    """Test _update_email_or_phone for email update exception."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    # Mock the admin API calls to raise exception
    mock_admin_supabase, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(user_metadata={}))
    )
    mock_admin_api.update_user_by_id = AsyncMock(side_effect=Exception("Update failed"))

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_phone_exception():
    """Test _update_email_or_phone for phone update exception."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    # Mock the admin API calls to raise exception
    mock_admin_supabase, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(user_metadata={}))
    )
    mock_admin_api.update_user_by_id = AsyncMock(side_effect=Exception("Update failed"))

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_supabase),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _update_email_or_phone(
                "user-123",
                "1234567890",
                VerificationTrigger.PHONE_NUMBER_UPDATE.value,
                "fake-token",
            )
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_missing_jwt_secret():
    """Missing SUPABASE_JWT_SECRET should translate to failure."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            side_effect=lambda key, default=None: {"SUPABASE_JWT_SECRET": None}.get(key, default),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_expired_token():
    """Expired tokens should raise 401."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    expired_time = int(time.time()) - 3600
    expired_token = jwt.encode(
        {"sub": "user-123", "exp": expired_time}, "secret", algorithm="HS256"
    )

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": expired_time},
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                expired_token,
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_update_email_or_phone_user_conversion_error():
    """Failures converting Supabase user models should fall back gracefully."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()

    class SimpleUser:
        """Simple user class for testing."""

        def __init__(self):
            self.email = "old@example.com"

    mock_user_response.user = SimpleUser()

    future_time = int(time.time()) + 3600

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": future_time},
        ),
        patch("supabase_auth.types.User", side_effect=Exception("conversion failure")),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(side_effect=Exception("stop flow")),
        ),
    ):
        with pytest.raises(HTTPException):
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )


@pytest.mark.asyncio
async def test_update_email_or_phone_no_user_response():
    """Missing Supabase user should raise 401."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = None

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
    ):
        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_update_email_or_phone_email_update_no_response():
    """Email update should fail when admin API returns empty."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    mock_admin, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(user_metadata={}))
    )
    mock_admin_api.update_user_by_id = AsyncMock(return_value=MagicMock(user=None))

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin),
        ),
        patch("supabase_auth.types.Session") as mock_session,
        patch("supabase_auth.types.User") as mock_user_cls,
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_user_cls.return_value = mock_user_response.user
        mock_session.return_value = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_phone_update_no_response():
    """Phone update should fail when admin API returns empty."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    mock_admin, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(user_metadata={}))
    )
    mock_admin_api.update_user_by_id = AsyncMock(return_value=MagicMock(user=None))

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin),
        ),
        patch("supabase_auth.types.Session") as mock_session,
        patch("supabase_auth.types.User") as mock_user_cls,
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_user_cls.return_value = mock_user_response.user
        mock_session.return_value = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "+1234567890",
                VerificationTrigger.PHONE_NUMBER_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_or_phone_generic_exception():
    """Generic Supabase client failures should raise 500."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    with patch(
        "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
        AsyncMock(side_effect=Exception("connection error")),
    ):
        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_email_full_flow_with_retry():
    """Full email update path including retry logic."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user = MagicMock()
    del mock_user.model_dump
    mock_user.dict.return_value = {"id": "user-123"}
    mock_user_response.user = mock_user

    mock_admin_client, mock_admin_api = _build_admin_client()
    mock_admin_api.get_user_by_id = AsyncMock(
        side_effect=[
            MagicMock(user=MagicMock(user_metadata={"email": "old@example.com"})),
            MagicMock(user=MagicMock(user_metadata={"email": "old@example.com"})),
            MagicMock(user=MagicMock(user_metadata={"email": "new@example.com"})),
        ]
    )
    mock_admin_api.update_user_by_id = AsyncMock(
        return_value=MagicMock(user=MagicMock(email="new@example.com"))
    )
    mock_admin_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[{"id": 1}])
    )

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch("supabase_auth.types.Session") as mock_session,
        patch("supabase_auth.types.User") as mock_user_cls,
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_user_cls.return_value = mock_user
        mock_session.return_value = MagicMock()
        mock_client.auth._persist_session = True
        mock_client.auth._storage_key = "key"
        mock_client.auth._storage.set_item = AsyncMock()

        email_updated, phone_updated = await _update_email_or_phone(
            "user-123",
            "new@example.com",
            VerificationTrigger.EMAIL_UPDATE.value,
            "fake-token",
        )

    assert email_updated is True
    assert phone_updated is False


@pytest.mark.asyncio
async def test_update_phone_full_flow_with_plus_stripping():
    """Phone update should retry when Supabase strips '+'."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    mock_admin_client, mock_admin_api = _build_admin_client()
    stripped_response = MagicMock()
    stripped_response.user.phone = "1234567890"
    stripped_response.user.user_metadata = {"phone": "1234567890"}
    corrected_response = MagicMock()
    corrected_response.user.phone = "+1234567890"
    corrected_response.user.user_metadata = {"phone": "+1234567890"}

    mock_admin_api.get_user_by_id = AsyncMock(
        side_effect=[MagicMock(user=MagicMock(user_metadata={})), stripped_response]
    )
    mock_admin_api.update_user_by_id = AsyncMock(
        side_effect=[
            stripped_response,
            Exception("Metadata update failed"),
            stripped_response,
            corrected_response,
        ]
    )
    mock_admin_client.table.return_value.update.side_effect = Exception("Org update failed")

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch("supabase_auth.types.Session"),
        patch("supabase_auth.types.User"),
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_client.auth._persist_session = True
        mock_client.auth._storage.set_item = AsyncMock()

        email_updated, phone_updated = await _update_email_or_phone(
            "user-123",
            "+1234567890",
            VerificationTrigger.PHONE_NUMBER_UPDATE.value,
            "fake-token",
        )

    assert email_updated is False
    assert phone_updated is True


@pytest.mark.asyncio
async def test_update_email_metadata_verification_mismatch():
    """Metadata mismatch should trigger retry logic."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    mock_admin_client, mock_admin_api = _build_admin_client()
    verify_response = MagicMock()
    verify_response.user.user_metadata = {"email": "WRONG@example.com"}
    final_response = MagicMock()
    final_response.user.email = "new@example.com"
    final_response.user.user_metadata = {"email": "new@example.com"}

    mock_admin_api.get_user_by_id = AsyncMock(
        side_effect=[
            MagicMock(user=MagicMock(user_metadata={})),
            verify_response,
            final_response,
        ]
    )
    mock_admin_api.update_user_by_id = AsyncMock(return_value=final_response)
    mock_admin_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch(
            "libs.shared_db.supabase_db.db.get_supabase_admin_client",
            AsyncMock(return_value=mock_admin_client),
        ),
        patch("supabase_auth.types.Session"),
        patch("supabase_auth.types.User"),
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_client.auth._persist_session = True
        mock_client.auth._storage.set_item = AsyncMock()

        email_updated, _ = await _update_email_or_phone(
            "user-123",
            "new@example.com",
            VerificationTrigger.EMAIL_UPDATE.value,
            "fake-token",
        )

    assert email_updated is True


@pytest.mark.asyncio
async def test_session_persistence_error():
    """Storage failures while persisting session should raise 500."""
    from apps.user_service.app.api.verification_codes import _update_email_or_phone

    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()

    with (
        patch(
            "apps.user_service.app.api.verification_codes._get_supabase_client_with_token",
            AsyncMock(return_value=mock_client),
        ),
        patch.object(mock_client.auth, "get_user", AsyncMock(return_value=mock_user_response)),
        patch(
            "apps.user_service.app.api.verification_codes.os.getenv",
            return_value="secret",
        ),
        patch(
            "apps.user_service.app.api.verification_codes.jwt.decode",
            return_value={"sub": "user-123", "exp": int(time.time()) + 3600},
        ),
        patch("supabase_auth.types.Session"),
        patch("supabase_auth.types.User"),
        patch("supabase_auth.helpers.model_dump_json", return_value="{}"),
    ):
        mock_client.auth._persist_session = True
        mock_client.auth._storage_key = "key"
        mock_client.auth._storage.set_item = AsyncMock(side_effect=Exception("storage failed"))

        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "new@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token",
            )
    assert exc.value.status_code == 500


# ============================================================================
# Direct verify endpoint coverage (async invocation)
# ============================================================================


@pytest.mark.asyncio
async def test_verify_ver_code_with_stored_user_id_async(
    mock_verification_record,
):
    """Stored user id should allow verification without auth."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["user_id"] = "stored-user-id"
    mock_record["triggered_text"] = VerificationTrigger.EMAIL_UPDATE.value

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_record["id"],
        verification_code="1111",
        email="test@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = None

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._update_email_or_phone",
            AsyncMock(return_value=(False, False)),
        ),
    ):
        result = await verify_verification_code(request, data, current_user=None)
    assert result.verified is True


@pytest.mark.asyncio
async def test_verify_ver_code_email_update_success_async(
    mock_verification_record,
):
    """Authenticated email update should trigger Supabase update."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["given_input"] = "new@example.com"
    mock_record["user_id"] = "user-123"
    mock_record["triggered_text"] = VerificationTrigger.EMAIL_UPDATE.value

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_record["id"],
        verification_code="1111",
        email="new@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = "fake-token"

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._update_email_or_phone",
            AsyncMock(return_value=(True, False)),
        ),
    ):
        result = await verify_verification_code(request, data, current_user={"sub": "user-123"})
    assert "Email has been updated" in result.message


@pytest.mark.asyncio
async def test_verify_ver_code_phone_update_success_async(
    mock_verification_record,
):
    """Authenticated phone update should trigger Supabase update."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["type_text"] = VerificationType.PHONE_NUMBER.value
    mock_record["given_input"] = "+1234567890"
    mock_record["triggered_text"] = VerificationTrigger.PHONE_NUMBER_UPDATE.value
    mock_record["user_id"] = "user-123"

    data = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verification_id=mock_record["id"],
        verification_code="1111",
        phoneNumber="+1234567890",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = "fake-token"

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._update_email_or_phone",
            AsyncMock(return_value=(False, True)),
        ),
    ):
        result = await verify_verification_code(request, data, current_user={"sub": "user-123"})
    assert "Phone number has been updated" in result.message


@pytest.mark.asyncio
async def test_verify_ver_code_access_token_missing_async(
    mock_verification_record,
):
    """Missing access tokens should raise 401 for update triggers."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["given_input"] = "new@example.com"
    mock_record["triggered_text"] = VerificationTrigger.EMAIL_UPDATE.value
    mock_record["user_id"] = "user-123"

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_record["id"],
        verification_code="1111",
        email="new@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = None

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await verify_verification_code(request, data, current_user={"sub": "user-123"})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_ver_code_skip_update_no_trigger_async(
    mock_verification_record,
):
    """Non-update triggers should skip Supabase update."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["user_id"] = "user-123"
    mock_record["triggered_text"] = VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_record["id"],
        verification_code="1111",
        email="test@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = "fake-token"

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._update_email_or_phone",
            AsyncMock(),
        ) as mock_update,
    ):
        result = await verify_verification_code(request, data, current_user={"sub": "user-123"})
    mock_update.assert_not_called()
    assert "updated" not in result.message.lower()


@pytest.mark.asyncio
async def test_verify_verification_code_no_user_id_async(mock_verification_record):
    """When no user_id stored or provided, verification should still succeed."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    mock_record = mock_verification_record.copy()
    mock_record["user_id"] = None
    mock_record["triggered_text"] = VerificationTrigger.EMAIL_UPDATE.value

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_record["id"],
        verification_code="1111",
        email="test@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = "fake-token"

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.update_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._update_email_or_phone",
            AsyncMock(return_value=(False, False)),
        ),
    ):
        result = await verify_verification_code(request, data, current_user=None)
    assert result.verified is True


@pytest.mark.asyncio
async def test_verify_ver_code_generic_exception_async(
    mock_verification_record,
):
    """Generic DB errors should bubble up as HTTPException 500."""

    from apps.user_service.app.api.verification_codes import verify_verification_code

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=mock_verification_record["id"],
        verification_code="1111",
        email="test@example.com",
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.access_token = None

    with patch(
        "apps.user_service.app.api.verification_codes.get_verification_code_by_id",
        AsyncMock(side_effect=Exception("Database error")),
    ):
        with pytest.raises(HTTPException) as exc:
            await verify_verification_code(request, data, current_user=None)
    assert exc.value.status_code == 500


# ============================================================================
# Additional edge cases for helper utilities
# ============================================================================


@pytest.mark.asyncio
async def test_check_phone_exists_other_user_no_phone_attr():
    """Users without phone attribute should be skipped."""
    from apps.user_service.app.api.verification_codes import (
        _check_phone_exists_for_other_user,
    )

    mock_user = MagicMock()
    del mock_user.phone
    mock_user.id = "other-user"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_phone_attr():
    """Auth users lacking phone field should be ignored."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    del mock_user.phone

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        assert await _check_auth_user_exists_by_phone("+1234567890") is False


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_user_data():
    """None response from get_user_by_id should not raise."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_by_id",
        AsyncMock(return_value=None),
    ):
        await _validate_phone_for_update("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_user_attr():
    """User data missing .user attribute should not raise."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    mock_user_data = MagicMock()
    del mock_user_data.user

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_supabase),
        ),
    ):
        await _validate_phone_for_update("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_no_phone_field():
    """Users without phone field should pass validation."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    mock_user_data = MagicMock()
    mock_user = MagicMock()
    del mock_user.phone
    mock_user_data.user = mock_user

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_supabase),
        ),
    ):
        await _validate_phone_for_update("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_no_sub():
    """Missing sub claim should return None user_id."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    current_user = {"email": "current@example.com"}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    with patch(
        "apps.user_service.app.api.verification_codes._validate_email_for_update",
        AsyncMock(),
    ):
        user_id, trigger = await _validate_authenticated_user_input(data, current_user)
    assert user_id is None
    assert trigger == VerificationTrigger.EMAIL_UPDATE.value


def test_determine_triggered_text_authenticated_phone():
    """Test _determine_triggered_text for authenticated user phone - covers lines 421-424."""
    from apps.user_service.app.api.verification_codes import _determine_triggered_text

    data = SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER, phoneNumber="1234567890")
    current_user = {"sub": "user-123"}

    result = _determine_triggered_text(data, current_user)
    assert result == VerificationTrigger.PHONE_NUMBER_UPDATE.value


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_success():
    """Test _check_auth_user_exists_by_phone when phone exists."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    # Set phone field (not user_metadata) since we check the actual phone field
    mock_user.phone = "9876543210"
    mock_user.user_metadata = {}

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is True


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_not_found():
    """Test _check_auth_user_exists_by_phone when phone doesn't exist - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    mock_user.user_metadata = {"phone": "1111111111"}  # Different phone

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_metadata():
    """Test _check_auth_user_exists_by_phone when user has no metadata - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    mock_user.user_metadata = None

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_exception():
    """Test _check_auth_user_exists_by_phone when exception occurs - covers lines 189-191."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(side_effect=Exception("Database error")),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_empty_list():
    """Test _check_auth_user_exists_by_phone when user list is empty - covers lines 169-191."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_no_phone():
    """Test _check_auth_user_exists_by_phone when user_metadata exists but no phone.

    Covers lines 169-191.
    """
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    mock_user.user_metadata = {"email": "test@example.com"}  # No phone field

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("9876543210")
        assert result is False


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_no_user_id():
    """Test _validate_authenticated_user_input with no user_id - covers line 452."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"email": "old@example.com"}  # No 'sub' field

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        user_id, _ = await _validate_authenticated_user_input(data, current_user)
        # Should still work, just with warning logged
        assert user_id is None or user_id == ""


# ============================================================================
# ADDITIONAL HELPER + VALIDATION TESTS (merged from auxiliary suites)
# ============================================================================


def test_sanitize_ip_valid_ipv4():
    """Ensure IPv4 addresses are accepted."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    assert _sanitize_ip("192.168.1.1") == "192.168.1.1"


def test_sanitize_ip_valid_ipv6():
    """Ensure IPv6 addresses are accepted."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    ipv6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
    assert _sanitize_ip(ipv6) == ipv6


def test_sanitize_ip_with_comma():
    """First IP before comma should be returned."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    assert _sanitize_ip(" 192.168.1.1 , 10.0.0.1 ") == "192.168.1.1"


def test_sanitize_ip_invalid():
    """Invalid IP strings return None."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    assert _sanitize_ip("invalid-ip") is None


def test_sanitize_ip_none():
    """None input returns None."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    assert _sanitize_ip(None) is None


def test_sanitize_ip_empty():
    """Empty string returns None."""
    from apps.user_service.app.api.verification_codes import _sanitize_ip

    assert _sanitize_ip("") is None


def test_get_client_ip_from_client_host():
    """Client host should be used when headers missing."""

    from apps.user_service.app.api.verification_codes import get_client_ip

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"

    assert get_client_ip(mock_request) == "127.0.0.1"


def test_get_client_ip_invalid_host():
    """Invalid host should fall back to original value."""

    from apps.user_service.app.api.verification_codes import get_client_ip

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "invalid-ip-address"

    assert get_client_ip(mock_request) == "invalid-ip-address"


def test_get_client_ip_no_client():
    """Unknown should be returned when client missing."""

    from apps.user_service.app.api.verification_codes import get_client_ip

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = None

    assert get_client_ip(mock_request) == "unknown"


def test_normalize_phone_with_plus():
    """Leading + should be stripped."""
    from apps.user_service.app.api.verification_codes import _normalize_phone

    assert _normalize_phone("+1234567890") == "1234567890"


def test_normalize_phone_without_plus():
    """Phone strings without + stay unchanged."""
    from apps.user_service.app.api.verification_codes import _normalize_phone

    assert _normalize_phone("1234567890") == "1234567890"


def test_normalize_phone_multiple_plus():
    """Only the leading + symbols should be removed."""
    from apps.user_service.app.api.verification_codes import _normalize_phone

    assert _normalize_phone("++1234567890") == "1234567890"


def test_normalize_phone_none():
    """None input should return None."""
    from apps.user_service.app.api.verification_codes import _normalize_phone

    assert _normalize_phone(None) is None


def test_normalize_phone_empty():
    """Empty string should remain empty."""
    from apps.user_service.app.api.verification_codes import _normalize_phone

    assert _normalize_phone("") == ""


@pytest.mark.asyncio
async def test_validate_email_for_update_same_email():
    """Entering the existing email should raise."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update

    with pytest.raises(HTTPException) as exc:
        await _validate_email_for_update("test@example.com", "user-123", "test@example.com")
    assert exc.value.status_code == 400
    assert "same as your current email" in exc.value.detail


@pytest.mark.asyncio
async def test_validate_email_update_exists_other_user():
    """Duplicate email for another user should raise 409."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update

    mock_user = MagicMock()
    mock_user.id = "other-user"

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(return_value=mock_user),
    ):
        with pytest.raises(HTTPException) as exc:
            await _validate_email_for_update(
                "existing@example.com", "user-123", "current@example.com"
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_validate_email_update_exists_same_user():
    """Same user reusing their email should be allowed."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update

    mock_user = MagicMock()
    mock_user.id = "user-123"

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(return_value=mock_user),
    ):
        await _validate_email_for_update("existing@example.com", "user-123", "current@example.com")


@pytest.mark.asyncio
async def test_validate_email_for_update_get_user_fails():
    """Errors in lookup should be swallowed."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(side_effect=Exception("db error")),
    ):
        await _validate_email_for_update("new@example.com", "user-123", "current@example.com")


@pytest.mark.asyncio
async def test_validate_email_for_update_email_not_found():
    """Missing emails should pass validation."""
    from apps.user_service.app.api.verification_codes import _validate_email_for_update

    with patch(
        "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        await _validate_email_for_update("new@example.com", "user-123", "current@example.com")


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_phone_exists():
    """Duplicate phone for other user should raise 409."""
    from apps.user_service.app.api.verification_codes import (
        _check_phone_exists_for_other_user,
    )

    mock_other_user = MagicMock()
    mock_other_user.id = "other-user"
    mock_other_user.phone = "+1234567890"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        with pytest.raises(HTTPException) as exc:
            await _check_phone_exists_for_other_user("+1234567890", "user-123")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_check_phone_exists_other_user_not_exists():
    """Unique phone numbers should pass."""
    from apps.user_service.app.api.verification_codes import (
        _check_phone_exists_for_other_user,
    )

    mock_other_user = MagicMock()
    mock_other_user.id = "other-user"
    mock_other_user.phone = "+9999999999"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_phone_exists_other_user_normalized():
    """Normalization should compare phones without +."""
    from apps.user_service.app.api.verification_codes import (
        _check_phone_exists_for_other_user,
    )

    mock_other_user = MagicMock()
    mock_other_user.id = "other-user"
    mock_other_user.phone = "1234567890"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_other_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        with pytest.raises(HTTPException):
            await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_error():
    """Errors from Supabase client should propagate."""
    from apps.user_service.app.api.verification_codes import (
        _check_phone_exists_for_other_user,
    )

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(side_effect=RuntimeError("network error")),
    ):
        with pytest.raises(RuntimeError):
            await _check_phone_exists_for_other_user("+1234567890", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_same_phone():
    """Using the same phone should raise."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1234567890"

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_by_id",
        AsyncMock(return_value=mock_user_data),
    ):
        with pytest.raises(HTTPException) as exc:
            await _validate_phone_for_update("+1234567890", "user-123")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_phone_for_update_different_phone():
    """New phones should pass validation."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1111111111"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[])

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_supabase),
        ),
    ):
        await _validate_phone_for_update("+2222222222", "user-123")


@pytest.mark.asyncio
async def test_validate_phone_for_update_get_user_fails():
    """Lookup failures should not break flow."""
    from apps.user_service.app.api.verification_codes import _validate_phone_for_update

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_by_id",
        AsyncMock(side_effect=Exception("db error")),
    ):
        await _validate_phone_for_update("+2222222222", "user-123")


def test_determine_triggered_text_authenticated_email():
    """Authenticated email requests map to EMAIL_UPDATE."""
    from apps.user_service.app.api.verification_codes import _determine_triggered_text

    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")
    current_user = {"sub": "user-123"}

    assert _determine_triggered_text(data, current_user) == VerificationTrigger.EMAIL_UPDATE.value


def test_determine_triggered_text_unauthenticated_email():
    """Signup email requests map to signup trigger."""
    from apps.user_service.app.api.verification_codes import _determine_triggered_text

    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    assert (
        _determine_triggered_text(data, None) == VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
    )


def test_determine_triggered_text_unauthenticated_phone():
    """Signup phone requests map to signup trigger."""
    from apps.user_service.app.api.verification_codes import _determine_triggered_text

    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890"
    )

    assert (
        _determine_triggered_text(data, None) == VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value
    )


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_email():
    """Validate flow for email updates."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    current_user = {"sub": "user-123", "email": "current@example.com"}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.email = "current@example.com"

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._validate_email_for_update",
            AsyncMock(),
        ),
    ):
        user_id, trigger = await _validate_authenticated_user_input(data, current_user)
    assert user_id == "user-123"
    assert trigger == VerificationTrigger.EMAIL_UPDATE.value


@pytest.mark.asyncio
async def test_validate_authenticated_user_input_phone():
    """Validate flow for phone updates."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    current_user = {"sub": "user-123"}
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER, phoneNumber="+1234567890"
    )

    with patch(
        "apps.user_service.app.api.verification_codes._validate_phone_for_update",
        AsyncMock(),
    ):
        user_id, trigger = await _validate_authenticated_user_input(data, current_user)
    assert user_id == "user-123"
    assert trigger == VerificationTrigger.PHONE_NUMBER_UPDATE.value


@pytest.mark.asyncio
async def test_validate_auth_user_input_get_user_fails():
    """Should fall back gracefully when get_user_by_id fails."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    current_user = {"sub": "user-123", "email": "current@example.com"}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(side_effect=Exception("db error")),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._validate_email_for_update",
            AsyncMock(),
        ),
    ):
        user_id, trigger = await _validate_authenticated_user_input(data, current_user)
    assert user_id == "user-123"
    assert trigger == VerificationTrigger.EMAIL_UPDATE.value


def test_get_optional_user_with_user():
    """If request has user, it should be returned."""

    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = {"sub": "user-123"}

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        return_value={"sub": "user-123"},
    ):
        assert get_optional_user(mock_request)["sub"] == "user-123"


@pytest.mark.asyncio
async def test_get_supabase_client_with_token_success(monkeypatch):
    """Happy path for creating Supabase client with token."""
    from apps.user_service.app.api.verification_codes import (
        _get_supabase_client_with_token,
    )

    mock_client = MagicMock()

    async def fake_create_async_client(url, key, options):
        assert url == "https://test.supabase.co"
        assert key == "anon-key"
        assert "Authorization" in options.headers
        return mock_client

    monkeypatch.setattr(
        "apps.user_service.app.api.verification_codes.create_async_client",
        fake_create_async_client,
    )

    with patch("apps.user_service.app.api.verification_codes.os.getenv") as mock_getenv:
        mock_getenv.side_effect = lambda key, default=None: {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_ANON_KEY": "anon-key",
        }.get(key, default)

        client = await _get_supabase_client_with_token("fake-token")
    assert client is mock_client


@pytest.mark.asyncio
async def test_get_supabase_client_with_token_missing_url():
    """Missing Supabase URL should raise."""
    from apps.user_service.app.api.verification_codes import (
        _get_supabase_client_with_token,
    )

    with patch("apps.user_service.app.api.verification_codes.os.getenv") as mock_getenv:
        mock_getenv.side_effect = lambda key, default=None: {
            "SUPABASE_URL": None,
            "SUPABASE_ANON_KEY": "anon-key",
        }.get(key, default)

        with pytest.raises(RuntimeError):
            await _get_supabase_client_with_token("fake-token")


@pytest.mark.asyncio
async def test_get_supabase_client_with_token_missing_key():
    """Missing anon key should raise."""
    from apps.user_service.app.api.verification_codes import (
        _get_supabase_client_with_token,
    )

    with patch("apps.user_service.app.api.verification_codes.os.getenv") as mock_getenv:
        mock_getenv.side_effect = lambda key, default=None: {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_ANON_KEY": None,
        }.get(key, default)

        with pytest.raises(RuntimeError):
            await _get_supabase_client_with_token("fake-token")


# ============================================================================
# Verification record + ownership helpers
# ============================================================================


def test_validate_verification_record_not_found():
    """Missing record should raise 404."""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=str(uuid.uuid4()),
        verification_code="1111",
        email="test@example.com",
    )

    with pytest.raises(HTTPException) as exc:
        _validate_verification_record(None, data)
    assert exc.value.status_code == 404


def test_validate_verification_record_already_verified():
    """Already verified records should raise 400."""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=str(uuid.uuid4()),
        verification_code="1111",
        email="test@example.com",
    )

    record = {
        "verified": True,
        "given_input": "test@example.com",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
    }

    with pytest.raises(HTTPException) as exc:
        _validate_verification_record(record, data)
    assert exc.value.status_code == 400
    assert "already been verified" in exc.value.detail


def test_validate_verification_record_expired():
    """Expired records should raise 400."""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=str(uuid.uuid4()),
        verification_code="1111",
        email="test@example.com",
    )

    record = {
        "verified": False,
        "given_input": "test@example.com",
        "expiry_at": int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000),
    }

    with pytest.raises(HTTPException):
        _validate_verification_record(record, data)


def test_validate_verification_record_input_mismatch():
    """Mismatched email/phone should raise 400."""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id=str(uuid.uuid4()),
        verification_code="1111",
        email="different@example.com",
    )

    record = {
        "verified": False,
        "given_input": "test@example.com",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp() * 1000),
    }

    with pytest.raises(HTTPException):
        _validate_verification_record(record, data)


def test_validate_verification_record_phone_number():
    """PHONE_NUMBER type should compare against phoneNumber field."""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verification_id=str(uuid.uuid4()),
        verification_code="1111",
        phoneNumber="+1234567890",
    )

    record = {
        "verified": False,
        "given_input": "+1234567890",
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp() * 1000),
    }

    assert _validate_verification_record(record, data) == "+1234567890"


def test_check_ver_code_ownership_no_current_user():
    """Ownership check should pass when unauthenticated."""
    from apps.user_service.app.api.verification_codes import (
        _check_verification_code_ownership,
    )

    _check_verification_code_ownership({"user_id": "user-123"}, None)


def test_check_ver_code_ownership_no_stored_user_id():
    """Signup codes lacking user_id should skip ownership check."""
    from apps.user_service.app.api.verification_codes import (
        _check_verification_code_ownership,
    )

    _check_verification_code_ownership({"user_id": None}, {"sub": "user-123"})


@pytest.mark.asyncio
async def test_verify_code_and_update_record_correct_code():
    """Happy path for verifying a correct code."""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    record = {"verification_code": "1111", "attempts": []}

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(),
    ):
        assert await _verify_code_and_update_record(record, "1111", "verification-id")


@pytest.mark.asyncio
async def test_verify_code_and_update_record_wrong_code():
    """Wrong codes should raise 400."""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    record = {"verification_code": "1111", "attempts": []}

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(),
    ):
        with pytest.raises(HTTPException):
            await _verify_code_and_update_record(record, "9999", "verification-id")


@pytest.mark.asyncio
async def test_verify_code_update_record_existing_attempts():
    """Existing attempts should be preserved and appended."""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    record = {
        "verification_code": "1234",
        "attempts": [{"entered_value": "0000", "matched": False, "success": False}],
    }

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(),
    ):
        await _verify_code_and_update_record(record, "1234", "verification-id")


# ============================================================================
# Additional coverage tests for uncovered lines
# ============================================================================


@pytest.mark.asyncio
async def test_send_ver_code_method_provided():
    """Test send ver code when verification_method is provided in request."""

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.access_token = None

    data = SendVerificationCodeRequest(
        type=VerificationType.EMAIL,
        email="new@example.com",
        verification_method="CUSTOM_TRIGGER",
    )

    mock_record = {
        "id": str(uuid.uuid4()),
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "verification_code": "1111",
    }

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_optional_user",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_auth_user_by_email",
            AsyncMock(return_value=None),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
            AsyncMock(return_value=[]),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.create_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.send_verification_code_email",
            return_value=True,
        ),
    ):
        result = await send_verification_code(mock_request, data, None)
        assert result.verification_id == mock_record["id"]


@pytest.mark.asyncio
async def test_send_ver_code_method_authenticated():
    """Test send ver code with verification_method when authenticated."""

    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.access_token = None

    current_user = {"sub": "user-123", "email": "current@example.com"}
    data = SendVerificationCodeRequest(
        type=VerificationType.EMAIL,
        email="new@example.com",
        verification_method="CUSTOM_TRIGGER",
    )

    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.email = "current@example.com"

    mock_record = {
        "id": str(uuid.uuid4()),
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "verification_code": "1111",
    }

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_optional_user",
            return_value=current_user,
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._validate_email_for_update",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
            AsyncMock(return_value=[]),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.create_verification_code",
            AsyncMock(return_value=mock_record),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.send_verification_code_email",
            return_value=True,
        ),
    ):
        result = await send_verification_code(mock_request, data, current_user)
        assert result.verification_id == mock_record["id"]


def test_check_ver_code_ownership_no_sub_in_user():
    """Test ownership check when user has no 'sub' key."""
    from apps.user_service.app.api.verification_codes import (
        _check_verification_code_ownership,
    )

    verification_record = {"user_id": "user-123"}
    current_user = {"email": "test@example.com"}  # No 'sub' key

    # Should not raise, just log warning
    with patch("apps.user_service.app.api.verification_codes.logger") as mock_logger:
        _check_verification_code_ownership(verification_record, current_user)
        # Should log warning about missing sub
        assert mock_logger.warning.called


def test_check_ver_code_ownership_no_current_user_id():
    """Test ownership check when current_user_id is None."""
    from apps.user_service.app.api.verification_codes import (
        _check_verification_code_ownership,
    )

    verification_record = {"user_id": "user-123"}
    current_user = {"sub": None}  # sub is None

    # Should not raise
    _check_verification_code_ownership(verification_record, current_user)


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_plus_norm():
    """Test phone existence check with + prefix normalization."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_user = MagicMock()
    mock_user.phone = "+1234567890"

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(return_value=[mock_user])

    with patch(
        "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
        AsyncMock(return_value=mock_supabase),
    ):
        result = await _check_auth_user_exists_by_phone("1234567890")  # Without +
        assert result is True  # Should match after normalization


@pytest.mark.asyncio
async def test_check_auth_user_exists_by_phone_list_users_ex():
    """Test phone existence check when list_users raises exception."""
    from apps.user_service.app.api.verification_codes import (
        _check_auth_user_exists_by_phone,
    )

    mock_supabase = MagicMock()
    mock_supabase.auth.admin.list_users = AsyncMock(side_effect=Exception("DB error"))

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_supabase_admin_client",
            AsyncMock(return_value=mock_supabase),
        ),
        patch("apps.user_service.app.api.verification_codes.logger") as mock_logger,
    ):
        result = await _check_auth_user_exists_by_phone("+1234567890")
        assert result is False  # Should return False on error
        mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_validate_auth_user_input_ex_in_get_user():
    """Test authenticated user input when get_user_by_id raises exception."""
    from apps.user_service.app.api.verification_codes import (
        _validate_authenticated_user_input,
    )

    current_user = {"sub": "user-123", "email": "jwt@example.com"}
    data = SendVerificationCodeRequest(type=VerificationType.EMAIL, email="new@example.com")

    # Make get_user_by_id raise an exception to trigger the warning
    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(side_effect=Exception("DB error")),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._validate_email_for_update",
            AsyncMock(),
        ),
        patch("apps.user_service.app.api.verification_codes.logger") as mock_logger,
    ):
        user_id, _ = await _validate_authenticated_user_input(data, current_user)
        assert user_id == "user-123"
        # Should log warning when exception occurs and fall back to JWT email
        mock_logger.warning.assert_called_once()
        assert "Could not get current email from auth" in mock_logger.warning.call_args[0][0]


@pytest.mark.asyncio
async def test_get_optional_user_ex_in_get_user_from_auth():
    """Test get_optional_user when get_user_from_auth raises exception."""
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    mock_request.state.user = {"sub": "user-123"}

    with patch(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        side_effect=Exception("Auth error"),
    ):
        result = get_optional_user(mock_request)
        # Should return None on exception
        assert result is None


@pytest.mark.asyncio
async def test_send_ver_code_method_phone_unauthenticated():
    """Test send verification code with verification_method for phone when unauthenticated."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.access_token = None

    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phoneNumber="+1234567890",
        verification_method="CUSTOM_TRIGGER",
    )

    mock_record = {
        "id": str(uuid.uuid4()),
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "verification_code": "1111",
    }

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_optional_user",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.verification_codes._check_auth_user_exists_by_phone",
            AsyncMock(return_value=False),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
            AsyncMock(return_value=[]),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.create_verification_code",
            AsyncMock(return_value=mock_record),
        ),
    ):
        result = await send_verification_code(mock_request, data, None)
        assert result.verification_id == mock_record["id"]


@pytest.mark.asyncio
async def test_send_ver_code_method_phone_authenticated():
    """Test send verification code with verification_method for phone when authenticated."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.state = MagicMock()
    mock_request.state.access_token = None

    current_user = {"sub": "user-123"}
    data = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phoneNumber="+1234567890",
        verification_method="CUSTOM_TRIGGER",
    )

    mock_user_data = MagicMock()
    mock_user_data.user = MagicMock()
    mock_user_data.user.phone = "+1111111111"

    mock_record = {
        "id": str(uuid.uuid4()),
        "expiry_at": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000),
        "verification_code": "1111",
    }

    with (
        patch(
            "apps.user_service.app.api.verification_codes.get_optional_user",
            return_value=current_user,
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_user_by_id",
            AsyncMock(return_value=mock_user_data),
        ),
        patch(
            "apps.user_service.app.api.verification_codes._validate_phone_for_update",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.get_recent_verification_codes",
            AsyncMock(return_value=[]),
        ),
        patch(
            "apps.user_service.app.api.verification_codes.create_verification_code",
            AsyncMock(return_value=mock_record),
        ),
    ):
        result = await send_verification_code(mock_request, data, current_user)
        assert result.verification_id == mock_record["id"]
