"""
Async integration tests for authentication endpoints.
Tests auth.py endpoints with proper AsyncMock usage.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
import uuid
import asyncio

@pytest.fixture
def auth_client():
    """Test client for auth endpoints"""
    from fastapi import FastAPI
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    with TestClient(app) as client:
        yield client

@pytest.fixture
def async_auth_client():
    """Async test client for auth endpoints"""
    from fastapi import FastAPI
    from apps.user_service.app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    return app

def test_login_endpoint_success(auth_client):
    """Test successful login - covers auth.py login function"""
    login_data = {
        "email": "test@example.com",
        "password": "TestPass123!"
    }

    # Mock the Supabase login response
    mock_result = MagicMock()
    mock_result.session.access_token = "test-access-token"
    mock_result.user.id = "test-user-id"
    mock_result.user.email = "test@example.com"
    mock_result.user.user_metadata = {"first_name": "Test", "last_name": "User"}

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
    mock_result = MagicMock()
    mock_result.session.access_token = "test-access-token"
    mock_result.user.id = "test-user-id"
    mock_result.user.email = "test@example.com"
    mock_result.user.user_metadata = {"first_name": "Test", "last_name": "User"}

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
        assert response.status_code == 401
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

        assert exc_info.value.status_code == 401
        assert "Invalid login credentials" in exc_info.value.detail

def test_signup_endpoint_success(auth_client):
    """Test successful signup - covers auth.py signup function"""
    signup_data = {
        "user_data": {
            "email": "newuser@example.com",
            "password": "NewPass123!",
            "full_name": "New User"
        }
    }

    with patch('apps.user_service.app.api.auth.sign_up_supabase_user',
               AsyncMock(return_value="new-user-id")):
        response = auth_client.post("/auth/signup", json=signup_data)
        assert response.status_code == 201
        data = response.json()
        assert "Account created successfully" in data["message"]
        assert data["data"]["user_id"] == "new-user-id"

@pytest.mark.asyncio
async def test_signup_endpoint_success_async(async_auth_client):
    """Test successful signup asynchronously - covers auth.py signup function"""
    from fastapi.testclient import TestClient

    signup_data = {
        "user_data": {
            "email": "newuser@example.com",
            "password": "NewPass123!",
            "full_name": "New User"
        }
    }

    with patch('apps.user_service.app.api.auth.sign_up_supabase_user',
               AsyncMock(return_value="new-user-id")):
        with TestClient(async_auth_client) as client:
            response = client.post("/auth/signup", json=signup_data)
            assert response.status_code == 201
            data = response.json()
            assert "Account created successfully" in data["message"]
            assert data["data"]["user_id"] == "new-user-id"

def test_signup_endpoint_weak_password(auth_client):
    """Test signup with weak password - covers auth.py password validation"""
    signup_data = {
        "user_data": {
            "email": "newuser@example.com",
            "password": "weak",
            "full_name": "New User"
        }
    }

    response = auth_client.post("/auth/signup", json=signup_data)
    assert response.status_code == 400
    assert "Password must be at least 6 characters" in response.json()["detail"]

@pytest.mark.asyncio
async def test_signup_endpoint_weak_password_async(async_auth_client):
    """Test signup with weak password asynchronously - covers auth.py password validation"""
    from fastapi.testclient import TestClient

    signup_data = {
        "user_data": {
            "email": "newuser@example.com",
            "password": "weak",
            "full_name": "New User"
        }
    }

    with TestClient(async_auth_client) as client:
        response = client.post("/auth/signup", json=signup_data)
        assert response.status_code == 400
        assert "Password must be at least 6 characters" in response.json()["detail"]

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

        assert result.status_code == 200
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
               AsyncMock(return_value={"sub": "user-id"})), \
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
               AsyncMock(return_value={"sub": "user-id"})), \
         patch('apps.user_service.app.api.auth.update_password_with_token',
               AsyncMock(return_value=MagicMock(user=MagicMock()))):
        mock_request = MagicMock(spec=Request)
        result = await reset_password(request=mock_request, data=reset_data)

        assert result.status_code == 200
        assert "Password reset successfully" in result.message

def test_reset_password_endpoint_weak_password(auth_client):
    """Test reset password with weak password - covers auth.py password validation"""
    reset_data = {
        "token": "valid-reset-token",
        "new_password": "weak"
    }

    with patch('apps.user_service.app.api.auth.get_user_from_token',
               AsyncMock(return_value={"sub": "user-id"})):
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
               AsyncMock(return_value={"sub": "user-id"})):
        mock_request = MagicMock(spec=Request)

        with pytest.raises(HTTPException) as exc_info:
            await reset_password(request=mock_request, data=reset_data)

        assert exc_info.value.status_code == 400
        assert "Password must be at least 6 characters" in exc_info.value.detail

def test_verify_email_endpoint_success(auth_client):
    """Test verify email - covers auth.py verify email function"""
    verify_data = {"email": "test@example.com"}

    mock_auth_user = {
        "raw_user_meta_data": '{"type": "organization_member"}',
        "raw_app_meta_data": '{}'
    }

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

    mock_auth_user = {
        "raw_user_meta_data": '{"type": "organization_member"}',
        "raw_app_meta_data": '{}'
    }

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
        assert data["email_found"] == False
        assert data["can_login"] == False

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
        result = await verify_email(request=mock_request, body=verify_data)

        assert result.email_found == False
        assert result.can_login == False

def test_delete_user_endpoint_success(auth_client):
    """Test delete user - covers auth.py delete user function"""
    user_id = str(uuid.uuid4())

    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value={"message": "User deleted"})):
        response = auth_client.delete(f"/auth/user/{user_id}")
        assert response.status_code == 200
        data = response.json()
        assert "deleted successfully" in data["message"]
        assert data["deleted_user_id"] == user_id

@pytest.mark.asyncio
async def test_delete_user_endpoint_success_async(async_auth_client):
    """Test delete user asynchronously - covers auth.py delete user function"""
    from fastapi import Request
    from apps.user_service.app.api.auth import delete_user

    user_id = str(uuid.uuid4())

    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value={"message": "User deleted"})):
        mock_request = MagicMock(spec=Request)
        result = await delete_user(request=mock_request, user_id=user_id)

        assert result["status_code"] == 200
        assert "deleted successfully" in result["message"]
        assert result["deleted_user_id"] == user_id

def test_delete_user_endpoint_not_found(auth_client):
    """Test delete user with non-existent user - covers auth.py error handling"""
    user_id = str(uuid.uuid4())

    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value=None)):
        response = auth_client.delete(f"/auth/user/{user_id}")
        assert response.status_code == 200
        data = response.json()
        assert "No user found" in data["message"]
        assert data["deleted_user_id"] is None

@pytest.mark.asyncio
async def test_delete_user_endpoint_not_found_async(async_auth_client):
    """Test delete user with non-existent user asynchronously - covers auth.py error handling"""
    from fastapi import Request
    from apps.user_service.app.api.auth import delete_user

    user_id = str(uuid.uuid4())

    with patch('apps.user_service.app.api.auth.delete_auth_user',
               AsyncMock(return_value=None)):
        mock_request = MagicMock(spec=Request)
        result = await delete_user(request=mock_request, user_id=user_id)

        assert result["status_code"] == 200
        assert "No user found" in result["message"]
        assert result["deleted_user_id"] is None

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

def test_prepare_signup_response_data():
    """Test signup response data preparation - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _prepare_signup_response_data
    from apps.user_service.app.schemas.auth import SignupRequest, UserSignupData

    user_data = UserSignupData(
        email="test@example.com",
        password="TestPass123!",
        full_name="Test User"
    )
    signup_data = SignupRequest(user_data=user_data)

    result = _prepare_signup_response_data(
        user_id="test-user-id",
        signup_data=signup_data
    )

    assert result["user_id"] == "test-user-id"

