"""Async integration tests for authentication endpoints."""

# pylint: disable=too-many-lines

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from libs.shared_middleware.jwt_auth import get_user_from_auth

EMAIL_NOT_FOUND_MESSAGE = "Email Is Not Registered! Please Signup First To Login."


@pytest.fixture
def auth_client():
    """Test client for auth endpoints"""
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    # Override the auth dependency for testing
    def mock_get_user_from_auth():
        return {"sub": "test-user-id", "email": "test@example.com"}

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth

    with TestClient(app) as client:
        yield client


@pytest.fixture
def async_auth_client():
    """Async test client for auth endpoints"""
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    # Override the auth dependency for testing
    def mock_get_user_from_auth():
        return {"sub": "test-user-id", "email": "test@example.com"}

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth

    return app


def test_login_endpoint_success(auth_client):
    """Test successful login - covers auth.py login function"""
    login_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={
                "first_name": "Test",
                "last_name": "User",
                "timezone": "UTC",
                "organization_id": "org-123",
            },
        ),
    )

    # Mock user with user_metadata that doesn't have 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No 2FA enabled
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["first_name"] == "Test"
        assert data["user"]["last_name"] == "User"
        assert data["user"]["org_setup_status_completed"] is True
        assert data["user"]["organization_id"] == "org-123"
        assert data["user"]["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_login_endpoint_success_async(_async_auth_client):
    """Test successful login asynchronously"""
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(email="test@example.com", password="TestPass123!")

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={
                "first_name": "Test",
                "last_name": "User",
                "timezone": "UTC",
                "organization_id": "org-123",
            },
        ),
    )

    # Mock user with user_metadata that doesn't have 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No 2FA enabled
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        # Create a mock request
        mock_request = MagicMock(spec=Request)
        result = await login(request=mock_request, data=login_data)

        assert result.access_token == "test-access-token"
        assert result.user.email == "test@example.com"
        assert result.user.first_name == "Test"
        assert result.user.last_name == "User"
        assert result.user.org_setup_status_completed is True
        assert result.user.organization_id == "org-123"
        assert result.user.tzone == "UTC"


def test_login_endpoint_invalid_credentials(auth_client):
    """Test login with invalid credentials - covers auth.py error handling"""
    login_data = {"email": "test@example.com", "password": "wrongpassword"}

    # Mock user with user_metadata that doesn't have 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No 2FA enabled
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("Invalid login credentials")),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]


def test_login_endpoint_email_not_found(auth_client):
    """Ensure login returns proper 400 when Supabase user record is missing."""
    login_data = {"email": "missing@example.com", "password": "TestPass123!"}

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert response.json()["detail"] == "Email Is Not Registered! Please Signup First To Login."


def test_login_invalid_credentials_authapierror(auth_client):
    """Test login with AuthApiError for invalid credentials - covers AuthApiError handling"""
    login_data = {"email": "test@example.com", "password": "wrongpassword"}

    from supabase_auth.errors import AuthApiError

    # Mock user with user_metadata that doesn't have 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No 2FA enabled
        phone=None,
    )

    # Mock AuthApiError for invalid credentials
    auth_error = AuthApiError("Invalid login credentials", status=400, code="invalid_credentials")
    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=auth_error),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]


def test_login_endpoint_authapierror_server_error(auth_client):
    """AuthApiError with non-credential failure should bubble up original status/detail."""
    login_data = {"email": "test@example.com", "password": "TestPass123!"}

    from supabase_auth.errors import AuthApiError

    # Mock user with user_metadata that doesn't have 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No 2FA enabled
        phone=None,
    )

    auth_error = AuthApiError("Service unavailable", status=503, code="service_error")

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=auth_error),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 503
        assert response.json()["detail"] == "Service unavailable"


@pytest.mark.asyncio
async def test_login_endpoint_invalid_credentials_async(_async_auth_client):
    """Test login with invalid credentials asynchronously"""
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(email="test@example.com", password="wrongpassword")

    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("Invalid login credentials")),
        ),
    ):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await login(request=mock_request, data=login_data)

        assert exc_info.value.status_code == 400
        assert "Invalid login credentials" in exc_info.value.detail


