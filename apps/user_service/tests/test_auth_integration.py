# pylint: disable=all

"""
Async integration tests for authentication endpoints.
Tests auth.py endpoints with proper AsyncMock usage.
"""

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace
from datetime import datetime, timedelta
import asyncio
from libs.shared_middleware.jwt_auth import get_user_from_auth

@pytest.fixture
def auth_client():
    """Test client for auth endpoints"""
    from fastapi import FastAPI
    from apps.user_service.app.api.auth import router as auth_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth

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
    from fastapi import FastAPI
    from apps.user_service.app.api.auth import router as auth_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth

    app = FastAPI()
    app.include_router(auth_router)

    # Override the auth dependency for testing
    def mock_get_user_from_auth():
        return {"sub": "test-user-id", "email": "test@example.com"}

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth

    return app

def test_login_endpoint_success(auth_client):
    """Test successful login - covers auth.py login function"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!"
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
            user_metadata={"first_name": "Test", "last_name": "User", "timezone": "UTC"},
        ),
    )

    with patch('apps.user_service.app.api.auth.login_user', AsyncMock(return_value=mock_result)):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test-access-token"
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["first_name"] == "Test"
        assert data["user"]["last_name"] == "User"

@pytest.mark.asyncio
async def test_login_endpoint_success_async(async_auth_client):
    """Test successful login asynchronously - covers auth.py login function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(
        email="test@example.com",
        password="TestPass123!"
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
            user_metadata={"first_name": "Test", "last_name": "User", "timezone": "UTC"},
        ),
    )

    with patch('apps.user_service.app.api.auth.login_user', AsyncMock(return_value=mock_result)):
        # Create a mock request
        mock_request = MagicMock(spec=Request)
        result = await login(request=mock_request, data=login_data)

        assert result.access_token == "test-access-token"
        assert result.user.email == "test@example.com"
        assert result.user.first_name == "Test"
        assert result.user.last_name == "User"

def test_login_endpoint_invalid_credentials(auth_client):
    """Test login with invalid credentials - covers auth.py error handling"""
    login_data = {
        "email": "test@example.com",
        "password": "wrongpassword"
    }

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=Exception("Invalid login credentials"))):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]

def test_login_endpoint_invalid_credentials_authapierror(auth_client):
    """Test login with AuthApiError for invalid credentials - covers AuthApiError handling"""
    login_data = {
        "email": "test@example.com",
        "password": "wrongpassword"
    }

    from supabase_auth.errors import AuthApiError

    # Mock AuthApiError for invalid credentials
    auth_error = AuthApiError("Invalid login credentials", status=400, code="invalid_credentials")
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=auth_error)):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 400
        assert "Invalid login credentials" in response.json()["detail"]

@pytest.mark.asyncio
async def test_login_endpoint_invalid_credentials_async(async_auth_client):
    """Test login with invalid credentials asynchronously - covers auth.py error handling"""
    from fastapi import Request, HTTPException
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(
        email="test@example.com",
        password="wrongpassword"
    )

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=Exception("Invalid login credentials"))):
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
        "verificationId": "test-verification-id",
        "verificationCode": "1111"
    }

    # Mock verification code record (verified and matching)
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True
    }

    # Mock the signup response with user and session (same as login)
    mock_result = MagicMock()
    mock_result.user.id = "new-user-id"
    mock_result.user.email = "newuser@example.com"
    mock_result.user.user_metadata = {"first_name": "New", "last_name": "User", "timezone": "UTC"}
    mock_session = SimpleNamespace(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_in=3600,
        expires_at=datetime.utcnow()
    )
    mock_result.session = mock_session

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=mock_verification_record)), \
         patch('apps.user_service.app.api.auth.sign_up_supabase_user',
               AsyncMock(return_value=mock_result)), \
         patch('apps.user_service.app.api.auth._get_session_after_signup',
               AsyncMock(return_value=mock_session)), \
         patch('apps.user_service.app.api.auth.send_welcome_email',
               return_value=True):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 201
        data = response.json()
        assert data["access_token"] == "test-access-token"
        assert data["refresh_token"] == "test-refresh-token"
        assert data["user"]["id"] == "new-user-id"
        assert data["user"]["email"] == "newuser@example.com"

@pytest.mark.asyncio
async def test_signup_endpoint_success_async(async_auth_client):
    """Test successful signup asynchronously - covers auth.py signup function"""
    from fastapi.testclient import TestClient

    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verificationId": "test-verification-id",
        "verificationCode": "1111"
    }

    # Mock verification code record (verified and matching)
    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": True
    }

    # Mock the signup response with user and session (same as login)
    mock_result = MagicMock()
    mock_result.user.id = "new-user-id"
    mock_result.user.email = "newuser@example.com"
    mock_result.user.user_metadata = {"first_name": "New", "last_name": "User", "timezone": "UTC"}
    mock_session = SimpleNamespace(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_in=3600,
        expires_at=datetime.utcnow()
    )
    mock_result.session = mock_session

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=mock_verification_record)), \
         patch('apps.user_service.app.api.auth.sign_up_supabase_user',
               AsyncMock(return_value=mock_result)), \
         patch('apps.user_service.app.api.auth._get_session_after_signup',
               AsyncMock(return_value=mock_session)), \
         patch('apps.user_service.app.api.auth.send_welcome_email',
               return_value=True):
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
        "last_name": "User"
    }

    response = auth_client.post("/auth/signup", json=signup_data)
    assert response.status_code == 422  # Pydantic validation happens first
    assert "at least 6 characters" in str(response.json())