def test_parse_meta_helper():
    """Test metadata parsing helper - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _parse_meta
    import json

    # Test dict input
    assert _parse_meta({"key": "value"}) == {"key": "value"}

    # Test valid JSON string
    assert _parse_meta('{"key": "value"}') == {"key": "value"}

    # Test invalid JSON string
    assert _parse_meta('invalid json') is None

    # Test non-string, non-dict input
    assert _parse_meta(123) is None

def test_extract_user_type_strict():
    """Test user type extraction - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _extract_user_type_strict

    # Test with user_meta_data
    row1 = {"raw_user_meta_data": '{"type": "organization_member"}'}
    assert _extract_user_type_strict(row1) == "organization_member"

    # Test with app_meta_data
    row2 = {"raw_app_meta_data": '{"user_type": "admin"}'}
    assert _extract_user_type_strict(row2) == "admin"

    # Test with None input
    assert _extract_user_type_strict(None) is None

    # Test with empty row
    assert _extract_user_type_strict({}) is None

def test_response_found_helper():
    """Test response found helper - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _response_found

    # Test active status
    response = _response_found("active")
    assert response.email_found == True
    assert response.can_login == True
    assert response.status == "active"

    # Test suspended status
    response = _response_found("suspended")
    assert response.email_found == True
    assert response.can_login == False
    assert response.status == "suspended"

def test_get_not_found_response_helper():
    """Test not found response helper - covers auth.py helper function"""
    from apps.user_service.app.api.auth import _get_not_found_response

    response = _get_not_found_response()
    assert response.status_code == 404
    assert response.email_found == False
    assert response.can_login == False
    assert response.status is None

@pytest.mark.asyncio
async def test_auth_module_initialization_async():
    """Test auth module initialization asynchronously - covers auth.py"""
    from apps.user_service.app.api.auth import router, logger

    # Test that router is properly configured
    assert router.prefix == "/auth"
    assert "Authentication" in router.tags

    # Test that logger is initialized
    assert logger is not None

@pytest.mark.asyncio
async def test_auth_helper_functions_async():
    """Test auth helper functions asynchronously - covers auth.py"""
    from apps.user_service.app.api.auth import (
        _is_password_strong,
        _parse_meta,
        _extract_user_type_strict,
        _response_found,
        _get_not_found_response
    )

    # Test password strength
    assert await asyncio.to_thread(_is_password_strong, "Test123!") == True
    assert await asyncio.to_thread(_is_password_strong, "weak") == False

    # Test meta parsing
    assert await asyncio.to_thread(_parse_meta, {"key": "value"}) == {"key": "value"}
    assert await asyncio.to_thread(_parse_meta, 'invalid') is None

    # Test user type extraction
    row = {"raw_user_meta_data": '{"type": "test"}'}
    assert await asyncio.to_thread(_extract_user_type_strict, row) == "test"

    # Test response helpers
    response = await asyncio.to_thread(_response_found, "active")
    assert response.email_found == True
    assert response.can_login == True

    not_found = await asyncio.to_thread(_get_not_found_response)
    assert not_found.status_code == 404
    assert not_found.email_found == False