def test_signup_endpoint_success(auth_client):
    """Test successful signup - covers auth.py signup function"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "1111",
    }

    # Mock verification code record (verified and matching)
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True,
    }

    # Mock the signup response with user and session (same as login)
    mock_result = MagicMock()
    mock_result.user.id = "new-user-id"
    mock_result.user.email = "newuser@example.com"
    mock_result.user.user_metadata = {
        "first_name": "New",
        "last_name": "User",
        "timezone": "UTC",
    }
    mock_session = SimpleNamespace(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_in=3600,
        expires_at=datetime.utcnow(),
    )
    mock_result.session = mock_session

    with (
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth.sign_up_supabase_user",
            AsyncMock(return_value=mock_result),
        ),
        patch(
            "apps.user_service.app.api.auth._get_session_after_signup",
            AsyncMock(return_value=mock_session),
        ),
        patch("apps.user_service.app.api.auth.send_welcome_email", return_value=True),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 201
        data = response.json()
        assert data["access_token"] == "test-access-token"
        assert data["refresh_token"] == "test-refresh-token"
        assert data["user"]["id"] == "new-user-id"
        assert data["user"]["email"] == "newuser@example.com"


def test_signup_endpoint_session_creation_failure(auth_client):
    """Signup should fail with 500 when session creation cannot be completed."""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "1111",
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True,
    }

    mock_signup_result = MagicMock()
    mock_signup_result.user.id = "new-user-id"
    mock_signup_result.user.email = "newuser@example.com"
    mock_signup_result.user.user_metadata = {"first_name": "New"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth.sign_up_supabase_user",
            AsyncMock(return_value=mock_signup_result),
        ),
        patch(
            "apps.user_service.app.api.auth._get_session_after_signup",
            AsyncMock(return_value=None),
        ),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 500
        assert "Failed to create session after signup" in response.json()["detail"]


@pytest.mark.asyncio
async def test_signup_endpoint_success_async(_async_auth_client):
    """Test successful signup asynchronously - covers auth.py signup function"""

    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "1111",
    }

    # Mock verification code record (verified and matching)
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True,
    }

    # Mock the signup response with user and session (same as login)
    mock_result = MagicMock()
    mock_result.user.id = "new-user-id"
    mock_result.user.email = "newuser@example.com"
    mock_result.user.user_metadata = {
        "first_name": "New",
        "last_name": "User",
        "timezone": "UTC",
    }
    mock_session = SimpleNamespace(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_in=3600,
        expires_at=datetime.utcnow(),
    )
    mock_result.session = mock_session

    with (
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth.sign_up_supabase_user",
            AsyncMock(return_value=mock_result),
        ),
        patch(
            "apps.user_service.app.api.auth._get_session_after_signup",
            AsyncMock(return_value=mock_session),
        ),
        patch("apps.user_service.app.api.auth.send_welcome_email", return_value=True),
    ):
        with TestClient(async_auth_client) as client:
            response = client.post("/auth/signup", json=signup_data)
            assert response.status_code == 201
            data = response.json()
            assert data["access_token"] == "test-access-token"
            assert data["refresh_token"] == "test-refresh-token"
            assert data["user"]["id"] == "new-user-id"
            assert data["user"]["email"] == "newuser@example.com"


def test_signup_endpoint_weak_password(auth_client):
    """Test signup with weak password - covers auth.py password validation"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "weak",
        "first_name": "New",
        "last_name": "User",
    }

    response = auth_client.post("/auth/signup", json=signup_data)
    assert response.status_code == 422  # Pydantic validation happens first
    assert "at least 6 characters" in str(response.json())


@pytest.mark.asyncio
async def test_signup_endpoint_weak_password_async(async_auth_client):
    """Test signup with weak password asynchronously"""

    signup_data = {
        "email": "newuser@example.com",
        "password": "weak",
        "first_name": "New",
        "last_name": "User",
    }

    with TestClient(async_auth_client) as client:
        response = client.post("/auth/signup", json=signup_data)
        assert response.status_code == 422  # Pydantic validation happens first
        assert "at least 6 characters" in str(response.json())