@pytest.mark.asyncio
async def test_signup_endpoint_weak_password_async(async_auth_client):
    """Test signup with weak password asynchronously - covers auth.py password validation"""
    from fastapi.testclient import TestClient

    signup_data = {
        "email": "newuser@example.com",
        "password": "weak",
        "first_name": "New",
        "last_name": "User"
    }

    with TestClient(async_auth_client) as client:
        response = client.post("/auth/signup", json=signup_data)
        assert response.status_code == 422  # Pydantic validation happens first
        assert "at least 6 characters" in str(response.json())

def test_signup_endpoint_verification_code_not_found(auth_client):
    """Test signup when verification code not found - covers line 123"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verificationId": "non-existent-id",
        "verificationCode": "1111"
    }

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=None)):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 404
        assert "Verification code not found" in response.json()["detail"]

def test_signup_endpoint_verification_code_not_verified(auth_client):
    """Test signup when verification code not verified - covers line 129"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verificationId": "test-verification-id",
        "verificationCode": "1111"
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",
        "verified": False  # Not verified
    }

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=mock_verification_record)):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "must be verified before signup" in response.json()["detail"]

def test_signup_endpoint_email_mismatch(auth_client):
    """Test signup when email doesn't match verification record - covers line 136"""
    signup_data = {
        "email": "different@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verificationId": "test-verification-id",
        "verificationCode": "1111"
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",  # Different email
        "verification_code": "1111",
        "verified": True
    }

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=mock_verification_record)):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "does not match the verification record" in response.json()["detail"]

def test_signup_endpoint_invalid_verification_code(auth_client):
    """Test signup with invalid verification code - covers line 143"""
    signup_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "first_name": "New",
        "last_name": "User",
        "verificationId": "test-verification-id",
        "verificationCode": "9999"  # Wrong code
    }

    mock_verification_record = {
        "id": "test-verification-id",
        "type_text": "EMAIL",
        "given_input": "newuser@example.com",
        "verification_code": "1111",  # Different code
        "verified": True
    }

    with patch('apps.user_service.app.api.auth.get_verification_code_by_id',
               AsyncMock(return_value=mock_verification_record)):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "Invalid verification code" in response.json()["detail"]

def test_extract_session_none():
    """Test _extract_session when session has no access_token - covers line 161"""
    from apps.user_service.app.api.auth import _extract_session
    from types import SimpleNamespace

    # Test with session without access_token
    session_no_token = SimpleNamespace()
    result = _extract_session(session_no_token)
    assert result is None

    # Test with None session
    result = _extract_session(None)
    assert result is None

@pytest.mark.asyncio
async def test_get_session_after_signup_login_fallback():
    """Test _get_session_after_signup login fallback - covers lines 184-190"""
    from apps.user_service.app.api.auth import _get_session_after_signup
    from types import SimpleNamespace

    # Mock signup result without session token
    signup_result = SimpleNamespace(session=SimpleNamespace())  # No access_token

    # Mock login result with session
    login_result = SimpleNamespace(
        session=SimpleNamespace(
            access_token="login-token",
            refresh_token="login-refresh-token",
            expires_in=3600,
            expires_at=datetime.utcnow()
        )
    )

    with patch('apps.user_service.app.api.auth._extract_session',
               side_effect=[None, login_result.session]), \
         patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(return_value=login_result)):

        result = await _get_session_after_signup(
            signup_result=signup_result,
            email="test@example.com",
            password="password"
        )

        assert result is not None
        assert result.access_token == "login-token"

@pytest.mark.asyncio
async def test_get_session_after_signup_login_fails():
    """Test _get_session_after_signup when login fails - covers lines 184-190"""
    from apps.user_service.app.api.auth import _get_session_after_signup
    from types import SimpleNamespace

    # Mock signup result without session token
    signup_result = SimpleNamespace(session=SimpleNamespace())  # No access_token

    with patch('apps.user_service.app.api.auth._extract_session',
               return_value=None), \
         patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=Exception("Login failed"))):

        result = await _get_session_after_signup(
            signup_result=signup_result,
            email="test@example.com",
            password="password"
        )

        assert result is None

def test_send_welcome_email_safely_failure():
    """Test _send_welcome_email_safely when email fails - covers lines 204-206"""
    from apps.user_service.app.api.auth import _send_welcome_email_safely

    with patch('apps.user_service.app.api.auth.send_welcome_email',
               return_value=False):  # Email sending fails
        # Should not raise exception
        _send_welcome_email_safely("test@example.com", "Test")

    with patch('apps.user_service.app.api.auth.send_welcome_email',
               side_effect=Exception("Email error")):
        # Should not raise exception
        _send_welcome_email_safely("test@example.com", "Test")

def test_validate_password_strength_weak():
    """Test _validate_password_strength with weak password - covers line 220"""
    from apps.user_service.app.api.auth import _validate_password_strength
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _validate_password_strength("weak")
    
    assert exc_info.value.status_code == 400
    assert "Password must be at least 6 characters" in exc_info.value.detail

def test_extract_user_type_strict_app_meta_none():
    """Test _extract_user_type_strict when app_meta has no type - covers line 689"""
    from apps.user_service.app.api.auth import _extract_user_type_strict

    mock_row = MagicMock()
    mock_row.user_metadata = {}  # No type
    mock_row.app_metadata = {}  # No type either
    result = _extract_user_type_strict(mock_row)
    assert result is None

