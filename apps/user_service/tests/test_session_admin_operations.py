# pylint: disable=all

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from postgrest import APIError
from httpx import HTTPError, RequestError, TimeoutException

from libs.shared_db.supabase_db.admin_operations.session import get_session_by_id_admin
from apps.user_service.app.schemas.auth import CODE_VERIFIER


class TestGetSessionByIdAdmin:
    """Test cases for get_session_by_id_admin function."""

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_success(self):
        """Test successful session retrieval by admin."""
        auth_code = "test_auth_code_123"
        mock_session_result = {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_456",
            "user": {
                "id": "user_123",
                "email": "test@example.com"
            },
            "expires_in": 3600
        }

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(return_value=mock_session_result)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            result = await get_session_by_id_admin(auth_code)

            # Verify the result
            assert result == mock_session_result

            # Verify the function was called with correct arguments
            expected_args = {
                "auth_code": auth_code,
                "code_verifier": CODE_VERIFIER
            }
            mock_exchange.assert_called_once_with(expected_args)

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_api_error(self):
        """Test session retrieval with Supabase API error."""
        auth_code = "test_auth_code_123"
        api_error = APIError({"message": "Invalid auth code", "code": "invalid_grant"})

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=api_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the APIError is raised
            with pytest.raises(APIError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == api_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_http_error(self):
        """Test session retrieval with HTTP error."""
        auth_code = "test_auth_code_123"
        http_error = HTTPError("Connection failed")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=http_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the HTTPError is raised
            with pytest.raises(HTTPError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == http_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_request_error(self):
        """Test session retrieval with Request error."""
        auth_code = "test_auth_code_123"
        request_error = RequestError("Request timeout")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=request_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the RequestError is raised
            with pytest.raises(RequestError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == request_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_timeout_error(self):
        """Test session retrieval with Timeout error."""
        auth_code = "test_auth_code_123"
        timeout_error = TimeoutException("Request timed out")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=timeout_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the TimeoutException is raised
            with pytest.raises(TimeoutException) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == timeout_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_key_error(self):
        """Test session retrieval with KeyError."""
        auth_code = "test_auth_code_123"
        key_error = KeyError("missing_key")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=key_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the KeyError is raised
            with pytest.raises(KeyError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == key_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_type_error(self):
        """Test session retrieval with TypeError."""
        auth_code = "test_auth_code_123"
        type_error = TypeError("Invalid type")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=type_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the TypeError is raised
            with pytest.raises(TypeError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == type_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_value_error(self):
        """Test session retrieval with ValueError."""
        auth_code = "test_auth_code_123"
        value_error = ValueError("Invalid value")

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(side_effect=value_error)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            # Verify the ValueError is raised
            with pytest.raises(ValueError) as exc_info:
                await get_session_by_id_admin(auth_code)

            assert exc_info.value == value_error

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_empty_code(self):
        """Test session retrieval with empty auth code."""
        auth_code = ""
        mock_session_result = {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_456",
            "user": {
                "id": "user_123",
                "email": "test@example.com"
            },
            "expires_in": 3600
        }

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(return_value=mock_session_result)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            result = await get_session_by_id_admin(auth_code)

            # Verify the result
            assert result == mock_session_result

            # Verify the function was called with empty auth_code
            expected_args = {
                "auth_code": "",
                "code_verifier": CODE_VERIFIER
            }
            mock_exchange.assert_called_once_with(expected_args)

    @pytest.mark.asyncio
    async def test_get_session_by_id_admin_none_code(self):
        """Test session retrieval with None auth code."""
        auth_code = None
        mock_session_result = {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_456",
            "user": {
                "id": "user_123",
                "email": "test@example.com"
            },
            "expires_in": 3600
        }

        with patch("libs.shared_db.supabase_db.admin_operations.session.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_auth = MagicMock()
            mock_exchange = AsyncMock(return_value=mock_session_result)

            # Set up the mock chain
            mock_supabase.auth = mock_auth
            mock_auth.exchange_code_for_session = mock_exchange
            mock_get_client.return_value = mock_supabase

            result = await get_session_by_id_admin(auth_code)

            # Verify the result
            assert result == mock_session_result

            # Verify the function was called with None auth_code
            expected_args = {
                "auth_code": None,
                "code_verifier": CODE_VERIFIER
            }
            mock_exchange.assert_called_once_with(expected_args)