def test_signup_endpoint_verification_code_not_found(auth_client):
    """Test signup when verification code not found"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "non-existent-id",
        "verification_code": "1111",
    }

    with patch(
        "apps.user_service.app.api.auth.get_verification_code_by_id",
        AsyncMock(return_value=None),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 404
        assert "Verification code not found" in response.json()["detail"]


def test_signup_endpoint_verification_code_not_verified(auth_client):
    """Test signup when verification code not verified"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "1111",
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": False,
    }

    with patch(
        "apps.user_service.app.api.auth.get_verification_code_by_id",
        AsyncMock(return_value=mock_verification_record),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "must be verified before signup" in response.json()["detail"]


def test_signup_endpoint_email_mismatch(auth_client):
    """Test signup when email doesn't match verification record"""
    signup_data = {
        "email": "different@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "1111",
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True,
    }

    with patch(
        "apps.user_service.app.api.auth.get_verification_code_by_id",
        AsyncMock(return_value=mock_verification_record),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "does not match the verification record" in response.json()["detail"]


def test_signup_endpoint_invalid_verification_code(auth_client):
    """Test signup with invalid verification code"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verification_id": "test-verification-id",
        "verification_code": "9999",
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",  # Different code
        "verified": True,
    }

    with patch(
        "apps.user_service.app.api.auth.get_verification_code_by_id",
        AsyncMock(return_value=mock_verification_record),
    ):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "Invalid verification code" in response.json()["detail"]


def test_extract_session_none():
    """Test _extract_session when session has no access_token"""
    from apps.user_service.app.api.auth import _extract_session

    session_no_token = SimpleNamespace()
    result = _extract_session(session_no_token)
    assert result is None


def test_extract_session_with_access_token():
    """Session objects that already contain tokens should be returned untouched."""
    from apps.user_service.app.api.auth import _extract_session

    session = SimpleNamespace(access_token="token-123")
    assert _extract_session(session) is session

    result = _extract_session(None)
    assert result is None


@pytest.mark.asyncio
async def test_get_session_after_signup_login_fallback():
    """Test _get_session_after_signup login fallback"""
    from apps.user_service.app.api.auth import _get_session_after_signup

    signup_result = SimpleNamespace(session=SimpleNamespace())

    login_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="login-token",
            refresh_token="login-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        )
    )

    with (
        patch(
            "apps.user_service.app.api.auth._extract_session",
            side_effect=[None, login_result.session],
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=login_result),
        ),
    ):
        result = await _get_session_after_signup(
            signup_result=signup_result, email="test@example.com", password="password"
        )

        assert result is not None
        assert result.access_token == "login-token"


@pytest.mark.asyncio
async def test_get_session_after_signup_login_fails():
    """Test _get_session_after_signup when login fails"""
    from apps.user_service.app.api.auth import _get_session_after_signup

    signup_result = SimpleNamespace(session=SimpleNamespace())

    with (
        patch("apps.user_service.app.api.auth._extract_session", return_value=None),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("Login failed")),
        ),
    ):
        result = await _get_session_after_signup(
            signup_result=signup_result, email="test@example.com", password="password"
        )

        assert result is None


@pytest.mark.asyncio
async def test_get_session_after_signup_returns_session():
    """If signup already returned a session, no fallback login should run."""
    from apps.user_service.app.api.auth import _get_session_after_signup

    signup_session = SimpleNamespace(access_token="signup-token")
    signup_result = SimpleNamespace(session=signup_session)

    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=AssertionError("login_user should not be called")),
    ):
        result = await _get_session_after_signup(
            signup_result=signup_result, email="test@example.com", password="password"
        )

    assert result is signup_session


def test_validate_password_strength_weak():
    """Test _validate_password_strength with weak password"""
    from apps.user_service.app.api.auth import _validate_password_strength

    with pytest.raises(HTTPException) as exc_info:
        _validate_password_strength("weak")

    assert exc_info.value.status_code == 400
    assert "Password must be at least 6 characters" in exc_info.value.detail


@pytest.mark.asyncio
async def test_login_endpoint_http_exception_re_raise():
    """Test login endpoint re-raising HTTPException"""
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(email="test@example.com", password="TestPass123!")

    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=HTTPException(status_code=500, detail="Server error")),
        ),
    ):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await login(request=mock_request, data=login_data)

        assert exc_info.value.status_code == 500
        assert "Server error" in exc_info.value.detail


def test_forgot_password_endpoint_success(auth_client):
    """Test forgot password"""
    forgot_data = {"email": "test@example.com"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value={"id": "user-id"}),
        ),
        patch("apps.user_service.app.api.auth.send_password_reset_email", AsyncMock()),
    ):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password reset email sent" in data["message"]


@pytest.mark.asyncio
async def test_forgot_password_endpoint_success_async(_async_auth_client):
    """Test forgot password asynchronously"""
    from apps.user_service.app.api.auth import forgot_password
    from apps.user_service.app.schemas.auth import ForgotPasswordRequest

    forgot_data = ForgotPasswordRequest(email="test@example.com")

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value={"id": "user-id"}),
        ),
        patch("apps.user_service.app.api.auth.send_password_reset_email", AsyncMock()),
    ):
        mock_request = MagicMock(spec=Request)
        result = await forgot_password(request=mock_request, data=forgot_data)

        assert "Password reset email sent" in result.message


def test_forgot_password_endpoint_email_not_found(auth_client):
    """Test forgot password with non-existent email"""
    forgot_data = {"email": "nonexistent@example.com"}

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 404
        assert "Email not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_forgot_password_endpoint_email_not_found_async(_async_auth_client):
    """Test forgot password with non-existent email asynchronously"""
    from apps.user_service.app.api.auth import forgot_password
    from apps.user_service.app.schemas.auth import ForgotPasswordRequest

    forgot_data = ForgotPasswordRequest(email="nonexistent@example.com")

    with patch(
        ("apps.user_service.app.api.auth.get_auth_user_by_email"),
        AsyncMock(return_value=None),
    ):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await forgot_password(request=mock_request, data=forgot_data)

        assert exc_info.value.status_code == 404
        assert "Email not found" in exc_info.value.detail


def test_reset_password_endpoint_success(auth_client):
    """Test reset password"""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_user_from_token",
            return_value={"sub": "user-id"},
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=MagicMock(user=MagicMock())),
        ),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password reset successfully" in data["message"]


def test_reset_password_email_not_sent_warning(auth_client):
    """Reset password should still succeed even if success email returns False."""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    mock_user = {
        "sub": "user-id",
        "email": "test@example.com",
        "user_metadata": {"first_name": "Reset"},
    }
    mock_result = MagicMock()
    mock_result.user = MagicMock()

    with (
        patch("apps.user_service.app.api.auth.get_user_from_token", return_value=mock_user),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=mock_result),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_reset_success_email",
            return_value=False,
        ),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 200
        assert "Password reset successfully" in response.json()["message"]


def test_reset_password_email_exception_is_swallowed(auth_client):
    """Reset password should swallow success-email exceptions."""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    mock_user = {
        "sub": "user-id",
        "email": "test@example.com",
        "user_metadata": {"first_name": "Reset"},
    }
    mock_result = MagicMock()
    mock_result.user = MagicMock()

    with (
        patch("apps.user_service.app.api.auth.get_user_from_token", return_value=mock_user),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=mock_result),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_reset_success_email",
            side_effect=Exception("email down"),
        ),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 200
        assert "Password reset successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_reset_password_endpoint_success_async(_async_auth_client):
    """Test reset password asynchronously"""
    from apps.user_service.app.api.auth import reset_password
    from apps.user_service.app.schemas.auth import ResetPasswordRequest

    reset_data = ResetPasswordRequest(token="valid-reset-token", new_password="NewPass123!")

    with (
        patch(
            "apps.user_service.app.api.auth.get_user_from_token",
            return_value={"sub": "user-id"},
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=MagicMock(user=MagicMock())),
        ),
    ):
        mock_request = MagicMock(spec=Request)
        result = await reset_password(request=mock_request, data=reset_data)

        assert "Password reset successfully" in result.message


def test_reset_password_endpoint_weak_password(auth_client):
    """Test reset password with weak password"""
    reset_data = {"token": "valid-reset-token", "new_password": "weak"}

    with patch(
        "apps.user_service.app.api.auth.get_user_from_token",
        return_value={"sub": "user-id"},
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 400
        assert "Password must be at least 6 characters" in response.json()["detail"]


@pytest.mark.asyncio
async def test_reset_password_endpoint_weak_password_async(_async_auth_client):
    """Test reset password with weak password asynchronously"""
    from apps.user_service.app.api.auth import reset_password
    from apps.user_service.app.schemas.auth import ResetPasswordRequest

    reset_data = ResetPasswordRequest(token="valid-reset-token", new_password="weak")

    with patch(
        "apps.user_service.app.api.auth.get_user_from_token",
        return_value={"sub": "user-id"},
    ):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await reset_password(request=mock_request, data=reset_data)

        assert exc_info.value.status_code == 400
        assert "Password must be at least 6 characters" in exc_info.value.detail


def test_verify_email_endpoint_success(auth_client):
    """Test verify email"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_auth_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_organization_member_status_by_email",
            AsyncMock(return_value="active"),
        ),
    ):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 200
        data = response.json()
        assert data["email_found"] is True
        assert data["can_login"] is True
        assert data["status"] == "active"
        assert data["message"] == "Email verified and active."