def test_verify_email_non_organization_member(auth_client):
    """Test verify_email when user_type is not organization_member - covers line 726"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "other_type"}  # Not organization_member
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)):
        response = auth_client.post("/auth/email/verify", json=verify_data)        
        assert response.status_code == 200
        data = response.json()
        assert data["email_found"] is True
        assert data["message"] == "Email found."
        assert data["status"] is None
        assert data["can_login"] is False

@pytest.mark.asyncio
async def test_login_endpoint_http_exception_re_raise():
    """Test login endpoint re-raising HTTPException - covers line 316"""
    from fastapi import Request, HTTPException
    from apps.user_service.app.api.auth import login
    from apps.user_service.app.schemas.auth import AuthLogin

    login_data = AuthLogin(
        email="test@example.com",
        password="TestPass123!"
    )

    # Mock login_user to raise HTTPException directly
    # Note: HTTPException with 401/403 will be converted to 400 for invalid credentials
    # But if it's not 401/403, it will be re-raised as-is
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=HTTPException(status_code=500, detail="Server error"))):
        mock_request = MagicMock(spec=Request)
        
        with pytest.raises(HTTPException) as exc_info:
            await login(request=mock_request, data=login_data)
        
        assert exc_info.value.status_code == 500
        assert "Server error" in exc_info.value.detail

def test_forgot_password_endpoint_success(auth_client):
    """Test forgot password - covers auth.py forgot password function"""
    forgot_data = {"email": "test@example.com"}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value={"id": "user-id"})), \
         patch('apps.user_service.app.api.auth.reset_the_password_email',
               AsyncMock()):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password reset email sent" in data["message"]

@pytest.mark.asyncio
async def test_forgot_password_endpoint_success_async(async_auth_client):
    """Test forgot password asynchronously - covers auth.py forgot password function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import forgot_password
    from apps.user_service.app.schemas.auth import ForgotPasswordRequest

    forgot_data = ForgotPasswordRequest(email="test@example.com")

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value={"id": "user-id"})), \
         patch('apps.user_service.app.api.auth.reset_the_password_email',
               AsyncMock()):
        mock_request = MagicMock(spec=Request)
        result = await forgot_password(request=mock_request, data=forgot_data)

        assert "Password reset email sent" in result.message

def test_forgot_password_endpoint_email_not_found(auth_client):
    """Test forgot password with non-existent email - covers auth.py error handling"""
    forgot_data = {"email": "nonexistent@example.com"}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 404
        assert "Email not found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_forgot_password_endpoint_email_not_found_async(async_auth_client):
    """Test forgot password with non-existent email asynchronously - covers auth.py error handling"""
    from fastapi import Request, HTTPException
    from apps.user_service.app.api.auth import forgot_password
    from apps.user_service.app.schemas.auth import ForgotPasswordRequest

    forgot_data = ForgotPasswordRequest(email="nonexistent@example.com")

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await forgot_password(request=mock_request, data=forgot_data)

        assert exc_info.value.status_code == 404
        assert "Email not found" in exc_info.value.detail

def test_reset_password_endpoint_success(auth_client):
    """Test reset password - covers auth.py reset password function"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id"}), \
         patch('apps.user_service.app.api.auth.update_password_with_token',
               AsyncMock(return_value=MagicMock(user=MagicMock()))):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password reset successfully" in data["message"]

@pytest.mark.asyncio
async def test_reset_password_endpoint_success_async(async_auth_client):
    """Test reset password asynchronously - covers auth.py reset password function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import reset_password
    from apps.user_service.app.schemas.auth import ResetPasswordRequest

    reset_data = ResetPasswordRequest(
        token="valid-reset-token",
        new_password="NewPass123!"
    )

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id"}), \
         patch('apps.user_service.app.api.auth.update_password_with_token',
               AsyncMock(return_value=MagicMock(user=MagicMock()))):
        mock_request = MagicMock(spec=Request)
        result = await reset_password(request=mock_request, data=reset_data)

        assert "Password reset successfully" in result.message

def test_reset_password_endpoint_weak_password(auth_client):
    """Test reset password with weak password - covers auth.py password validation"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "weak"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id"}):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 400
        assert "Password must be at least 6 characters" in response.json()["detail"]

@pytest.mark.asyncio
async def test_reset_password_endpoint_weak_password_async(async_auth_client):
    """Test reset password with weak password asynchronously - covers auth.py password validation"""
    from fastapi import Request, HTTPException
    from apps.user_service.app.api.auth import reset_password
    from apps.user_service.app.schemas.auth import ResetPasswordRequest

    reset_data = ResetPasswordRequest(
        token="valid-reset-token",
        new_password="weak"
    )

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id"}):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await reset_password(request=mock_request, data=reset_data)

        assert exc_info.value.status_code == 400
        assert "Password must be at least 6 characters" in exc_info.value.detail

def test_verify_email_endpoint_success(auth_client):
    """Test verify email - covers auth.py verify email function"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)), \
         patch('apps.user_service.app.api.auth.get_organization_member_status_by_email',
               AsyncMock(return_value="active")):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 200
        data = response.json()
        assert data["email_found"] == True
        assert data["can_login"] == True

