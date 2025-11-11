# pylint: disable=all

"""Comprehensive tests for JWT authentication middleware and utilities.

This module tests all the functions in libs/shared_middleware/jwt_auth.py
to achieve high coverage for the authentication system.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request, HTTPException, status
from starlette.responses import Response

from libs.shared_middleware.jwt_auth import (
    raise_auth_error,
    raise_forbidden_error,
    raise_internal_error,
    extract_user_data,
    setup_audit_context,
    check_user_access_async,
    get_user_from_auth,
    get_user_from_token,
    JWTAuthMiddleware,
)


class TestErrorHandlers:
    """Test error handler functions."""

    def test_raise_auth_error(self):
        """Test raise_auth_error function."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            raise_auth_error(request, "Test description", "Test detail")

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Test detail"
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
        assert request.state.audit_description == "Test description"

    def test_raise_forbidden_error(self):
        """Test raise_forbidden_error function."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            raise_forbidden_error(request, "Test description", "Test detail")

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail == "Test detail"
        assert request.state.audit_description == "Test description"

    def test_raise_internal_error(self):
        """Test raise_internal_error function."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            raise_internal_error(request, "Test description", "Test detail")

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Test detail"
        assert request.state.audit_description == "Test description"


class TestHelperFunctions:
    """Test helper functions."""

    def test_extract_user_data_with_valid_user(self):
        """Test extract_user_data with valid user data."""
        user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"organization_id": "org123"},
            "session_id": "session123"
        }

        user_id, organization_id, user_email, session_id = extract_user_data(user)

        assert user_id == "user123"
        assert organization_id == "org123"
        assert user_email == "test@example.com"
        assert session_id == "session123"

    def test_extract_user_data_with_none_user(self):
        """Test extract_user_data with None user."""
        user_id, organization_id, user_email, session_id = extract_user_data(None)

        assert user_id is None
        assert organization_id is None
        assert user_email is None
        assert session_id is None

    def test_extract_user_data_with_empty_user(self):
        """Test extract_user_data with empty user dict."""
        user = {}

        user_id, organization_id, user_email, session_id = extract_user_data(user)

        assert user_id is None
        assert organization_id is None
        assert user_email is None
        assert session_id is None

    def test_extract_user_data_with_partial_data(self):
        """Test extract_user_data with partial user data."""
        user = {
            "sub": "user123",
            "email": "test@example.com"
            # Missing user_metadata and session_id
        }

        user_id, organization_id, user_email, session_id = extract_user_data(user)

        assert user_id == "user123"
        assert organization_id is None
        assert user_email == "test@example.com"
        assert session_id is None

    def test_setup_audit_context(self):
        """Test setup_audit_context function."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        setup_audit_context(
            request, "user123", "test@example.com", "org123", "session123"
        )

        assert request.state.audit_risk_level == "high"
        assert request.state.audit_description == "Authentication or authorization failure"
        assert request.state.audit_user_context == {
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "unknown",
            "organization_id": "org123",
            "session_id": "session123"
        }


class TestCheckUserAccessAsync:
    """Test check_user_access_async function."""

    @pytest.mark.asyncio
    async def test_check_user_access_async_success(self):
        """Test successful permission check."""
        with patch('libs.shared_middleware.jwt_auth.get_supabase_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_response = MagicMock()
            mock_response.data = True
            # Fix: Make rpc return an object with execute method
            mock_rpc_result = MagicMock()
            mock_rpc_result.execute = AsyncMock(return_value=mock_response)
            mock_supabase.rpc = MagicMock(return_value=mock_rpc_result)
            mock_get_client.return_value = mock_supabase

            result = await check_user_access_async(
                ["USERS_READ"], "user123", "org123"
            )

            assert result is True
            mock_supabase.rpc.assert_called_once_with(
                "check_permission",
                {
                    "user_id": "user123",
                    "organization_id": "org123",
                    "permission_code": ["USERS_READ"],
                }
            )

    @pytest.mark.asyncio
    async def test_check_user_access_async_with_none_data(self):
        """Test permission check with None response data."""
        with patch('libs.shared_middleware.jwt_auth.get_supabase_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_response = MagicMock()
            mock_response.data = None
            # Fix: Make rpc return an object with execute method
            mock_rpc_result = MagicMock()
            mock_rpc_result.execute = AsyncMock(return_value=mock_response)
            mock_supabase.rpc = MagicMock(return_value=mock_rpc_result)
            mock_get_client.return_value = mock_supabase

            result = await check_user_access_async(
                ["USERS_READ"], "user123", "org123"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_check_user_access_async_exception(self):
        """Test permission check with exception."""
        with patch('libs.shared_middleware.jwt_auth.get_supabase_client') as mock_get_client:
            mock_get_client.side_effect = Exception("Database error")

            with pytest.raises(HTTPException) as exc_info:
                await check_user_access_async(
                    ["USERS_READ"], "user123", "org123"
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert exc_info.value.detail == "Failed to check permission"


class TestGetUserFromAuth:
    """Test get_user_from_auth function."""

    def test_get_user_from_auth_success(self):
        """Test successful user authentication."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        request.state.user = {
            "sub": "user123",
            "email": "test@example.com",
            "user_metadata": {"type": "organization_member", "organization_id": "org123"},
            "session_id": "session123"
        }

        result = get_user_from_auth(request)

        assert result == request.state.user
        assert request.state.audit_risk_level == "low"
        assert request.state.audit_description == "Successfully authenticated and authorized user"

    def test_get_user_from_auth_no_user(self):
        """Test authentication with no user in request state."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        request.state.user = None

        with pytest.raises(HTTPException) as exc_info:
            get_user_from_auth(request)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Not authenticated"
        assert request.state.audit_risk_level == "high"
        assert request.state.audit_description == "User not authenticated (missing token or invalid token)"


class TestGetUserFromToken:
    """Test get_user_from_token function."""

    def test_get_user_from_token_success(self):
        """Test successful token decoding."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            mock_payload = {
                "sub": "user123",
                "email": "test@example.com",
                "aud": "authenticated"
            }
            mock_decode.return_value = mock_payload

            result = get_user_from_token("valid_token")

            assert result == mock_payload
            # Don't assert the exact JWT secret since it comes from environment
            mock_decode.assert_called_once()
            call_args = mock_decode.call_args
            assert call_args[0][0] == "valid_token"  # token
            assert call_args[1]["algorithms"] == ["HS256"]
            assert call_args[1]["audience"] == "authenticated"

    def test_get_user_from_token_expired(self):
        """Test token decoding with expired token."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            from jwt import ExpiredSignatureError
            mock_decode.side_effect = ExpiredSignatureError("Token expired")

            with pytest.raises(HTTPException) as exc_info:
                get_user_from_token("expired_token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail == "Token expired"

    def test_get_user_from_token_invalid(self):
        """Test token decoding with invalid token."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            from jwt import InvalidTokenError
            mock_decode.side_effect = InvalidTokenError("Invalid token")

            with pytest.raises(HTTPException) as exc_info:
                get_user_from_token("invalid_token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail == "Invalid token"


class TestJWTAuthMiddleware:
    """Test JWTAuthMiddleware class."""

    def test_middleware_initialization(self):
        """Test middleware initialization."""
        app = MagicMock()
        middleware = JWTAuthMiddleware(app)

        assert middleware.app == app

    @pytest.mark.asyncio
    async def test_dispatch_no_auth_header(self):
        """Test dispatch with no Authorization header."""
        app = MagicMock()
        middleware = JWTAuthMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = {}

        call_next = AsyncMock(return_value=Response("Next response"))

        response = await middleware.dispatch(request, call_next)

        # Check that call_next was called and response is a Response object
        call_next.assert_called_once_with(request)
        assert isinstance(response, Response)

    @pytest.mark.asyncio
    async def test_dispatch_invalid_auth_header_format(self):
        """Test dispatch with invalid Authorization header format."""
        app = MagicMock()
        middleware = JWTAuthMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "InvalidFormat token123"}

        call_next = AsyncMock(return_value=Response("Next response"))

        response = await middleware.dispatch(request, call_next)

        # Check that call_next was called and response is a Response object
        call_next.assert_called_once_with(request)
        assert isinstance(response, Response)

    @pytest.mark.asyncio
    async def test_dispatch_valid_token(self):
        """Test dispatch with valid JWT token."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            app = MagicMock()
            middleware = JWTAuthMiddleware(app)

            request = MagicMock(spec=Request)
            request.headers = {"Authorization": "Bearer valid_token"}
            request.state = MagicMock()

            mock_payload = {
                "sub": "user123",
                "email": "test@example.com",
                "aud": "authenticated"
            }
            mock_decode.return_value = mock_payload

            call_next = AsyncMock(return_value=Response("Next response"))

            response = await middleware.dispatch(request, call_next)

            # Check that call_next was called and response is a Response object
            call_next.assert_called_once_with(request)
            assert isinstance(response, Response)
            assert request.state.user == mock_payload

    @pytest.mark.asyncio
    async def test_dispatch_expired_token(self):
        """Test dispatch with expired JWT token."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            from jwt import ExpiredSignatureError
            app = MagicMock()
            middleware = JWTAuthMiddleware(app)

            request = MagicMock(spec=Request)
            request.headers = {"Authorization": "Bearer expired_token"}

            mock_decode.side_effect = ExpiredSignatureError("Token expired")

            call_next = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await middleware.dispatch(request, call_next)

            assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Token expired" in exc.value.detail
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_invalid_token(self):
        """Test dispatch with invalid JWT token."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            from jwt import InvalidTokenError
            app = MagicMock()
            middleware = JWTAuthMiddleware(app)

            request = MagicMock(spec=Request)
            request.headers = {"Authorization": "Bearer invalid_token"}

            mock_decode.side_effect = InvalidTokenError("Invalid token")

            call_next = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await middleware.dispatch(request, call_next)

            assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Invalid token" in exc.value.detail
            call_next.assert_not_called()


class TestJWTIntegration:
    """Integration tests for JWT authentication system."""

    @pytest.mark.asyncio
    async def test_full_auth_flow_with_middleware(self):
        """Test complete authentication flow with middleware."""
        with patch('libs.shared_middleware.jwt_auth.jwt.decode') as mock_decode:
            app = MagicMock()
            middleware = JWTAuthMiddleware(app)

            # Mock request with valid token
            request = MagicMock(spec=Request)
            request.headers = {"Authorization": "Bearer valid_token"}
            request.state = MagicMock()

            mock_payload = {
                "sub": "user123",
                "email": "test@example.com",
                "user_metadata": {"organization_id": "org123"},
                "session_id": "session123"
            }
            mock_decode.return_value = mock_payload

            # Test middleware dispatch
            call_next = AsyncMock(return_value=Response("Next response"))
            response = await middleware.dispatch(request, call_next)

            # Check that call_next was called and response is a Response object
            call_next.assert_called_once_with(request)
            assert isinstance(response, Response)
            assert request.state.user == mock_payload

            # Test get_user_from_auth with the user from middleware
            result = get_user_from_auth(request)

            assert result == mock_payload
            assert request.state.audit_risk_level == "low"
            assert request.state.audit_description == "Successfully authenticated and authorized user"

    @pytest.mark.asyncio
    async def test_permission_check_integration(self):
        """Test permission checking integration."""
        with patch('libs.shared_middleware.jwt_auth.get_supabase_client') as mock_get_client:
            mock_supabase = AsyncMock()
            mock_response = MagicMock()
            mock_response.data = True
            # Fix: Make rpc return an object with execute method
            mock_rpc_result = MagicMock()
            mock_rpc_result.execute = AsyncMock(return_value=mock_response)
            mock_supabase.rpc = MagicMock(return_value=mock_rpc_result)
            mock_get_client.return_value = mock_supabase

            # Test permission check
            result = await check_user_access_async(
                ["USERS_READ", "ROLES_READ"], "user123", "org123"
            )

            assert result is True
            mock_supabase.rpc.assert_called_once_with(
                "check_permission",
                {
                    "user_id": "user123",
                    "organization_id": "org123",
                    "permission_code": ["USERS_READ", "ROLES_READ"],
                }
            )