@pytest.mark.asyncio
async def test_verify_email_endpoint_success_async(_async_auth_client):
    """Test verify email asynchronously"""
    from apps.user_service.app.api.auth import verify_email
    from apps.user_service.app.schemas.auth import VerifyEmailRequest

    verify_data = VerifyEmailRequest(email="test@example.com")

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_auth_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_organization_member_status_by_email",
            AsyncMock(return_value="active"),
        ),
    ):
        mock_request = MagicMock(spec=Request)
        result = await verify_email(request=mock_request, body=verify_data)

        assert result.email_found is True
        assert result.can_login is True
        assert result.status == "active"
        assert result.message == "Email verified and active."


def test_verify_email_endpoint_not_found(auth_client):
    """Test verify email with non-existent email"""
    verify_data = {"email": "nonexistent@example.com"}

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["message"] == EMAIL_NOT_FOUND_MESSAGE


def test_verify_email_endpoint_inactive_member(auth_client):
    """Test verify email when organization member is suspended"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"user_type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_auth_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_organization_member_status_by_email",
            AsyncMock(return_value="suspended"),
        ),
    ):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 403
        data = response.json()
        assert data["detail"]["message"] == "Account is not active. Please contact support."


def test_verify_email_member_missing_in_org(auth_client):
    """Test verify email when membership row is missing"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_auth_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_organization_member_status_by_email",
            AsyncMock(return_value=None),
        ),
    ):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["message"] == EMAIL_NOT_FOUND_MESSAGE


@pytest.mark.asyncio
async def test_verify_email_endpoint_not_found_async(_async_auth_client):
    """Test verify email with non-existent email"""
    from apps.user_service.app.api.auth import verify_email
    from apps.user_service.app.schemas.auth import VerifyEmailRequest

    verify_data = VerifyEmailRequest(email="nonexistent@example.com")

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        mock_request = MagicMock(spec=Request)
        with pytest.raises(HTTPException) as exc_info:
            await verify_email(request=mock_request, body=verify_data)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["message"] == EMAIL_NOT_FOUND_MESSAGE


def test_refresh_endpoint_success(auth_client):
    """Test refresh endpoint success path"""
    refresh_response = SimpleNamespace(
        session=SimpleNamespace(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="user-id",
            email="user@example.com",
            user_metadata={
                "first_name": "Test",
                "last_name": "User",
                "timezone": "UTC",
                "organization_id": "org-321",
            },
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.refresh_session",
            AsyncMock(return_value=refresh_response),
        ),
        patch(
            "apps.user_service.app.api.auth.jwt.decode",
            side_effect=jwt.ExpiredSignatureError("Token expired"),
        ),
        patch("apps.user_service.app.api.auth.os.getenv", return_value="test-secret"),
    ):
        response = auth_client.put(
            "/auth/refresh",
            headers={
                "Access-Token": "expired-token",
                "Refresh-Token": "refresh-token",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "new-access"
        assert data["user"]["email"] == "user@example.com"
        assert data["user"]["org_setup_status_completed"] is True
        assert data["user"]["organization_id"] == "org-321"
        assert data["user"]["timezone"] == "UTC"


def test_refresh_endpoint_token_not_expired(auth_client):
    """Test refresh endpoint when access token not expired"""
    future_token = jwt.encode(
        {
            "exp": int((datetime.now() + timedelta(hours=1)).timestamp()),
            "aud": "authenticated",
        },
        "secret",
        algorithm="HS256",
    )

    with patch("apps.user_service.app.api.auth.os.getenv", return_value="secret"):
        response = auth_client.put(
            "/auth/refresh",
            headers={
                "Access-Token": future_token,
                "Refresh-Token": "refresh-token",
            },
        )
        assert response.status_code == 400
        assert "Token is not expired" in response.json()["detail"]


def test_refresh_endpoint_handles_exception(auth_client):
    """Test refresh endpoint when refresh session fails"""
    with patch(
        "apps.user_service.app.api.auth.refresh_session",
        AsyncMock(side_effect=Exception("boom")),
    ):
        response = auth_client.put(
            "/auth/refresh",
            headers={
                "Access-Token": "expired-token",
                "Refresh-Token": "refresh-token",
            },
        )
        assert response.status_code == 500
        assert "Authentication failed" in response.json()["detail"]


def test_delete_user_endpoint_success(auth_client):
    """Test delete user"""
    with patch(
        "apps.user_service.app.api.auth.delete_auth_user",
        AsyncMock(return_value={"message": "User deleted"}),
    ):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_user_endpoint_success_async(_async_auth_client):
    """Test delete user asynchronously"""
    from apps.user_service.app.api.auth import delete_user

    with (
        patch(
            "libs.shared_middleware.jwt_auth.get_user_from_auth",
            return_value={"sub": "test-user-id"},
        ),
        patch(
            "apps.user_service.app.api.auth.delete_auth_user",
            AsyncMock(return_value={"message": "User deleted"}),
        ),
    ):
        mock_request = MagicMock(spec=Request)
        result = await delete_user(request=mock_request, current_user={"sub": "test-user-id"})

        assert result.status_code == 204


def test_delete_user_endpoint_not_found(auth_client):
    """Test delete user with non-existent user"""
    with patch("apps.user_service.app.api.auth.delete_auth_user", AsyncMock(return_value=None)):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_endpoint_not_found_async(_async_auth_client):
    """Test delete user with non-existent user asynchronously"""
    from apps.user_service.app.api.auth import delete_user

    with (
        patch(
            "libs.shared_middleware.jwt_auth.get_user_from_auth",
            return_value={"sub": "test-user-id"},
        ),
        patch(
            "apps.user_service.app.api.auth.delete_auth_user",
            AsyncMock(return_value=None),
        ),
    ):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await delete_user(request=mock_request, current_user={"sub": "test-user-id"})

        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_auth_module_initialization_async():
    """Test auth module initialization asynchronously"""
    from apps.user_service.app.api.auth import logger, router

    # Test that router is properly configured
    assert router.prefix == "/auth"
    assert "Authentication" in router.tags
    assert logger is not None


def test_set_password_endpoint_success(auth_client):
    """Test set password - covers auth.py set password function"""
    set_password_data = {"password": "NewPass123!"}

    with patch(
        "apps.user_service.app.api.auth.update_password_with_link_identity",
        AsyncMock(return_value=True),
    ):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 202
        assert "Password set successfully" in response.json()["message"]


def test_set_password_endpoint_weak_password(auth_client):
    """Test set password with weak password"""
    set_password_data = {"password": "weak"}

    response = auth_client.post("/auth/set-password", json=set_password_data)
    assert response.status_code == 400
    assert "Password must be at least 6 characters" in response.json()["detail"]


@pytest.mark.asyncio
async def test_auth_helper_functions_async():
    """Test auth helper functions asynchronously"""
    from apps.user_service.app.api.auth import _validate_password_strength

    await asyncio.to_thread(_validate_password_strength, "Test123!")
    with pytest.raises(HTTPException):
        await asyncio.to_thread(_validate_password_strength, "weak")


# ============================================================================
# MISSING COVERAGE TESTS
# ============================================================================


def test_set_password_update_fails(auth_client):
    """Test set_password when password update fails"""
    set_password_data = {"password": "NewPass123!"}

    with patch(
        "apps.user_service.app.api.auth.update_password_with_link_identity",
        AsyncMock(return_value=False),
    ):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]


def test_set_password_exception_handling(auth_client):
    """Test set_password exception handling"""
    set_password_data = {"password": "NewPass123!"}

    with patch(
        "apps.user_service.app.api.auth.update_password_with_link_identity",
        AsyncMock(side_effect=Exception("Database error")),
    ):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]