@pytest.mark.asyncio
async def test_verify_email_endpoint_success_async(async_auth_client):
    """Test verify email asynchronously - covers auth.py verify email function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import verify_email
    from apps.user_service.app.schemas.auth import VerifyEmailRequest

    verify_data = VerifyEmailRequest(email="test@example.com")

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)), \
         patch('apps.user_service.app.api.auth.get_organization_member_status_by_email',
               AsyncMock(return_value="active")):
        mock_request = MagicMock(spec=Request)
        result = await verify_email(request=mock_request, body=verify_data)

        assert result.email_found == True
        assert result.can_login == True

def test_verify_email_endpoint_not_found(auth_client):
    """Test verify email with non-existent email - covers auth.py error handling"""
    verify_data = {"email": "nonexistent@example.com"}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        response = auth_client.post("/auth/email/verify", json=verify_data)        
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Email not found."
        assert data["email_found"] is False
        assert data["can_login"] is False
        assert data["status"] is None


def test_verify_email_endpoint_unknown_type(auth_client):
    """Test verify email when user metadata lacks type - covers fallback branch""" 
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {}
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)):
        response = auth_client.post("/auth/email/verify", json=verify_data)        
        assert response.status_code == 200
        data = response.json()
        assert data["email_found"] is True
        assert data["message"] == "Email found."
        assert data["status"] is None
        assert data["can_login"] is False


def test_verify_email_endpoint_inactive_member(auth_client):
    """Test verify email when organization member is suspended - covers suspended flow"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"user_type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)), \
         patch('apps.user_service.app.api.auth.get_organization_member_status_by_email',
               AsyncMock(return_value="suspended")):
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "suspended"
        assert data["can_login"] is False

def test_verify_email_endpoint_organization_member_not_in_table(auth_client):
    """Test verify email when organization_member exists in auth.users but not in organization_members table."""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = MagicMock()
    mock_auth_user.user_metadata = {"type": "organization_member"}
    mock_auth_user.app_metadata = {}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=mock_auth_user)), \
         patch('apps.user_service.app.api.auth.get_organization_member_status_by_email',
               AsyncMock(return_value=None)):  # Not found in organization_members table
        response = auth_client.post("/auth/email/verify", json=verify_data)
        assert response.status_code == 200
        data = response.json()
        assert data["email_found"] is True
        assert data["message"] == "Email found."
        assert data["status"] is None
        assert data["can_login"] is False


@pytest.mark.asyncio
async def test_verify_email_endpoint_not_found_async(async_auth_client):
    """Test verify email with non-existent email asynchronously - covers auth.py error handling"""
    from fastapi import Request
    from apps.user_service.app.api.auth import verify_email
    from apps.user_service.app.schemas.auth import VerifyEmailRequest

    verify_data = VerifyEmailRequest(email="nonexistent@example.com")

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value=None)):
        mock_request = MagicMock(spec=Request)
        response = await verify_email(request=mock_request, body=verify_data)

    assert response.email_found is False
    assert response.message == "Email not found."
    assert response.status is None
    assert response.can_login is False


def test_get_oauth_link_url_endpoint_success(auth_client):
    """Test generating OAuth link - covers success path"""
    with patch('apps.user_service.app.api.auth.get_oauth_link_url',
               AsyncMock(return_value={"url": "https://example.com/oauth"})):
        response = auth_client.get("/auth/link-user-oauth-url/google")
        assert response.status_code == 200
        assert response.json() == {"url": "https://example.com/oauth"}


def test_get_oauth_link_url_endpoint_provider_already_linked(auth_client):
    """Test OAuth link endpoint when provider already linked - covers error branch"""
    from fastapi import FastAPI
    from apps.user_service.app.api.auth import router as auth_router
    from libs.shared_middleware.jwt_auth import get_user_from_auth

    # Create a new app with different dependency override
    app = FastAPI()
    app.include_router(auth_router)

    def mock_get_user_with_provider():
        return {
            "sub": "test-user-id",
            "email": "test@example.com",
            "app_metadata": {"providers": ["google"]}
        }

    app.dependency_overrides[get_user_from_auth] = mock_get_user_with_provider

    with TestClient(app) as client:
        response = client.get("/auth/link-user-oauth-url/google")
        assert response.status_code == 400
        assert "already linked" in response.json()["detail"]


def test_get_oauth_link_url_endpoint_failure(auth_client):
    """Test OAuth link endpoint when underlying call fails - covers exception handling"""
    with patch('apps.user_service.app.api.auth.get_oauth_link_url',
               AsyncMock(side_effect=Exception("boom"))):
        response = auth_client.get("/auth/link-user-oauth-url/google")
        assert response.status_code == 500
        assert "Failed to generate" in response.json()["detail"]


def test_oauth_connect_success(auth_client):
    """Test oauth_connect success path"""
    with patch('apps.user_service.app.api.auth.supabase_user_oauth',
               AsyncMock(return_value={"url": "https://example.com/oauth"})):
        response = auth_client.get("/auth/oauth-connect/google")
        assert response.status_code == 200
        assert response.json() == {"url": "https://example.com/oauth"}


def test_oauth_connect_failure(auth_client):
    """Test oauth_connect raising exception"""
    with patch('apps.user_service.app.api.auth.supabase_user_oauth',
               AsyncMock(side_effect=Exception("failed"))):
        response = auth_client.get("/auth/oauth-connect/google")
        assert response.status_code == 500
        assert "Failed to generate" in response.json()["detail"]


def test_oauth_callback_success(auth_client):
    """Test oauth_callback success flow"""
    session_mock = SimpleNamespace(session=SimpleNamespace(access_token="token"))
    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(return_value=session_mock)):
        response = auth_client.get("/auth/oauth-callback?code=mycode")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["session"]["access_token"] == "token"


def test_oauth_callback_missing_code(auth_client):
    """Test oauth_callback when code missing"""
    response = auth_client.get("/auth/oauth-callback")
    assert response.status_code == 400
    assert "Missing authorization code" in response.json()["detail"]


def test_oauth_callback_invalid_session(auth_client):
    """Test oauth_callback when session exchange fails"""
    session_mock = SimpleNamespace(session=None)
    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(return_value=session_mock)):
        response = auth_client.get("/auth/oauth-callback?code=mycode")
        assert response.status_code == 400
        assert "Failed to exchange authorization code" in response.json()["detail"]


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
            user_metadata={"first_name": "Test", "last_name": "User", "timezone": "UTC"},
        ),
    )

    with patch('apps.user_service.app.api.auth.refresh_session',
               AsyncMock(return_value=refresh_response)), \
         patch('apps.user_service.app.api.auth.jwt.decode',
               side_effect=jwt.ExpiredSignatureError("Token expired")), \
         patch('apps.user_service.app.api.auth.os.getenv', return_value="test-secret"):
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


def test_refresh_endpoint_token_not_expired(auth_client):
    """Test refresh endpoint when access token not expired"""
    future_token = jwt.encode(
        {"exp": int((datetime.now() + timedelta(hours=1)).timestamp()), "aud": "authenticated"},
        "secret",
        algorithm="HS256",
    )

    with patch('apps.user_service.app.api.auth.os.getenv', return_value="secret"):
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
    with patch('apps.user_service.app.api.auth.refresh_session',
               AsyncMock(side_effect=Exception("boom"))):
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
    """Test delete user - covers auth.py delete user function"""
    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value={"message": "User deleted"})):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 204

@pytest.mark.asyncio
async def test_delete_user_endpoint_success_async(async_auth_client):
    """Test delete user asynchronously - covers auth.py delete user function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import delete_user

    with patch('libs.shared_middleware.jwt_auth.get_user_from_auth',
               return_value={"sub": "test-user-id"}), \
         patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value={"message": "User deleted"})):
        mock_request = MagicMock(spec=Request)
        result = await delete_user(request=mock_request, current_user={"sub": "test-user-id"})

        assert result.status_code == 204

def test_delete_user_endpoint_not_found(auth_client):
    """Test delete user with non-existent user - covers auth.py error handling"""
    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value=None)):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 404

@pytest.mark.asyncio
async def test_delete_user_endpoint_not_found_async(async_auth_client):
    """Test delete user with non-existent user asynchronously - covers auth.py error handling"""
    from fastapi import Request, HTTPException
    from apps.user_service.app.api.auth import delete_user

    with patch('libs.shared_middleware.jwt_auth.get_user_from_auth',
               return_value={"sub": "test-user-id"}), \
         patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value=None)):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await delete_user(request=mock_request, current_user={"sub": "test-user-id"})

        assert exc_info.value.status_code == 404

def test_password_strength_validation():
    """Test password strength validation - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _is_password_strong

    # Test strong passwords
    assert _is_password_strong("StrongPass123!") == True
    assert _is_password_strong("MyP@ssw0rd") == True
    assert _is_password_strong("Test123!") == True
    assert _is_password_strong("Password1!") == True

    # Test weak passwords
    assert _is_password_strong("weak") == False
    assert _is_password_strong("123456") == False
    assert _is_password_strong("password") == False
    assert _is_password_strong("PASSWORD") == False
    assert _is_password_strong("Pass1") == False  # Too short
    assert _is_password_strong("Password") == False  # No numbers
    assert _is_password_strong("password123") == False  # No uppercase
    assert _is_password_strong("PASSWORD123") == False  # No lowercase

# Note: _parse_meta function is commented out in auth.py, so this test is removed

def test_extract_user_type_strict():
    """Test user type extraction - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _extract_user_type_strict

    # Test with user_metadata
    mock_row1 = MagicMock()
    mock_row1.user_metadata = {"type": "organization_member"}
    mock_row1.app_metadata = {}
    assert _extract_user_type_strict(mock_row1) == "organization_member"

    # Test with app_metadata
    mock_row2 = MagicMock()
    mock_row2.user_metadata = {}
    mock_row2.app_metadata = {"user_type": "admin"}
    assert _extract_user_type_strict(mock_row2) == "admin"

    # Test with None input
    assert _extract_user_type_strict(None) is None

def test_get_not_found_response_helper():
    """Test not found response helper - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _get_not_found_response

    response = _get_not_found_response()

    assert response.email_found is False
    assert response.message == "Email not found."
    assert response.status is None
    assert response.can_login is False

@pytest.mark.asyncio
async def test_auth_module_initialization_async():
    """Test auth module initialization asynchronously - covers auth.py"""
    from apps.user_service.app.api.auth import router, logger

    # Test that router is properly configured
    assert router.prefix == "/auth"
    assert "Authentication" in router.tags

    # Test that logger is initialized
    assert logger is not None

def test_set_password_endpoint_success(auth_client):
    """Test set password - covers auth.py set password function"""
    set_password_data = {"password": "NewPass123!"}

    with patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(return_value=True)):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 202
        data = response.json()
        assert "Password set successfully" in data["message"]

def test_set_password_endpoint_weak_password(auth_client):
    """Test set password with weak password - covers auth.py password validation"""
    set_password_data = {"password": "weak"}

    response = auth_client.post("/auth/set-password", json=set_password_data)
    assert response.status_code == 400
    assert "Password must be at least 6 characters" in response.json()["detail"]