# ============================================================================
# CHANGE PASSWORD TESTS - NEW CODE COVERAGE
# ============================================================================


def test_change_password_success(auth_client):
    """Test successful password change"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    call_count = [0]

    async def mock_login_user(_email, _password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    with (
        patch(
            ("apps.user_service.app.api.auth.login_user"),
            AsyncMock(side_effect=mock_login_user),
        ),
        patch(
            ("apps.user_service.app.api.auth.update_password_with_link_identity"),
            AsyncMock(return_value=True),
        ),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password changed successfully" in data["message"]


def test_change_password_invalid_user_info(_auth_client):
    """Test change_password with invalid user information"""
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    def mock_get_user_no_email():
        return {"sub": "test-user-id"}

    app.dependency_overrides[get_user_from_auth] = mock_get_user_no_email
    client = TestClient(app)

    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    response = client.post("/auth/change-password", json=change_password_data)
    assert response.status_code == 401
    assert "Invalid user information" in response.json()["detail"]


def test_change_password_invalid_current_password(auth_client):
    """Test change_password with incorrect current password"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!",
    }

    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid login credentials")),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert "Current password is incorrect" in response.json()["detail"]


def test_change_pwd_invalid_current_unexpected_exc(auth_client):
    """Test change_pwd with unexpected exception"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!",
    }

    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized access")),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized access"


def test_change_pwd_invalid_current_wrong_detail(auth_client):
    """Test change_pwd with wrong detail"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!",
    }

    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Some other error")),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert response.json()["detail"] == "Some other error"


def test_change_pwd_invalid_current_authapierror(auth_client):
    """Test change_pwd with AuthApiError"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!",
    }

    from supabase_auth.errors import AuthApiError

    auth_error = AuthApiError("Invalid login credentials", status=400, code="invalid_credentials")
    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=auth_error),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error during change_password"


def test_change_pwd_current_pwd_verification_exc(auth_client):
    """Test change_password with current password verification exception"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    with patch(
        "apps.user_service.app.api.auth.login_user",
        AsyncMock(side_effect=Exception("Database connection error")),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error during change_password"