@pytest.mark.asyncio
async def test_auth_helper_functions_async():
    """Test auth helper functions asynchronously - covers auth.py"""
    from apps.user_service.app.api.auth import (
        _is_password_strong,
        _extract_user_type_strict,
        _get_not_found_response
    )
    from fastapi import HTTPException

    # Test password strength
    assert await asyncio.to_thread(_is_password_strong, "Test123!") == True
    assert await asyncio.to_thread(_is_password_strong, "weak") == False

    # Test user type extraction
    mock_row = MagicMock()
    mock_row.user_metadata = {"type": "test"}
    mock_row.app_metadata = {}
    assert await asyncio.to_thread(_extract_user_type_strict, mock_row) == "test"

    # Test response helpers (now returns response object)
    response = await asyncio.to_thread(_get_not_found_response)
    assert response.email_found is False
    assert response.message == "Email not found."
    assert response.status is None
    assert response.can_login is False


# ============================================================================
# OAUTH ENDPOINT TESTS (Missing Coverage)
# ============================================================================

def test_get_oauth_link_url_endpoint_success(auth_client):
    """Test OAuth link URL generation endpoint - covers auth.py OAuth functionality"""
    with patch('libs.shared_middleware.jwt_auth.get_user_from_auth',
               return_value={"sub": "test-user-id", "email": "test@example.com",
                           "app_metadata": {"providers": ["email"]}}), \
         patch('apps.user_service.app.api.auth.get_oauth_link_url',
               AsyncMock(return_value={"oauth_url": "https://oauth.google.com/auth", "message": "Success"})):

        response = auth_client.get("/auth/link-user-oauth-url/google")
        assert response.status_code == 200
        data = response.json()
        assert "oauth_url" in data


def test_get_oauth_link_url_endpoint_already_linked():
    """Test OAuth link URL generation when provider already linked - covers auth.py error handling"""
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    # Override the auth dependency for this specific test
    def mock_get_user_from_auth():
        return {"sub": "test-user-id", "email": "test@example.com",
                "app_metadata": {"providers": ["email", "google"]}}

    app.dependency_overrides[get_user_from_auth] = mock_get_user_from_auth

    with TestClient(app) as client:
        response = client.get("/auth/link-user-oauth-url/google")
        assert response.status_code == 400
        data = response.json()
        assert "google account is already linked to this user" in data["detail"]


def test_get_oauth_link_url_endpoint_error(auth_client):
    """Test OAuth link URL generation error handling - covers auth.py error handling"""
    with patch('libs.shared_middleware.jwt_auth.get_user_from_auth',
               return_value={"sub": "test-user-id", "email": "test@example.com",
                           "app_metadata": {"providers": ["email"]}}), \
         patch('apps.user_service.app.api.auth.get_oauth_link_url',
               AsyncMock(side_effect=Exception("OAuth error"))):

        response = auth_client.get("/auth/link-user-oauth-url/google")
        # The actual function catches exceptions and returns 500
        assert response.status_code == 500
        assert "Failed to generate" in response.json()["detail"]


def test_oauth_connect_endpoint_success(auth_client):
    """Test OAuth connect endpoint - covers auth.py OAuth functionality"""
    with patch('apps.user_service.app.api.auth.supabase_user_oauth',
               AsyncMock(return_value={"url": "https://oauth.google.com/auth"})):

        response = auth_client.get("/auth/oauth-connect/google")
        assert response.status_code == 200
        data = response.json()
        assert "url" in data


def test_oauth_connect_endpoint_error(auth_client):
    """Test OAuth connect endpoint error handling - covers auth.py error handling"""
    with patch('apps.user_service.app.api.auth.supabase_user_oauth',
               AsyncMock(side_effect=Exception("OAuth error"))):

        response = auth_client.get("/auth/oauth-connect/google")
        # The actual function now handles exceptions and returns a 500 error
        assert response.status_code == 500
        data = response.json()
        assert "Failed to generate google OAuth URL" in data["detail"]


def test_oauth_callback_endpoint_success(auth_client):
    """Test OAuth callback endpoint - covers auth.py OAuth functionality"""
    # Create mock objects with proper attributes
    mock_session_result = MagicMock()
    mock_session_result.session = {"access_token": "token"}
    mock_session_result.user = {"id": "user-id"}

    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(return_value=mock_session_result)):

        response = auth_client.get("/auth/oauth-callback?code=test-code")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True
        assert "OAuth authentication successful" in data["message"]


def test_oauth_callback_endpoint_missing_code(auth_client):
    """Test OAuth callback endpoint with missing code - covers auth.py error handling"""
    response = auth_client.get("/auth/oauth-callback")
    assert response.status_code == 400
    assert "Missing authorization code" in response.json()["detail"]


def test_oauth_callback_endpoint_invalid_session(auth_client):
    """Test OAuth callback endpoint with invalid session - covers auth.py error handling"""
    # Create mock object with None session
    mock_session_result = MagicMock()
    mock_session_result.session = None
    mock_session_result.user = None

    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(return_value=mock_session_result)):

        response = auth_client.get("/auth/oauth-callback?code=test-code")
        assert response.status_code == 400
        assert "Failed to exchange authorization code" in response.json()["detail"]


def test_oauth_callback_endpoint_error(auth_client):
    """Test OAuth callback endpoint error handling - covers auth.py error handling"""
    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(side_effect=Exception("Session error"))):

        response = auth_client.get("/auth/oauth-callback?code=test-code")
        assert response.status_code == 500
        assert "Failed to process OAuth callback" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_endpoints_async(async_auth_client):
    """Test OAuth endpoints asynchronously - covers auth.py OAuth functionality"""
    from fastapi import Request
    from apps.user_service.app.api.auth import (
        get_oauth_link_url_endpoint,
        oauth_connect,
        oauth_callback
    )

    # Test get_oauth_link_url_endpoint
    with patch('apps.user_service.app.api.auth.get_oauth_link_url',
               AsyncMock(return_value={"oauth_url": "https://oauth.google.com/auth", "message": "Success"})):
        result = await get_oauth_link_url_endpoint("google", {"sub": "test-user-id", "email": "test@example.com",
                                                              "app_metadata": {"providers": ["email"]}})
        assert "oauth_url" in result

    # Test oauth_connect
    with patch('apps.user_service.app.api.auth.supabase_user_oauth',
               AsyncMock(return_value={"url": "https://oauth.google.com/auth"})):
        result = await oauth_connect("google")
        assert "url" in result

    # Test oauth_callback
    mock_request = MagicMock(spec=Request)
    mock_request.query_params = {"code": "test-code"}

    # Create mock objects with proper attributes
    mock_session_result = MagicMock()
    mock_session_result.session = {"access_token": "token"}
    mock_session_result.user = {"id": "user-id"}

    with patch('apps.user_service.app.api.auth.get_session_by_id_admin',
               AsyncMock(return_value=mock_session_result)):
        result = await oauth_callback(mock_request)
        assert result["success"] == True


# ============================================================================
# MISSING COVERAGE TESTS
# ============================================================================

def test_set_password_update_fails(auth_client):
    """Test set_password when password update fails - covers lines 189"""
    set_password_data = {"password": "NewPass123!"}

    with patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(return_value=False)):  # Return False to trigger failure
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]

def test_set_password_exception_handling(auth_client):
    """Test set_password exception handling - covers lines 195-197"""
    set_password_data = {"password": "NewPass123!"}

    with patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(side_effect=Exception("Database error"))):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]


# ============================================================================
# CHANGE PASSWORD TESTS - NEW CODE COVERAGE
# ============================================================================

def test_change_password_success(auth_client):
    """Test successful password change - covers change_password endpoint"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException, status
    
    # Mock login_user: first call succeeds (current password correct), 
    # second call fails with 401 (new password is different - good!)
    call_count = [0]  # Use list to allow modification in nested function
    async def mock_login_user(email, password):
        call_count[0] += 1
        # First call: current_password check - succeeds
        if call_count[0] == 1:
            return MagicMock()
        # Second call: new_password check - should fail to indicate it's different
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=mock_login_user)), \
         patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(return_value=True)):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 200
        data = response.json()
        assert "Password changed successfully" in data["message"]


def test_change_password_invalid_user_info(auth_client):
    """Test change_password with invalid user information - covers lines 924-931"""
    from apps.user_service.app.api.auth import router as auth_router
    from fastapi import FastAPI
    from libs.shared_middleware.jwt_auth import get_user_from_auth

    app = FastAPI()
    app.include_router(auth_router)

    # Override to return user without email
    def mock_get_user_no_email():
        return {"sub": "test-user-id"}  # Missing email

    app.dependency_overrides[get_user_from_auth] = mock_get_user_no_email
    client = TestClient(app)

    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    response = client.post("/auth/change-password", json=change_password_data)
    assert response.status_code == 401
    assert "Invalid user information" in response.json()["detail"]


def test_change_password_invalid_current_password(auth_client):
    """Test change_password with incorrect current password - covers lines 963-1008"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException, status

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid login credentials"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert "Current password is incorrect" in response.json()["detail"]


def test_change_password_invalid_current_password_unexpected_http_exception(auth_client):
    """Ensure non-400/Invalid login errors from login_user propagate unchanged"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized access"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized access"


def test_change_password_invalid_current_password_wrong_detail(auth_client):
    """Ensure 400 errors without the specific detail are propagated as-is"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=HTTPException(status_code=400, detail="Some other error"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert response.json()["detail"] == "Some other error"


def test_change_password_invalid_current_password_authapierror(auth_client):
    """Test change_password with AuthApiError for invalid current password - covers AuthApiError handling"""
    change_password_data = {
        "current_password": "WrongPass123!",
        "new_password": "NewPass123!"
    }

    from supabase_auth.errors import AuthApiError

    # Mock AuthApiError for invalid credentials
    auth_error = AuthApiError("Invalid login credentials", status=400, code="invalid_credentials")
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=auth_error)):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error during change_password"


def test_change_password_current_password_verification_exception(auth_client):
    """Test change_password when current password verification raises exception - covers lines 1004-1008"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=Exception("Database connection error"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error during change_password"


def test_change_password_update_fails(auth_client):
    """Test change_password when password update returns False - covers lines 1019-1024"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException
    
    # Mock login_user: first call succeeds (current password), second call fails (new password is different)
    call_count = [0]
    async def mock_login_user(email, password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails
    
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=mock_login_user)), \
         patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(return_value=False)):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Failed to update password" in response.json()["detail"]