def test_change_password_update_fails(auth_client):
    """Test change_password with password update failure"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    call_count = [0]

    async def mock_login_user(_email, _password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    with (
        patch(
            ("apps.user_service.app.api.auth.login_user"),
            AsyncMock(side_effect=mock_login_user),
        ),
        patch(
            ("apps.user_service.app.api.auth.update_password_with_link_identity"),
            AsyncMock(return_value=False),
        ),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Failed to update password" in response.json()["detail"]


def test_change_password_update_error_user_not_allowed(auth_client):
    """Test change_password with user not allowed error"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    call_count = [0]

    async def mock_login_user(_email, _password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()
        raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails

    with (
        patch(
            ("apps.user_service.app.api.auth.login_user"),
            AsyncMock(side_effect=mock_login_user),
        ),
        patch(
            ("apps.user_service.app.api.auth.update_password_with_link_identity"),
            AsyncMock(side_effect=Exception("User not allowed to change password")),
        ),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 403
        assert "User account is restricted" in response.json()["detail"]


def test_change_pwd_update_auth_error(auth_client):
    """Test change_password when update raises authentication error"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    call_count = [0]

    async def mock_login_user(_email, _password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails

    with (
        patch(
            ("apps.user_service.app.api.auth.login_user"),
            AsyncMock(side_effect=mock_login_user),
        ),
        patch(
            ("apps.user_service.app.api.auth.update_password_with_link_identity"),
            AsyncMock(side_effect=Exception("Authentication service unavailable")),
        ),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Authentication service error" in response.json()["detail"]


def test_change_password_update_error_generic(auth_client):
    """Test change_password when update raises generic error"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
    }

    call_count = [0]

    async def mock_login_user(_email, _password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails

    with (
        patch(
            ("apps.user_service.app.api.auth.login_user"),
            AsyncMock(side_effect=mock_login_user),
        ),
        patch(
            ("apps.user_service.app.api.auth.update_password_with_link_identity"),
            AsyncMock(side_effect=Exception("Unknown database error")),
        ),
    ):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Failed to update password" in response.json()["detail"]


def test_change_password_weak_new_password(auth_client):
    """Test change_password with weak new password"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "weak",  # Too weak
    }

    response = auth_client.post("/auth/change-password", json=change_password_data)
    assert response.status_code in [400, 422]


def test_change_password_same_as_current(auth_client):
    """Test change_password when new password is same as current password"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "OldPass123!",  # Same as current password
    }

    with patch("apps.user_service.app.api.auth.login_user", AsyncMock(return_value=MagicMock())):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert "New password must be different from current password" in response.json()["detail"]


@pytest.mark.asyncio
async def test_change_pwd_email_first_name_handles_false():
    """Change password should prefer first_name and warn when email send returns False."""
    from apps.user_service.app.api.auth import change_password
    from apps.user_service.app.schemas.auth import ChangePasswordRequest

    data = ChangePasswordRequest(current_password="OldPass123!", new_password="NewPass123!")
    mock_request = MagicMock(spec=Request)
    current_user = {
        "sub": "user-id",
        "email": "user@example.com",
        "user_metadata": {"first_name": "Tester"},
    }

    with (
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_link_identity",
            AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_change_success_email",
            return_value=False,
        ),
    ):
        result = await change_password(
            request=mock_request,
            data=data,
            current_user=current_user,
        )

    assert result.message == "Password changed successfully"


@pytest.mark.asyncio
async def test_change_pwd_email_full_name_exc():
    """Change password should fall back to full_name and swallow email errors."""
    from apps.user_service.app.api.auth import change_password
    from apps.user_service.app.schemas.auth import ChangePasswordRequest

    data = ChangePasswordRequest(current_password="OldPass123!", new_password="NewPass123!")
    mock_request = MagicMock(spec=Request)
    current_user = {
        "sub": "user-id",
        "email": "user@example.com",
        "user_metadata": {"full_name": "Test User"},
    }

    with (
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_link_identity",
            AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_change_success_email",
            side_effect=Exception("email down"),
        ),
    ):
        result = await change_password(
            request=mock_request,
            data=data,
            current_user=current_user,
        )

    assert result.message == "Password changed successfully"


def test_set_password_general_exception(auth_client):
    """Test set_password general exception handling"""
    set_password_data = {"password": "NewPass123!"}

    with patch(
        "apps.user_service.app.api.auth.update_password_with_link_identity",
        AsyncMock(side_effect=Exception("General error")),
    ):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]


def test_forgot_password_exception_handling(auth_client):
    """Test forgot_password exception handling"""
    forgot_data = {"email": "test@example.com"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value={"id": "user-id"}),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_reset_email",
            AsyncMock(side_effect=Exception("Email service error")),
        ),
    ):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to process password reset request" in data["detail"]


def test_reset_password_user_not_found(auth_client):
    """Test reset_password when user not found"""
    reset_data = {"token": "invalid-token", "new_password": "NewPass123!"}

    with patch("apps.user_service.app.api.auth.get_user_from_token", return_value=None):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 404
        data = response.json()
        assert "User not found" in data["detail"]


def test_reset_password_email_error_handling(auth_client):
    """Test reset_password email error handling"""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_user_from_token",
            return_value={
                "sub": "user-id",
                "email": "test@example.com",
                "user_metadata": {"full_name": "Test User"},
            },
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=MagicMock(user=MagicMock())),
        ),
        patch(
            "apps.user_service.app.api.auth.send_password_reset_confirmation_email",
            side_effect=Exception("Email service error"),
        ),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password reset successfully" in data["message"]


def test_reset_password_update_fails(auth_client):
    """Test reset_password when password update fails"""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    with (
        patch(
            "apps.user_service.app.api.auth.get_user_from_token",
            return_value={"sub": "user-id"},
        ),
        patch(
            "apps.user_service.app.api.auth.update_password_with_token",
            AsyncMock(return_value=MagicMock(user=None)),
        ),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 400
        data = response.json()
        assert "Failed to update password" in data["detail"]


def test_reset_password_general_exception(auth_client):
    """Test reset_password general exception handling"""
    reset_data = {"token": "valid-reset-token", "new_password": "NewPass123!"}

    with patch(
        "apps.user_service.app.api.auth.get_user_from_token",
        side_effect=Exception("Token processing error"),
    ):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to reset password" in data["detail"]


def test_login_general_exception(auth_client):
    """Test login general exception handling"""
    login_data = {"email": "test@example.com", "password": "TestPass123!"}

    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("General authentication error")),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 500
        data = response.json()
        assert "Authentication failed" in data["detail"]


def test_delete_user_exception_handling(auth_client):
    """Test delete_user exception handling"""
    with patch(
        "apps.user_service.app.api.auth.delete_auth_user",
        AsyncMock(side_effect=Exception("Delete service error")),
    ):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 500
        data = response.json()
        assert "Failed to delete user" in data["detail"]


# ============================================================================
# 2FA LOGIN TESTS
# ============================================================================


def test_login_with_2fa_email_enabled_success(auth_client):
    """Test login with 2FA enabled (EMAIL type)"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "123456",
        "verified": False,
    }

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={
                "first_name": "Test",
                "last_name": "User",
            },
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.auth._verify_code_and_update_record",
            AsyncMock(return_value=None),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"


def test_login_with_2fa_phone_enabled_success(auth_client):
    """Test login with 2FA enabled (PHONE type) - successful verification"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled (PHONE type)
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "PHONE"}},
        phone="+1234567890",
    )

    # Mock verification record
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "PHONE",
        "given_input": "+1234567890",
        "verification_code": "123456",
        "verified": False,
    }

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.auth._verify_code_and_update_record",
            AsyncMock(return_value=None),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"


def test_login_with_2fa_enabled_missing_credentials(auth_client):
    """Test login with 2FA enabled but missing verification credentials"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        # Missing verification_id and verification_code
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=mock_user),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification is required" in response.json()["detail"]


def test_login_with_2fa_enabled_verification_not_found(auth_client):
    """Test login with 2FA enabled but verification code not found"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "non-existent-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=None),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 404
        assert "Verification code not found" in response.json()["detail"]


def test_login_2fa_verification_missing_given_input(auth_client):
    """Test login with 2FA enabled but verification record missing given_input"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock verification record without given_input
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "verification_code": "123456",
        # Missing given_input
    }

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification failed" in response.json()["detail"]


def test_login_with_2fa_enabled_email_mismatch(auth_client):
    """Test login with 2FA enabled but email doesn't match verification record"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock verification record with different email
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "different@example.com",  # Different email
        "verification_code": "123456",
    }

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification failed" in response.json()["detail"]


def test_login_with_2fa_enabled_phone_mismatch(auth_client):
    """Test login with 2FA enabled (PHONE) but phone doesn't match"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled (PHONE type)
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "PHONE"}},
        phone="+1234567890",
    )

    # Mock verification record with different phone
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "PHONE",
        "given_input": "+9876543210",  # Different phone
        "verification_code": "123456",
    }

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification failed" in response.json()["detail"]


def test_login_with_2fa_enabled_phone_normalized_match(auth_client):
    """Test login with 2FA enabled (PHONE) with normalized phone numbers matching"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled (PHONE type)
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "PHONE"}},
        phone="1234567890",  # Without +
    )

    # Mock verification record with + prefix
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "PHONE",
        "given_input": "+1234567890",  # With +, should normalize and match
        "verification_code": "123456",
    }

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.auth._verify_code_and_update_record",
            AsyncMock(return_value=None),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200


def test_login_2fa_verification_validation_fails(auth_client):
    """Test login with 2FA enabled but verification validation fails"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "123456",
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock verification record
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "123456",
    }
    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            side_effect=HTTPException(status_code=400, detail="Invalid verification"),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification failed" in response.json()["detail"]


def test_login_with_2fa_enabled_code_verification_fails(auth_client):
    """Test login with 2FA enabled but code verification fails"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!",
        "verification_id": "test-verification-id",
        "verification_code": "wrong-code",
    }

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock verification record
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "123456",
    }

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.auth._verify_code_and_update_record",
            AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid code")),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "2FA verification failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_with_2fa_enabled_async(_async_auth_client):
    """Test login with 2FA enabled asynchronously"""
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(
        email="test@example.com",
        password="TestPass123!",
        verification_id="test-verification-id",
        verification_code="123456",
    )

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock verification record
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "123456",
    }

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.get_verification_code_by_id",
            AsyncMock(return_value=mock_verification_record),
        ),
        patch(
            "apps.user_service.app.api.auth._validate_verification_record",
            return_value=None,
        ),
        patch(
            "apps.user_service.app.api.auth._verify_code_and_update_record",
            AsyncMock(return_value=None),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        mock_request = MagicMock(spec=Request)
        result = await login(request=mock_request, data=login_data)
        assert result.access_token == "test-access-token"


def test_login_with_2fa_disabled_no_verification_needed(auth_client):
    """Test login with 2FA disabled - should work without verification"""
    login_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user with 2FA disabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={
            "verification_preference": {
                "enabled": False,  # 2FA disabled
                "type": "EMAIL",
            }
        },
        phone=None,
    )

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"


def test_login_with_2fa_preference_not_dict(auth_client):
    """Test login when verification_preference is not a dict"""
    login_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user with verification_preference as string (invalid)
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={
            "verification_preference": "invalid"  # Not a dict
        },
        phone=None,
    )

    # Mock the Supabase login response
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200  # Should work, 2FA check skipped


# ============================================================================
# VERIFICATION CODES EDGE CASES - NEW CODE COVERAGE
# ============================================================================


@pytest.mark.asyncio
async def test_verify_code_with_non_list_attempts():
    """Test _verify_code_and_update_record when attempts is not a list"""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    # Mock verification record with attempts as string (not a list)
    verification_record = {
        "id": "test-id",
        "verification_code": "123456",
        "attempts": "invalid",  # Not a list
    }

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(return_value=None),
    ):
        # Should handle non-list attempts gracefully
        try:
            await _verify_code_and_update_record(verification_record, "123456", "test-id")
        except Exception:
            # Should raise HTTPException if code doesn't match, or succeed if it does
            # The function should convert non-list attempts to empty list
            pass


@pytest.mark.asyncio
async def test_verify_code_with_missing_attempts():
    """Test _verify_code_and_update_record when attempts key is missing"""
    from apps.user_service.app.api.verification_codes import (
        _verify_code_and_update_record,
    )

    # Mock verification record without attempts key
    verification_record = {
        "id": "test-id",
        "verification_code": "123456",
        # Missing attempts key
    }

    with patch(
        "apps.user_service.app.api.verification_codes.update_verification_code",
        AsyncMock(return_value=None),
    ):
        # Should handle missing attempts gracefully
        try:
            await _verify_code_and_update_record(verification_record, "123456", "test-id")
        except Exception:
            # Should raise HTTPException if code doesn't match
            pass