def test_change_password_update_error_user_not_allowed(auth_client):
    """Test change_password when update raises 'user not allowed' error - covers lines 1026-1032"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }
    
    from fastapi import HTTPException
    
    # Mock login_user: first call succeeds (current password), second call fails (new password is different)
    call_count = [0]
    async def mock_login_user(email, password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails
    
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=mock_login_user)), \
         patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(side_effect=Exception("User not allowed to change password"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 403
        assert "User account is restricted" in response.json()["detail"]


def test_change_password_update_error_authentication_error(auth_client):
    """Test change_password when update raises authentication error - covers lines 1034-1040"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException
    
    # Mock login_user: first call succeeds (current password), second call fails (new password is different)
    call_count = [0]
    async def mock_login_user(email, password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails
    
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=mock_login_user)), \
         patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(side_effect=Exception("Authentication service unavailable"))):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Authentication service error" in response.json()["detail"]


def test_change_password_update_error_generic(auth_client):
    """Test change_password when update raises generic error - covers lines 1042-1048"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!"
    }

    from fastapi import HTTPException
    
    # Mock login_user: first call succeeds (current password), second call fails (new password is different)
    call_count = [0]
    async def mock_login_user(email, password):
        call_count[0] += 1
        if call_count[0] == 1:
            return MagicMock()  # First call succeeds
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")  # Second call fails
    
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=mock_login_user)), \
         patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(side_effect=Exception("Unknown database error"))):        
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 500
        assert "Failed to update password" in response.json()["detail"]


def test_change_password_weak_new_password(auth_client):
    """Test change_password with weak new password - covers password validation"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "weak"  # Too weak
    }

    response = auth_client.post("/auth/change-password", json=change_password_data)
    # Should fail validation (either 400 or 422 depending on validation)
    assert response.status_code in [400, 422]


def test_change_password_same_as_current(auth_client):
    """Test change_password when new password is same as current password - covers lines 953-961"""
    change_password_data = {
        "current_password": "OldPass123!",
        "new_password": "OldPass123!"  # Same as current password
    }

    # Mock login_user to succeed for both calls (current password check and new password check)
    # First call succeeds (current password is correct)
    # Second call also succeeds (new password = current password), which should trigger rejection
    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(return_value=MagicMock())):
        response = auth_client.post("/auth/change-password", json=change_password_data)
        assert response.status_code == 400
        assert "New password must be different from current password" in response.json()["detail"]


def test_set_password_general_exception(auth_client):
    """Test set_password general exception handling - covers line 159"""
    set_password_data = {"password": "NewPass123!"}

    with patch('apps.user_service.app.api.auth.update_password_with_link_identity',
               AsyncMock(side_effect=Exception("General error"))):
        response = auth_client.post("/auth/set-password", json=set_password_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to set password" in data["detail"]

def test_forgot_password_exception_handling(auth_client):
    """Test forgot_password exception handling - covers lines 267-268"""
    forgot_data = {"email": "test@example.com"}

    with patch('apps.user_service.app.api.auth.get_auth_user_by_email',
               AsyncMock(return_value={"id": "user-id"})), \
         patch('apps.user_service.app.api.auth.reset_the_password_email',
               AsyncMock(side_effect=Exception("Email service error"))):
        response = auth_client.post("/auth/forgot-password", json=forgot_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to process password reset request" in data["detail"]

def test_reset_password_user_not_found(auth_client):
    """Test reset_password when user not found - covers line 324"""
    reset_data = {
        "token": "invalid-token",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value=None):  # Return None to trigger user not found
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 404
        data = response.json()
        assert "User not found" in data["detail"]

def test_reset_password_email_error_handling(auth_client):
    """Test reset_password email error handling - covers lines 351-352"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id", "email": "test@example.com",
                           "user_metadata": {"full_name": "Test User"}}), \
         patch('apps.user_service.app.api.auth.update_password_with_token',
               AsyncMock(return_value=MagicMock(user=MagicMock()))), \
         patch('apps.user_service.app.api.auth.send_password_reset_confirmation_email',
               side_effect=Exception("Email service error")):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        # Should still succeed even if email fails
        assert response.status_code == 200
        data = response.json()
        assert "Password reset successfully" in data["message"]

def test_reset_password_update_fails(auth_client):
    """Test reset_password when password update fails - covers lines 359-360"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               return_value={"sub": "user-id"}), \
         patch('apps.user_service.app.api.auth.update_password_with_token',
               AsyncMock(return_value=MagicMock(user=None))):  # Return result without user
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 400
        data = response.json()
        assert "Failed to update password" in data["detail"]

def test_reset_password_general_exception(auth_client):
    """Test reset_password general exception handling - covers line 369"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "NewPass123!"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               side_effect=Exception("Token processing error")):
        response = auth_client.post("/auth/reset-password", json=reset_data)
        assert response.status_code == 500
        data = response.json()
        assert "Failed to reset password" in data["detail"]

def test_login_general_exception(auth_client):
    """Test login general exception handling - covers line 159"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!"
    }

    with patch('apps.user_service.app.api.auth.login_user',
               AsyncMock(side_effect=Exception("General authentication error"))):
        response = auth_client.post("/auth/login", json=login_data)
        assert response.status_code == 500
        data = response.json()
        assert "Authentication failed" in data["detail"]

# Note: Exception handling tests removed as they're covered by the @handle_api_exceptions decorator
# The decorator catches exceptions and converts them to HTTP 500 errors, which is already tested

def test_delete_user_exception_handling(auth_client):
    """Test delete_user exception handling - covers error scenarios"""
    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(side_effect=Exception("Delete service error"))):
        response = auth_client.delete("/auth/user")
        assert response.status_code == 500
        data = response.json()
        assert "Failed to delete user" in data["detail"]