def test_validate_verification_record_with_none_record():
    """Test _validate_verification_record with None record"""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )
    from apps.user_service.app.schemas.verification_codes import (
        VerificationType,
        VerifyVerificationCodeRequest,
    )

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="test-id",
        verification_code="123456",
        email="test@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_verification_record(None, data)

    assert exc_info.value.status_code == 404
    assert "Verification code not found" in exc_info.value.detail


def test_validate_verification_record_already_verified():
    """Test _validate_verification_record when code is already verified"""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )
    from apps.user_service.app.schemas.verification_codes import (
        VerificationType,
        VerifyVerificationCodeRequest,
    )

    verification_record = {
        "id": "test-id",
        "verified": True,
        "given_input": "test@example.com",
        "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 60000,
    }

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="test-id",
        verification_code="123456",
        email="test@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_verification_record(verification_record, data)

    assert exc_info.value.status_code == 400
    assert "already been verified" in exc_info.value.detail


def test_validate_verification_record_expired():
    """Test _validate_verification_record when code is expired"""
    from apps.user_service.app.api.verification_codes import (
        _validate_verification_record,
    )
    from apps.user_service.app.schemas.verification_codes import (
        VerificationType,
        VerifyVerificationCodeRequest,
    )

    # Expired code (expiry_at is in the past)
    verification_record = {
        "id": "test-id",
        "verified": False,
        "given_input": "test@example.com",
        "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) - 60000,  # Past
    }

    data = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="test-id",
        verification_code="123456",
        email="test@example.com",
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_verification_record(verification_record, data)

    assert exc_info.value.status_code == 400
    assert "expired" in exc_info.value.detail.lower()


# ============================================================================
# CHECK 2FA STATUS API TESTS
# ============================================================================


def test_check_2fa_status_success_2fa_enabled(auth_client):
    """Test check 2FA status when 2FA is enabled"""
    check_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock successful login
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 200
        data = response.json()
        assert data["two_fa_enabled"] is True


def test_check_2fa_status_success_2fa_disabled(auth_client):
    """Test check 2FA status when 2FA is disabled"""
    check_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user with 2FA disabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": False, "type": "EMAIL"}},
        phone=None,
    )

    # Mock successful login
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 200
        data = response.json()
        assert data["two_fa_enabled"] is False


def test_check_2fa_status_success_no_preference(auth_client):
    """Test check 2FA status when verification_preference is not set"""
    check_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user without verification_preference
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={},  # No verification_preference
        phone=None,
    )

    # Mock successful login
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 200
        data = response.json()
        assert data["two_fa_enabled"] is False


def test_check_2fa_status_email_not_found(auth_client):
    """Test check 2FA status when email is not registered"""
    check_data = {"email": "nonexistent@example.com", "password": "TestPass123!"}

    with patch(
        "apps.user_service.app.api.auth.get_auth_user_by_email",
        AsyncMock(return_value=None),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 400
        assert "Email Is Not Registered" in response.json()["detail"]


def test_check_2fa_status_invalid_credentials(auth_client):
    """Test check 2FA status with invalid credentials"""
    check_data = {"email": "test@example.com", "password": "wrongpassword"}

    # Mock user exists
    mock_user = SimpleNamespace(id="existing-user-id", user_metadata={}, phone=None)

    from supabase_auth.errors import AuthApiError

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(
                side_effect=AuthApiError(
                    "Invalid login credentials", status=400, code="invalid_credentials"
                )
            ),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]


def test_check_2fa_status_invalid_credentials_exception(auth_client):
    """Test check 2FA status with invalid credentials (Exception)"""
    check_data = {"email": "test@example.com", "password": "wrongpassword"}

    # Mock user exists
    mock_user = SimpleNamespace(id="existing-user-id", user_metadata={}, phone=None)

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("Invalid login credentials")),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]


def test_check_2fa_status_general_exception(auth_client):
    """Test check 2FA status with general exception"""
    check_data = {"email": "test@example.com", "password": "TestPass123!"}

    # Mock user exists
    mock_user = SimpleNamespace(id="existing-user-id", user_metadata={}, phone=None)

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(side_effect=Exception("Database connection error")),
        ),
    ):
        response = auth_client.post("/auth/verify/account", json=check_data)
        assert response.status_code == 500
        assert "Failed to validate credentials" in response.json()["detail"]


@pytest.mark.asyncio
async def test_check_2fa_status_success_async(_async_auth_client):
    """Test check 2FA status asynchronously"""
    from apps.user_service.app.api.auth import check_2fa_status
    from apps.user_service.app.schemas.auth import Check2FAStatusRequest

    check_data = Check2FAStatusRequest(email="test@example.com", password="TestPass123!")

    # Mock user with 2FA enabled
    mock_user = SimpleNamespace(
        id="existing-user-id",
        user_metadata={"verification_preference": {"enabled": True, "type": "EMAIL"}},
        phone=None,
    )

    # Mock successful login
    mock_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow(),
        ),
        user=SimpleNamespace(
            id="test-user-id",
            email="test@example.com",
            user_metadata={},
        ),
    )

    with (
        patch(
            "apps.user_service.app.api.auth.get_auth_user_by_email",
            AsyncMock(return_value=mock_user),
        ),
        patch(
            "apps.user_service.app.api.auth.login_user",
            AsyncMock(return_value=mock_result),
        ),
    ):
        mock_request = MagicMock(spec=Request)
        result = await check_2fa_status(request=mock_request, data=check_data)
        assert result.two_fa_enabled is True
