"""Tests functions in user_utility_admin.py module."""
# pylint: disable=too-many-lines

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import HTTPError, HTTPStatusError
from postgrest import APIError
from supabase_auth.errors import AuthApiError

from apps.user_service.app.schemas.users import CreateUserRequest
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    delete_auth_user,
    unban_the_user,
    update_email_of_user,
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    create_admin_update_email_content,
    generate_magic_link,
    invite_user_with_email,
    login_user,
    refresh_session,
    reset_the_password_email,
    send_admin_update_email,
    sign_up_supabase_user,
    update_password_with_token,
    update_supabase_user_email,
)
from libs.shared_utils.common_query import USER_NOT_FOUND_MESSAGE


class TestUpdateSupabaseUserEmail:
    """Test cases for update_supabase_user_email function."""

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_success(self):
        """Test successful email update."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        mock_user_info = {
            "user_id": user_id,
            "full_name": "Test User",
            "email": "old@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_user_profile_by_id"
                ),
                AsyncMock(return_value=mock_user_info),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_email_of_user"
                ),
                AsyncMock(return_value={"success": True}),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_user_email"
                ),
                AsyncMock(return_value=True),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.send_admin_update_email"
                ),
                return_value=True,
            ),
        ):
            # Should not raise any exception
            await update_supabase_user_email(user_id, org_id, new_email)

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_user_not_found(self):
        """Test email update when user not found."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 404
            assert USER_NOT_FOUND_MESSAGE in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_supabase_failure(self):
        """Test email update when Supabase update fails."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        mock_user_info = {
            "user_id": user_id,
            "full_name": "Test User",
            "email": "old@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_user_profile_by_id"
                ),
                AsyncMock(return_value=mock_user_info),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_email_of_user"
                ),
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 500
            assert "Failed to update user email" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_member_not_found(self):
        """Test email update when organization member not found."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        mock_user_info = {
            "user_id": user_id,
            "full_name": "Test User",
            "email": "old@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_user_profile_by_id"
                ),
                AsyncMock(return_value=mock_user_info),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_email_of_user"
                ),
                AsyncMock(return_value={"success": True}),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_user_email"
                ),
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 404
            assert "Member not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_general_exception(self):
        """Test email update with general exception."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_user_profile_by_id"
            ),
            side_effect=Exception("Database error"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 500
            assert "Internal server error" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_email_send_fails(self):
        """Test email update when email sending fails - covers line 67."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        mock_user_info = {
            "user_id": user_id,
            "full_name": "Test User",
            "email": "old@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_user_profile_by_id"
                ),
                AsyncMock(return_value=mock_user_info),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_email_of_user"
                ),
                AsyncMock(return_value={"success": True}),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.update_user_email"
                ),
                AsyncMock(return_value=True),
            ),
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.send_admin_update_email"
                ),
                AsyncMock(return_value=False),
            ),
            patch(
                "libs.shared_db.supabase_db.admin_operations.user_utility_admin.logger"
            ) as mock_logger,
        ):
            # Should not raise exception, but should log warning
            await update_supabase_user_email(user_id, org_id, new_email)

            # Verify warning was logged
            mock_logger.warning.assert_called_once()
            assert new_email in str(mock_logger.warning.call_args)


class TestGenerateMagicLink:
    """Test cases for generate_magic_link function."""

    @pytest.mark.asyncio
    async def test_generate_magic_link_success(self):
        """Test successful magic link generation."""
        email = "test@example.com"
        magic_link_url = "https://example.com/magic-link"

        mock_response = MagicMock()
        mock_response.properties.action_link = magic_link_url

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(
                        admin=MagicMock(generate_link=MagicMock(return_value=mock_response))
                    )
                )
            ),
        ):
            result = await generate_magic_link(email)
            assert result == magic_link_url

    @pytest.mark.asyncio
    async def test_generate_magic_link_no_properties(self):
        """Test magic link generation when response has no properties."""
        email = "test@example.com"

        mock_response = MagicMock()
        mock_response.properties = None

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(
                        admin=MagicMock(generate_link=MagicMock(return_value=mock_response))
                    )
                )
            ),
        ):
            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_no_action_link(self):
        """Test magic link generation when action_link is None."""
        email = "test@example.com"

        mock_response = MagicMock()
        mock_response.properties.action_link = None

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(
                        admin=MagicMock(generate_link=MagicMock(return_value=mock_response))
                    )
                )
            ),
        ):
            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_value_error(self):
        """Test magic link generation with ValueError."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=ValueError("Invalid email")),
        ):
            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_attribute_error(self):
        """Test magic link generation with AttributeError."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=AttributeError("No attribute")),
        ):
            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_general_exception(self):
        """Test magic link generation with general exception."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=Exception("Unexpected error")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await generate_magic_link(email)

            assert exc_info.value.status_code == 500
            assert "Failed to generate magic link" in exc_info.value.detail


class TestCreateAdminUpdateEmailContent:
    """Test cases for create_admin_update_email_content function."""

    def test_create_admin_update_email_content_success(self):
        """Test successful email content creation."""
        user = {"full_name": "John Doe", "email": "john@example.com"}
        magic_link = "https://example.com/magic-link"

        subject, html_message = create_admin_update_email_content(user, magic_link)

        assert subject == "Your Email Has Been Updated - XQtiv"
        assert "John Doe" in html_message
        assert magic_link in html_message
        assert "Magic Link" in html_message

    def test_create_admin_update_email_content_empty_name(self):
        """Test email content creation with empty full name."""
        user = {"full_name": "", "email": "john@example.com"}
        magic_link = "https://example.com/magic-link"

        subject, html_message = create_admin_update_email_content(user, magic_link)

        assert subject == "Your Email Has Been Updated - XQtiv"
        assert magic_link in html_message
        assert "Hello ," in html_message  # Empty name should result in empty greeting


class TestSendAdminUpdateEmail:
    """Test cases for send_admin_update_email function."""

    @pytest.mark.asyncio
    async def test_send_admin_update_email_success(self):
        """Test successful email sending."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.generate_magic_link"
                ),
                AsyncMock(return_value="https://example.com/magic-link"),
            ),
            patch(
                "libs.shared_db.supabase_db.admin_operations.user_utility_admin.send_email",
                return_value=True,
            ),
        ):
            result = await send_admin_update_email(user)
            assert result is True

    @pytest.mark.asyncio
    async def test_send_admin_update_email_no_magic_link(self):
        """Test email sending when magic link generation fails."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
            AsyncMock(return_value=None),
        ):
            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_send_failure(self):
        """Test email sending when send_email fails."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.generate_magic_link"
                ),
                AsyncMock(return_value="https://example.com/magic-link"),
            ),
            patch(
                "libs.shared_db.supabase_db.admin_operations.user_utility_admin.send_email",
                return_value=False,
            ),
        ):
            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_value_error(self):
        """Test email sending with ValueError."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
            AsyncMock(side_effect=ValueError("Invalid email")),
        ):
            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_attribute_error(self):
        """Test email sending with AttributeError."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
            AsyncMock(side_effect=AttributeError("No attribute")),
        ):
            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_general_exception(self):
        """Test email sending with general exception."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com",
        }

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
            AsyncMock(side_effect=Exception("Unexpected error")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await send_admin_update_email(user)

            assert exc_info.value.status_code == 500
            assert "Failed to send admin update email" in exc_info.value.detail


class TestSignUpSupabaseUser:
    """Test cases for sign_up_supabase_user function."""

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_success(self):
        """Test successful user signup."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user.id = user_id
        mock_response.session = MagicMock()
        mock_response.session.access_token = "test-token"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(sign_up=AsyncMock(return_value=mock_response))
                )
            ),
        ):
            result = await sign_up_supabase_user(body)
            assert result.user.id == user_id
            assert result.session.access_token == "test-token"

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_no_user_response(self):
        """Test user signup when response has no user."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        mock_response = MagicMock()
        mock_response.user = None

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(sign_up=AsyncMock(return_value=mock_response))
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            # The function returns 400 for missing user, which is correct
            assert exc_info.value.status_code == 400
            assert "Failed to create user account" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_duplicate_email(self):
        """Test user signup with duplicate email."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=AuthApiError("already registered", 409, "USER_ALREADY_EXISTS")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            assert exc_info.value.status_code == 409
            assert "Email already registered" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_connection_error(self):
        """Test user signup with connection error."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        # Mock ConnectionError during sign_up call, not during get_supabase_admin_client
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up = AsyncMock(side_effect=ConnectionError("Connection failed"))

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(return_value=mock_supabase),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            # ConnectionError should return 503 Service Unavailable
            assert exc_info.value.status_code == 503
            assert "Service temporarily unavailable" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_unexpected_error(self):
        """Test user signup with unexpected error - covers line 293."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        # Mock an error that doesn't match any patterns
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up = AsyncMock(side_effect=Exception("Unexpected database error"))

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(return_value=mock_supabase),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            # Should return 500 for unexpected errors
            assert exc_info.value.status_code == 500
            assert "Failed to create user account" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_value_error(self):
        """Test user signup with ValueError - covers lines 308-309."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up = AsyncMock(side_effect=ValueError("Invalid input data"))

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(return_value=mock_supabase),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            # ValueError should return 400 Bad Request
            assert exc_info.value.status_code == 400
            assert "Invalid request" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_auth_api_error(self):
        """Test user signup with AuthApiError - covers lines 308-309."""
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.first_name = "Test"
        body.last_name = None
        body.phone = None
        body.timezone = "UTC"

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up = AsyncMock(
            side_effect=AuthApiError("Invalid request", 400, "INVALID_REQUEST")
        )

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(return_value=mock_supabase),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            # AuthApiError should return 400 Bad Request
            assert exc_info.value.status_code == 400
            assert "Invalid request" in exc_info.value.detail


class TestLoginUser:
    """Test cases for login_user function."""

    @pytest.mark.asyncio
    async def test_login_user_success(self):
        """Test successful user login."""
        email = "test@example.com"
        password = "password123"

        mock_result = {"access_token": "token123", "user": {"id": "user123"}}

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_supabase_admin_client"
                ),
                AsyncMock(
                    return_value=MagicMock(
                        auth=MagicMock(sign_in_with_password=AsyncMock(return_value=mock_result))
                    )
                ),
            ),
            patch(("libs.shared_db.supabase_db.admin_operations.user_utility_admin.log_exception")),
        ):
            result = await login_user(email, password)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_login_user_failure(self):
        """Test user login failure."""
        email = "test@example.com"
        password = "wrongpassword"

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_supabase_admin_client"
                ),
                AsyncMock(
                    return_value=MagicMock(
                        auth=MagicMock(
                            sign_in_with_password=AsyncMock(
                                side_effect=Exception("Invalid credentials")
                            )
                        )
                    )
                ),
            ),
            patch(("libs.shared_db.supabase_db.admin_operations.user_utility_admin.log_exception")),
        ):
            with pytest.raises(Exception) as exc_info:
                await login_user(email, password)

            assert "Invalid credentials" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_user_email_not_confirmed(self):
        """Test login when email is not confirmed - covers lines 341-342."""
        email = "test@example.com"
        password = "password123"

        auth_error = AuthApiError("Email not confirmed", 400, "EMAIL_NOT_CONFIRMED")
        auth_error.status = 400
        auth_error.message = "Email not confirmed"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(sign_in_with_password=AsyncMock(side_effect=auth_error))
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await login_user(email, password)

            assert exc_info.value.status_code == 403
            assert "Email not confirmed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_user_invalid_credentials(self):
        """Test login with invalid credentials - covers lines 343-344."""
        email = "test@example.com"
        password = "wrongpassword"

        auth_error = AuthApiError("Invalid login credentials", 400, "INVALID_CREDENTIALS")
        auth_error.status = 400
        auth_error.message = "Invalid login credentials"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(sign_in_with_password=AsyncMock(side_effect=auth_error))
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await login_user(email, password)

            assert exc_info.value.status_code == 400
            assert "Invalid Password" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_user_auth_api_error_other(self):
        """Test login with other AuthApiError - covers lines 345-346."""
        email = "test@example.com"
        password = "password123"

        auth_error = AuthApiError("Account suspended", 403, "ACCOUNT_SUSPENDED")
        auth_error.status = 403
        auth_error.message = "Account suspended"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(sign_in_with_password=AsyncMock(side_effect=auth_error))
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await login_user(email, password)

            assert exc_info.value.status_code == 403
            assert "Account suspended" in exc_info.value.detail


class TestInviteUserWithEmail:
    """Test cases for invite_user_with_email function."""

    @pytest.mark.asyncio
    async def test_invite_user_with_email_success(self):
        """Test successful user invitation."""
        body = CreateUserRequest(
            email="test@example.com",
            full_name="Test User",
            phone="1234567890",
            timezone="UTC",
            role_id=str(uuid.uuid4()),
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member",
        )
        user_id = str(uuid.uuid4())

        mock_response = MagicMock()
        mock_response.user.id = user_id

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(
                        admin=MagicMock(invite_user_by_email=AsyncMock(return_value=mock_response))
                    )
                )
            ),
        ):
            result = await invite_user_with_email(body, user_context)
            assert result == user_id

    @pytest.mark.asyncio
    async def test_invite_user_with_email_already_exists(self):
        """Test user invitation when user already exists."""
        body = CreateUserRequest(
            email="test@example.com",
            full_name="Test User",
            phone="1234567890",
            timezone="UTC",
            role_id=str(uuid.uuid4()),
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member",
        )

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=Exception("user already exists")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await invite_user_with_email(body, user_context)

            assert exc_info.value.status_code == 409
            assert "User with this email already exists" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invite_user_with_email_general_error(self):
        """Test user invitation with general error."""
        body = CreateUserRequest(
            email="test@example.com",
            full_name="Test User",
            phone="1234567890",
            timezone="UTC",
            role_id=str(uuid.uuid4()),
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member",
        )

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=Exception("General error")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await invite_user_with_email(body, user_context)

            assert exc_info.value.status_code == 409
            assert "General error" in exc_info.value.detail


class TestResetThePasswordEmail:
    """Test cases for reset_the_password_email function."""

    @pytest.mark.asyncio
    async def test_reset_the_password_email_success(self):
        """Test successful password reset email."""
        email = "test@example.com"
        mock_response = {"success": True}

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_fresh_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(reset_password_email=AsyncMock(return_value=mock_response))
                )
            ),
        ):
            result = await reset_the_password_email(email)
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_reset_the_password_email_attribute_error(self):
        """Test password reset email with AttributeError."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_fresh_supabase_admin_client"
            ),
            AsyncMock(side_effect=AttributeError("No attribute")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 500
            assert "Internal error while sending password reset email" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reset_the_password_email_value_error(self):
        """Test password reset email with ValueError."""
        email = "invalid-email"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_fresh_supabase_admin_client"
            ),
            AsyncMock(side_effect=ValueError("Invalid email")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 400
            assert "Invalid email address provided" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reset_the_password_email_auth_api_error(self):
        """Test password reset email with AuthApiError."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_fresh_supabase_admin_client"
            ),
            AsyncMock(side_effect=AuthApiError("Auth service error", 502, "AUTH_SERVICE_ERROR")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 502
            assert (
                "Failed to send password reset email due to authentication service error"
                in exc_info.value.detail
            )

    @pytest.mark.asyncio
    async def test_reset_the_password_email_general_exception(self):
        """Test password reset email with general exception."""
        email = "test@example.com"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_fresh_supabase_admin_client"
            ),
            AsyncMock(side_effect=Exception("Unexpected error")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 500
            assert (
                "Unexpected error occurred while sending password reset email"
                in exc_info.value.detail
            )


class TestUpdatePasswordWithToken:
    """Test cases for update_password_with_token function."""

    @pytest.mark.asyncio
    async def test_update_password_with_token_success(self):
        """Test successful password update with token."""
        token = "token123"
        new_password = "newpassword123"
        mock_response = {"success": True}

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(
                        admin=MagicMock(update_user_by_id=AsyncMock(return_value=mock_response))
                    )
                )
            ),
        ):
            result = await update_password_with_token(token, new_password)
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_update_password_with_token_exception(self):
        """Test password update with token when exception occurs."""
        token = "token123"
        new_password = "newpassword123"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(side_effect=Exception("Update failed")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await update_password_with_token(token, new_password)

            assert exc_info.value.status_code == 500
            assert (
                "Unexpected error occurred while updating password with token"
                in exc_info.value.detail
            )


class TestRefreshSession:
    """Test cases for refresh_session function."""

    @pytest.mark.asyncio
    async def test_refresh_session_success(self):
        """Test successful session refresh - covers refresh_session function."""
        refresh_token = "refresh_token_123"
        mock_response = {
            "session": {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
            },
            "user": {"id": "user123"},
        }

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(refresh_session=AsyncMock(return_value=mock_response))
                )
            ),
        ):
            result = await refresh_session(refresh_token)
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_refresh_session_auth_api_error(self):
        """Test refresh session with AuthApiError - covers lines 454-458."""
        refresh_token = "invalid_refresh_token"

        auth_error = AuthApiError("Invalid refresh token", 400, "INVALID_REFRESH_TOKEN")
        auth_error.status = 400
        auth_error.message = "Invalid refresh token"

        with patch(
            (
                "libs.shared_db.supabase_db.admin_operations."
                "user_utility_admin.get_supabase_admin_client"
            ),
            AsyncMock(
                return_value=MagicMock(
                    auth=MagicMock(refresh_session=AsyncMock(side_effect=auth_error))
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await refresh_session(refresh_token)

            assert exc_info.value.status_code == 400
            assert "Invalid request" in exc_info.value.detail
            assert "Invalid refresh token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_refresh_session_general_exception(self):
        """Test refresh session with general exception - covers lines 459-464."""
        refresh_token = "refresh_token_123"

        with (
            patch(
                (
                    "libs.shared_db.supabase_db.admin_operations."
                    "user_utility_admin.get_supabase_admin_client"
                ),
                AsyncMock(
                    return_value=MagicMock(
                        auth=MagicMock(
                            refresh_session=AsyncMock(
                                side_effect=Exception("Database connection error")
                            )
                        )
                    )
                ),
            ),
            patch(
                "libs.shared_db.supabase_db.admin_operations.user_utility_admin.logger"
            ) as mock_logger,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await refresh_session(refresh_token)

            assert exc_info.value.status_code == 500
            assert "Unexpected error occurred while refreshing session" in exc_info.value.detail
            # Verify error was logged
            mock_logger.error.assert_called_once()
            assert "refreshing session" in str(mock_logger.error.call_args).lower()


class TestLogException:
    """Test cases for log_exception function."""

    def test_log_exception(self):
        """Test log_exception function."""
        with (
            patch("libs.shared_utils.common_query.sys.exc_info") as mock_exc_info,
            patch("libs.shared_utils.common_query.logger") as mock_logger,
        ):
            # Create mock traceback
            mock_tb = MagicMock()
            mock_tb.tb_frame.f_code.co_filename = "/test/path/file.py"
            mock_tb.tb_lineno = 42
            mock_exc_info.return_value = (ValueError, ValueError("test error"), mock_tb)

            from libs.shared_utils.common_query import log_exception

            log_exception()

            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0]
            assert "Error:" in call_args[0]
            assert "File:" in call_args[0]
            assert "Line:" in call_args[0]


class TestUserAdminOperations:
    """Test cases for user admin operations."""

    @pytest.mark.asyncio
    async def test_ban_the_user_success(self):
        """Test successful user ban."""
        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user = {"id": user_id}

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_supabase

            result = await ban_the_user(user_id)
            assert result is True
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, {"ban_duration": "365d"}
            )

    @pytest.mark.asyncio
    async def test_ban_the_user_api_error(self):
        """Test user ban with API error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=APIError({"message": "API Error", "code": "23505"})
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_ban_the_user_network_error(self):
        """Test user ban with network error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=HTTPError("Network Error")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_ban_the_user_validation_error(self):
        """Test user ban with validation error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=ValueError("Invalid user ID")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_success(self):
        """Test successful user unban."""
        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user = {"id": user_id}

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_supabase

            result = await unban_the_user(user_id)
            assert result is True
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, {"ban_duration": "none"}
            )

    @pytest.mark.asyncio
    async def test_unban_the_user_api_error(self):
        """Test user unban with API error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=APIError({"message": "API Error", "code": "23505"})
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_network_error(self):
        """Test user unban with network error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=HTTPError("Network Error")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_validation_error(self):
        """Test user unban with validation error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=ValueError("Invalid user ID")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_success(self):
        """Test successful auth user deletion."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(return_value=MagicMock())
            mock_get_client.return_value = mock_supabase

            result = await delete_auth_user(user_id)
            assert result is True
            mock_supabase.auth.admin.delete_user.assert_called_once_with(id=user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_api_error(self):
        """Test auth user deletion with API error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(
                side_effect=AuthApiError("User not found", status=404, code="user_not_found")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(AuthApiError):
                await delete_auth_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_network_error(self):
        """Test auth user deletion with network error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(side_effect=HTTPError("Network Error"))
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await delete_auth_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_validation_error(self):
        """Test auth user deletion with validation error."""
        user_id = str(uuid.uuid4())

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(
                side_effect=ValueError("Invalid user ID")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await delete_auth_user(user_id)

    @pytest.mark.asyncio
    async def test_update_email_of_user_success(self):
        """Test successful user email update."""
        user_id = str(uuid.uuid4())
        new_email = "new@example.com"
        mock_response = MagicMock()
        mock_response.user = {"id": user_id}

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_supabase

            result = await update_email_of_user(user_id, new_email)
            assert result is True
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, {"email": new_email}
            )

    @pytest.mark.asyncio
    async def test_update_email_of_user_api_error(self):
        """Test user email update with API error."""
        user_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=APIError({"message": "API Error", "code": "23505"})
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await update_email_of_user(user_id, new_email)

    @pytest.mark.asyncio
    async def test_update_email_of_user_network_error(self):
        """Test user email update with network error."""
        user_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=HTTPError("Network Error")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await update_email_of_user(user_id, new_email)

    @pytest.mark.asyncio
    async def test_update_email_of_user_validation_error(self):
        """Test user email update with validation error."""
        user_id = str(uuid.uuid4())
        new_email = "invalid-email"

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(
                side_effect=ValueError("Invalid email")
            )
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await update_email_of_user(user_id, new_email)


class TestEmailUtils:
    """Test cases for email_utils.py module."""

    def test_send_email_success(self):
        """Test successful email sending."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[1]["json"]["to"] == "test@example.com"
            assert call_args[1]["json"]["subject"] == "Test Subject"
            assert call_args[1]["json"]["message"] == "Test message"

    def test_send_email_with_html(self):
        """Test email sending with HTML content."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            result = send_email(
                "test@example.com",
                "Test Subject",
                "Test message",
                html="<h1>Test HTML</h1>",
            )

            assert result is True
            call_args = mock_post.call_args
            assert call_args[1]["json"]["html"] == "<h1>Test HTML</h1>"

    def test_send_email_failure(self):
        """Test email sending failure."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_post.return_value = mock_response

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is False

    def test_send_email_network_error(self):
        """Test email sending with network error."""
        import httpx

        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_post.side_effect = httpx.HTTPError("Network error")

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is False

    def test_send_email_timeout_error(self):
        """Test email sending with timeout error."""
        import httpx

        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Request timeout")

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is False

    def test_send_email_connection_error(self):
        """Test email sending with connection error."""
        import httpx

        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection failed")

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is False

    def test_send_email_http_error(self):
        """Test email sending with HTTP error."""
        import httpx

        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_post.side_effect = httpx.HTTPError("HTTP error")

            result = send_email("test@example.com", "Test Subject", "Test message")

            assert result is False

    def test_send_email_headers_verification(self):
        """Test that correct headers are sent with email request."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email("test@example.com", "Test Subject", "Test message")

            call_args = mock_post.call_args
            headers = call_args[1]["headers"]

            assert "apikey" in headers
            assert "Authorization" in headers
            assert "Content-Type" in headers
            assert headers["Content-Type"] == "application/json"
            assert "Bearer" in headers["Authorization"]

    def test_send_email_url_verification(self):
        """Test that correct URL is used for email sending."""
        from libs.shared_utils.email_utils import SUPABASE_URL, send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email("test@example.com", "Test Subject", "Test message")

            call_args = mock_post.call_args
            expected_url = f"{SUPABASE_URL}/functions/v1/custom-email"
            assert call_args[0][0] == expected_url

    def test_send_email_timeout_verification(self):
        """Test that correct timeout is set for email request."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email("test@example.com", "Test Subject", "Test message")

            call_args = mock_post.call_args
            assert call_args[1]["timeout"] == 10

    def test_send_email_payload_structure(self):
        """Test that email payload has correct structure."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email("test@example.com", "Test Subject", "Test message")

            call_args = mock_post.call_args
            payload = call_args[1]["json"]

            assert "to" in payload
            assert "subject" in payload
            assert "message" in payload
            assert payload["to"] == "test@example.com"
            assert payload["subject"] == "Test Subject"
            assert payload["message"] == "Test message"
            assert "html" not in payload

    def test_send_email_payload_with_html(self):
        """Test that email payload includes HTML when provided."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email(
                "test@example.com",
                "Test Subject",
                "Test message",
                html="<p>HTML content</p>",
            )

            call_args = mock_post.call_args
            payload = call_args[1]["json"]

            assert "html" in payload
            assert payload["html"] == "<p>HTML content</p>"

    def test_send_email_with_from_name(self):
        """Test email sending with from_name parameter - covers line 52."""
        from libs.shared_utils.email_utils import send_email

        with patch("libs.shared_utils.email_utils.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            result = send_email(
                "test@example.com",
                "Test Subject",
                "Test message",
                from_name="Test Sender",
            )

            assert result is True
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert "from_name" in payload
            assert payload["from_name"] == "Test Sender"


class TestSendPasswordResetConfirmationEmail:
    """Test cases for send_password_reset_confirmation_email function."""

    def test_send_password_reset_confirmation_email_success(self):
        """Test successful password reset confirmation email - covers lines 195-196."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email

        email = "test@example.com"
        user_name = "Test User"

        with (
            patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_password_reset_confirmation_email(email, user_name)

            assert result is True
            mock_send_email.assert_called_once()

            # Verify the email was sent with correct parameters
            call_args = mock_send_email.call_args
            assert call_args[0][0] == email  # email
            assert call_args[0][1] == "Password Changed Successfully"  # subject
            assert (
                "password for your House of App AI account was successfully updated"
                in call_args[0][2]
            )  # message
            assert "Password Changed" in call_args[0][3]  # html_message

            # Verify success logging - covers line 195
            mock_logger.info.assert_called_with(
                "Password reset confirmation email sent successfully to %s", email
            )

    def test_send_password_reset_confirmation_email_failure(self):
        """Test password reset confirmation email failure - covers line 198."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email

        email = "test@example.com"
        user_name = "Test User"

        with (
            patch(
                "libs.shared_utils.email_utils.send_email", return_value=False
            ) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_password_reset_confirmation_email(email, user_name)

            assert result is False
            mock_send_email.assert_called_once()

            # Verify error logging - covers line 198
            mock_logger.error.assert_called_with(
                "Failed to send password reset confirmation email to %s", email
            )

    def test_send_pwd_reset_conf_email_exception(self):
        """Test password reset confirmation email with exception - covers lines 201-203."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email

        email = "test@example.com"
        user_name = "Test User"

        with (
            patch(
                "libs.shared_utils.email_utils.send_email",
                side_effect=Exception("SMTP Error"),
            ) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_password_reset_confirmation_email(email, user_name)

            assert result is False
            mock_send_email.assert_called_once()

            # Verify exception logging - covers lines 202-203
            mock_logger.error.assert_called_with(
                "Error sending password reset confirmation email: %s", "SMTP Error"
            )

    def test_send_pwd_reset_conf_email_no_user_name(self):
        """Test password reset confirmation email without user name."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email

        email = "test@example.com"

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_password_reset_confirmation_email(email)

            assert result is True
            mock_send_email.assert_called_once()

            # Verify the email content includes "User" as fallback greeting
            call_args = mock_send_email.call_args
            message_content = call_args[0][2]  # message
            html_content = call_args[0][3]  # html_message
            assert "Hello User," in message_content
            assert "Hello User," in html_content

    def test_send_pwd_reset_conf_email_content_validation(self):
        """Test password reset confirmation email content validation."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email

        email = "test@example.com"
        user_name = "John Doe"

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_password_reset_confirmation_email(email, user_name)

            assert result is True

            # Verify email content
            call_args = mock_send_email.call_args
            subject = call_args[0][1]
            message = call_args[0][2]
            html_message = call_args[0][3]

            # Check subject
            assert subject == "Password Changed Successfully"

            # Check plain text message
            assert "password for your House of App AI account was successfully updated" in message
            assert user_name in message

            # Check HTML message
            assert "Password Changed" in html_message
            assert user_name in html_message
            assert "successfully updated" in html_message
            assert "House of App AI Team" in html_message
            assert "2025" in html_message  # current year


class TestSendOrganizationInvitationEmail:
    """Test cases for send_organization_invitation_email function."""

    def test_send_organization_invitation_email_success(self):
        """Test successful organization invitation email sending."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Organization"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "member"
        expires_at = "2024-12-31T23:59:59+00:00"  # ISO format with timezone

        with (
            patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is True
            mock_send_email.assert_called_once()

            # Verify the email was sent with correct parameters
            call_args = mock_send_email.call_args
            assert call_args[0][0] == email  # email
            assert call_args[0][1] == f"You're invited to join {organization_name}"  # subject
            assert organization_name in call_args[0][2]  # message
            assert invite_url in call_args[0][2]  # message contains invite URL
            assert role_name in call_args[0][2]  # message contains role
            assert organization_name in call_args[0][3]  # html_message

            # Verify success logging
            mock_logger.info.assert_called_with(
                "Organization invitation email sent successfully to %s", email
            )

    def test_send_organization_invitation_email_failure(self):
        """Test organization invitation email sending failure."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Organization"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "admin"
        expires_at = "2024-12-31 23:59:59"

        with (
            patch(
                "libs.shared_utils.email_utils.send_email", return_value=False
            ) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is False
            mock_send_email.assert_called_once()

            # Verify error logging
            mock_logger.error.assert_called_with(
                "Failed to send organization invitation email to %s", email
            )

    def test_send_organization_invitation_email_exception(self):
        """Test organization invitation email sending with exception."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Organization"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "owner"
        expires_at = "2024-12-31 23:59:59"

        with (
            patch(
                "libs.shared_utils.email_utils.send_email",
                side_effect=Exception("SMTP Error"),
            ) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is False
            mock_send_email.assert_called_once()

            # Verify exception logging
            mock_logger.error.assert_called_with(
                "Error sending organization invitation email: %s", "SMTP Error"
            )

    def test_send_org_invite_email_content_validation(self):
        """Test organization invitation email content validation."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Acme Corp"
        inviter_name = "Jane Smith"
        invite_url = "https://acme.com/invite/abc123"
        role_name = "manager"
        expires_at = "2024-06-15T18:30:00+00:00"  # ISO format with timezone

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is True

            # Verify email content
            call_args = mock_send_email.call_args
            subject = call_args[0][1]
            message = call_args[0][2]
            html_message = call_args[0][3]

            # Check subject
            assert subject == f"You're invited to join {organization_name}"

            # Check plain text message
            assert organization_name in message
            assert inviter_name in message
            assert role_name in message
            assert invite_url in message
            # Check for formatted date (should contain "June" and "2024")
            assert "June" in message or "2024" in message  # formatted expiration
            assert "You're invited to join" in message
            assert "To accept this invitation" in message
            assert "This invitation will expire" in message

            # Check HTML message
            assert organization_name in html_message
            assert inviter_name in html_message
            assert role_name in html_message
            assert invite_url in html_message
            # Check for formatted date in HTML
            assert "June" in html_message or "2024" in html_message  # formatted expiration
            assert "You're invited to join" in html_message
            assert "Accept Invitation" in html_message
            assert "This invitation will expire" in html_message

    def test_send_org_invite_email_with_timezone(self):
        """Test organization invitation email with non-UTC timezone."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Org"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "member"
        expires_at = "2024-12-31T23:59:59+05:30"  # Non-UTC timezone

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is True
            call_args = mock_send_email.call_args
            # Should contain formatted date
            assert "December" in call_args[0][2] or "2024" in call_args[0][2]

    def test_send_org_invite_email_naive_datetime(self):
        """Test organization invitation email with naive datetime (no timezone)."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Org"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "member"
        expires_at = "2024-12-31T23:59:59"  # Naive datetime (no timezone)

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is True
            call_args = mock_send_email.call_args
            # Should contain formatted date
            assert "December" in call_args[0][2] or "2024" in call_args[0][2]

    def test_send_org_invite_email_invalid_date(self):
        """Test organization invitation email with invalid date format."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Org"
        inviter_name = "John Doe"
        invite_url = "https://example.com/invite/123"
        role_name = "member"
        expires_at = "invalid-date-format"  # Invalid date

        with (
            patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email,
            patch("libs.shared_utils.email_utils.logger") as mock_logger,
        ):
            result = send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            assert result is True
            # Should fallback to original format
            call_args = mock_send_email.call_args
            assert expires_at in call_args[0][2]  # Original invalid date should be in message
            # Should log warning
            mock_logger.warning.assert_called()

    def test_send_org_invite_email_html_structure(self):
        """Test organization invitation email HTML structure."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Org"
        inviter_name = "Test User"
        invite_url = "https://test.com/invite"
        role_name = "member"
        expires_at = "2024-12-31"

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            send_organization_invitation_email(
                email,
                organization_name,
                inviter_name,
                "Invitee Name",
                invite_url,
                role_name,
                expires_at,
            )

            call_args = mock_send_email.call_args
            html_message = call_args[0][3]

            # Check HTML structure elements
            assert "<!DOCTYPE html>" in html_message
            assert "<html>" in html_message
            assert "<head>" in html_message
            assert "<body>" in html_message
            assert 'class="container"' in html_message
            assert 'class="header"' in html_message
            assert 'class="content"' in html_message
            assert 'class="button"' in html_message
            assert 'class="highlight"' in html_message
            assert 'class="footer"' in html_message

    def test_send_org_invite_email_different_roles(self):
        """Test organization invitation email with different role types."""
        from libs.shared_utils.email_utils import send_organization_invitation_email

        email = "test@example.com"
        organization_name = "Test Organization"
        inviter_name = "Admin User"
        invite_url = "https://example.com/invite/123"
        expires_at = "2024-12-31 23:59:59"

        roles = ["owner", "admin", "member", "viewer", "editor"]

        for role_name in roles:
            with patch(
                "libs.shared_utils.email_utils.send_email", return_value=True
            ) as mock_send_email:
                result = send_organization_invitation_email(
                    email,
                    organization_name,
                    inviter_name,
                    "Invitee Name",
                    invite_url,
                    role_name,
                    expires_at,
                )

                assert result is True

                # Verify role is included in email content
                call_args = mock_send_email.call_args
                message = call_args[0][2]
                html_message = call_args[0][3]

                assert role_name in message
                assert role_name in html_message


class TestSendWelcomeEmail:
    """Test cases for send_welcome_email function - covers lines 386-534."""

    def test_send_welcome_email_success(self):
        """Test successful welcome email sending."""
        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_welcome_email(email="test@example.com", first_name="John")

            assert result is True
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args
            assert call_args[0][0] == "test@example.com"
            assert "Welcome to" in call_args[0][1]  # Subject
            assert "John" in call_args[0][2]  # Message
            assert "John" in call_args[0][3]  # HTML message
            assert call_args[1]["from_name"] == "Ross.Ai"  # from_name is a keyword argument

    def test_send_welcome_email_with_custom_parameters(self):
        """Test welcome email with custom parameters."""
        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            result = send_welcome_email(
                email="test@example.com",
                first_name="Jane",
                company_name="Custom Company",
                dashboard_url="https://custom-dashboard.com",
                support_email="custom@support.com",
                company_address="456 Custom St",
                privacy_policy_url="https://custom.com/privacy",
                terms_url="https://custom.com/terms",
            )

            assert result is True
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args
            assert "Custom Company" in call_args[0][1]  # Subject
            assert "Custom Company" in call_args[0][2]  # Message
            assert "https://custom-dashboard.com" in call_args[0][3]  # HTML message
            assert "custom@support.com" in call_args[0][3]  # HTML message

    def test_send_welcome_email_failure(self):
        """Test welcome email sending failure."""
        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=False
        ) as mock_send_email:
            result = send_welcome_email(email="test@example.com", first_name="John")

            assert result is False
            mock_send_email.assert_called_once()

    def test_send_welcome_email_exception(self):
        """Test welcome email sending with exception."""
        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email",
            side_effect=Exception("Email service error"),
        ):
            result = send_welcome_email(email="test@example.com", first_name="John")

            assert result is False

    def test_send_welcome_email_content_validation(self):
        """Test welcome email content validation."""
        from datetime import datetime

        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            send_welcome_email(email="test@example.com", first_name="Alice")

            call_args = mock_send_email.call_args
            message = call_args[0][2]  # Plain text message
            html_message = call_args[0][3]  # HTML message
            current_year = datetime.now().year

            # Check plain text message content
            assert "Alice" in message
            assert "Thank you for signing up" in message
            assert "dashboard" in message.lower()

            # Check HTML message content
            assert "Alice" in html_message
            assert "Welcome to" in html_message
            assert "Go to Dashboard" in html_message
            assert str(current_year) in html_message
            assert "Privacy Policy" in html_message
            assert "Terms of Service" in html_message

    def test_send_welcome_email_html_structure(self):
        """Test welcome email HTML structure."""
        from libs.shared_utils.email_utils import send_welcome_email

        with patch(
            "libs.shared_utils.email_utils.send_email", return_value=True
        ) as mock_send_email:
            send_welcome_email(email="test@example.com", first_name="Bob")

            call_args = mock_send_email.call_args
            html_message = call_args[0][3]

            # Check HTML structure elements
            assert "<!DOCTYPE html>" in html_message
            assert "<html" in html_message
            assert "<head>" in html_message
            assert "<body" in html_message
            assert "<table" in html_message
            assert 'role="presentation"' in html_message
            assert "mobile-responsive" in html_message.lower() or "@media" in html_message


class TestUpdateMetadataOfUser:
    """Test cases for update_metadata_of_user function."""

    @pytest.mark.asyncio
    async def test_update_metadata_of_user_success(self):
        """Test successful metadata update."""
        user_id = str(uuid.uuid4())
        metadata = {
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+1234567890",
            "timezone": "UTC",
        }

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client",
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_result = MagicMock()
            mock_result.user = {"id": user_id, "email": "test@example.com"}
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_metadata_of_user,
            )

            result = await update_metadata_of_user(user_id, metadata)

            assert result is True
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, {"user_metadata": metadata}
            )

    @pytest.mark.asyncio
    async def test_update_metadata_of_user_api_error(self):
        """Test metadata update with API error."""
        user_id = str(uuid.uuid4())
        metadata = {"first_name": "John"}
        api_error = APIError({"message": "Invalid user ID"})

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client",
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(side_effect=api_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_metadata_of_user,
            )

            with pytest.raises(APIError):
                await update_metadata_of_user(user_id, metadata)

    @pytest.mark.asyncio
    async def test_update_metadata_of_user_network_error(self):
        """Test metadata update with network error."""
        user_id = str(uuid.uuid4())
        metadata = {"first_name": "John"}
        network_error = HTTPError("Connection failed")

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client",
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(side_effect=network_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_metadata_of_user,
            )

            with pytest.raises(HTTPError):
                await update_metadata_of_user(user_id, metadata)

    @pytest.mark.asyncio
    async def test_update_metadata_of_user_validation_error(self):
        """Test metadata update with validation error."""
        user_id = str(uuid.uuid4())
        metadata = {"first_name": "John"}
        validation_error = ValueError("Invalid metadata format")

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client",
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = MagicMock(side_effect=validation_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_metadata_of_user,
            )

            with pytest.raises(ValueError):
                await update_metadata_of_user(user_id, metadata)


class TestUpdatePasswordWithLinkIdentity:
    """Test cases for update_password_with_link_identity function."""

    @pytest.mark.asyncio
    async def test_update_pwd_link_existing_email(
        self,
    ):
        """Test password update when user already has email provider."""
        user_id = str(uuid.uuid4())
        password = "new_password123"

        mock_user_data = MagicMock()
        mock_user_data.user.app_metadata = {"providers": ["email", "google"]}
        mock_user_data.user.email = "test@example.com"
        mock_user_data.user.user_metadata = {"first_name": "John"}

        mock_update_result = MagicMock()
        mock_update_result.user = {"id": user_id}

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_update_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            result = await update_password_with_link_identity(user_id, password)

            assert result is True
            mock_supabase.auth.admin.get_user_by_id.assert_called_once_with(user_id)
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, {"password": password}
            )

    @pytest.mark.asyncio
    async def test_update_pwd_link_identity_success_oauth_user(self):
        """Test adding email/password identity to OAuth user."""
        user_id = str(uuid.uuid4())
        password = "new_password123"

        mock_user_data = MagicMock()
        mock_user_data.user.app_metadata = {"providers": ["google"]}
        mock_user_data.user.email = "test@example.com"
        mock_user_data.user.user_metadata = {"first_name": "John"}

        mock_update_result = MagicMock()
        mock_update_result.user = {"id": user_id}

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_update_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            result = await update_password_with_link_identity(user_id, password)

            assert result is True
            mock_supabase.auth.admin.get_user_by_id.assert_called_once_with(user_id)

            # Verify the call includes both password and updated providers
            expected_call_args = {
                "password": password,
                "app_metadata": {"providers": ["google", "email"]},
                "user_metadata": {"first_name": "John"},
            }
            mock_supabase.auth.admin.update_user_by_id.assert_called_once_with(
                user_id, expected_call_args
            )

    @pytest.mark.asyncio
    async def test_update_pwd_link_identity_auth_api_error(self):
        """Test password update with AuthApiError."""
        user_id = str(uuid.uuid4())
        password = "new_password123"
        auth_error = AuthApiError("Invalid user", status=400, code="invalid_user")

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=auth_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            with pytest.raises(AuthApiError):
                await update_password_with_link_identity(user_id, password)

    @pytest.mark.asyncio
    async def test_update_password_with_link_identity_api_error(self):
        """Test password update with APIError."""
        user_id = str(uuid.uuid4())
        password = "new_password123"
        api_error = APIError({"message": "Invalid password"})

        mock_user_data = MagicMock()
        mock_user_data.user.app_metadata = {"providers": ["email"]}
        mock_user_data.user.email = "test@example.com"
        mock_user_data.user.user_metadata = {}

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(side_effect=api_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            with pytest.raises(APIError):
                await update_password_with_link_identity(user_id, password)

    @pytest.mark.asyncio
    async def test_update_pwd_link_identity_network_error(self):
        """Test password update with network error."""
        user_id = str(uuid.uuid4())
        password = "new_password123"
        network_error = HTTPError("Connection failed")

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=network_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            with pytest.raises(HTTPError):
                await update_password_with_link_identity(user_id, password)

    @pytest.mark.asyncio
    async def test_update_pwd_link_identity_validation_error(self):
        """Test password update with validation error."""
        user_id = str(uuid.uuid4())
        password = "new_password123"
        validation_error = ValueError("Invalid user data")

        mock_user_data = MagicMock()
        mock_user_data.user.app_metadata = {"providers": ["email"]}
        mock_user_data.user.email = "test@example.com"
        mock_user_data.user.user_metadata = {}

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(side_effect=validation_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            with pytest.raises(ValueError):
                await update_password_with_link_identity(user_id, password)


class TestGetUserById:
    """Test cases for get_user_by_id function."""

    @pytest.mark.asyncio
    async def test_get_user_by_id_success(self):
        """Test successful user retrieval."""
        user_id = str(uuid.uuid4())
        mock_user_data = {
            "id": user_id,
            "email": "test@example.com",
            "user_metadata": {"first_name": "John"},
            "app_metadata": {"providers": ["email"]},
        }

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

            result = await get_user_by_id(user_id)

            assert result == mock_user_data
            mock_supabase.auth.admin.get_user_by_id.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_get_user_by_id_auth_api_error(self):
        """Test user retrieval with AuthApiError."""
        user_id = str(uuid.uuid4())
        auth_error = AuthApiError("User not found", status=404, code="user_not_found")

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=auth_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

            with pytest.raises(AuthApiError):
                await get_user_by_id(user_id)

    @pytest.mark.asyncio
    async def test_get_user_by_id_network_error(self):
        """Test user retrieval with network error."""
        user_id = str(uuid.uuid4())
        network_error = HTTPError("Connection failed")

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=network_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

            with pytest.raises(HTTPError):
                await get_user_by_id(user_id)

    @pytest.mark.asyncio
    async def test_get_user_by_id_validation_error(self):
        """Test user retrieval with validation error."""
        user_id = str(uuid.uuid4())
        validation_error = ValueError("Invalid user ID format")

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=validation_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

            with pytest.raises(ValueError):
                await get_user_by_id(user_id)

    @pytest.mark.asyncio
    async def test_get_user_by_id_auth_api_error_retry_success(self):
        """Test get_user_by_id with AuthApiError that retries and succeeds."""
        user_id = str(uuid.uuid4())
        mock_user_data = MagicMock()
        mock_user_data.user = {"id": user_id}

        # First call fails with "User not allowed", second succeeds
        auth_error = AuthApiError("User not allowed", status=403, code="user_not_allowed")

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ) as mock_get_client,
            patch("asyncio.sleep", AsyncMock()),
        ):  # Mock sleep to speed up test
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(
                side_effect=[auth_error, mock_user_data]
            )
            mock_get_client.return_value = mock_supabase

            # Import cache to patch it
            from libs.shared_db.supabase_db.db import _cache

            original_cache_value = _cache._supabase_admin_client

            try:
                from libs.shared_db.supabase_db.admin_operations.user import (
                    get_user_by_id,
                )

                result = await get_user_by_id(user_id)

                assert result == mock_user_data
                assert mock_supabase.auth.admin.get_user_by_id.call_count == 2
                # Verify cache was cleared
                assert _cache._supabase_admin_client is None
            finally:
                # Restore original cache value
                _cache._supabase_admin_client = original_cache_value

    @pytest.mark.asyncio
    async def test_get_user_by_id_auth_api_error_retry_exhausted(self):
        """Test get_user_by_id with AuthApiError that exhausts retries"""
        user_id = str(uuid.uuid4())

        # Both attempts fail with "User not allowed"
        auth_error = AuthApiError("User not allowed", status=403, code="user_not_allowed")

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ) as mock_get_client,
            patch("asyncio.sleep", AsyncMock()),
        ):  # Mock sleep to speed up test
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(side_effect=auth_error)
            mock_get_client.return_value = mock_supabase

            # Import cache to patch it
            from libs.shared_db.supabase_db.db import _cache

            original_cache_value = _cache._supabase_admin_client

            try:
                from libs.shared_db.supabase_db.admin_operations.user import (
                    get_user_by_id,
                )

                with pytest.raises(AuthApiError):
                    await get_user_by_id(user_id)

                # Should retry once (max_retries = 2, so 2 attempts total)
                assert mock_supabase.auth.admin.get_user_by_id.call_count == 2
            finally:
                # Restore original cache value
                _cache._supabase_admin_client = original_cache_value


class TestUpdatePhoneOfUser:
    """Test cases for update_phone_of_user function."""

    @pytest.mark.asyncio
    async def test_update_phone_user_success_existing_metadata(self):
        """Test successful phone update with existing metadata."""
        user_id = str(uuid.uuid4())
        phone = "+1234567890"

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {"first_name": "John", "last_name": "Doe"}

        mock_result = MagicMock()
        mock_result.user = {"id": user_id}

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ) as mock_get_client,
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
        ):
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_phone_of_user,
            )

            result = await update_phone_of_user(user_id, phone)

            assert result is True
            # Verify metadata was preserved and phone was added
            call_args = mock_supabase.auth.admin.update_user_by_id.call_args[0]
            assert call_args[0] == user_id
            assert call_args[1]["user_metadata"]["phone"] == phone
            assert call_args[1]["user_metadata"]["first_name"] == "John"

    @pytest.mark.asyncio
    async def test_update_phone_user_success_no_metadata(self):
        """Test successful phone update with no existing metadata."""
        user_id = str(uuid.uuid4())
        phone = "+1234567890"

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = None  # No existing metadata

        mock_result = MagicMock()
        mock_result.user = {"id": user_id}

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ) as mock_get_client,
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
        ):
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_phone_of_user,
            )

            result = await update_phone_of_user(user_id, phone)

            assert result is True
            # Verify phone was added to empty metadata
            call_args = mock_supabase.auth.admin.update_user_by_id.call_args[0]
            assert call_args[0] == user_id
            assert call_args[1]["user_metadata"]["phone"] == phone

    @pytest.mark.asyncio
    async def test_update_phone_of_user_success_empty_metadata(self):
        """Test successful phone update with empty metadata dict."""
        user_id = str(uuid.uuid4())
        phone = "+1234567890"

        mock_user_data = MagicMock()
        mock_user_data.user = MagicMock()
        mock_user_data.user.user_metadata = {}  # Empty metadata

        mock_result = MagicMock()
        mock_result.user = {"id": user_id}

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ) as mock_get_client,
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.get_user_by_id",
                AsyncMock(return_value=mock_user_data),
            ),
        ):
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(return_value=mock_result)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_phone_of_user,
            )

            result = await update_phone_of_user(user_id, phone)

            assert result is True
            call_args = mock_supabase.auth.admin.update_user_by_id.call_args[0]
            assert call_args[1]["user_metadata"]["phone"] == phone

    @pytest.mark.asyncio
    async def test_update_phone_of_user_user_not_found(self):
        """Test phone update when user not found."""
        user_id = str(uuid.uuid4())
        phone = "+1234567890"

        with (
            patch(
                ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
                AsyncMock(),
            ),
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.get_user_by_id",
                AsyncMock(return_value=None),
            ),
        ):
            from libs.shared_db.supabase_db.admin_operations.user import (
                update_phone_of_user,
            )

            with pytest.raises(ValueError, match="User not found"):
                await update_phone_of_user(user_id, phone)


class TestUpdatePasswordWithLinkIdentityErrorHandling:
    """Test cases for error handling in update_password_with_link_identity function."""

    @pytest.mark.asyncio
    async def test_update_pwd_link_identity_user_not_allowed(self):
        """Test password update with 'user not allowed' AuthApiError - covers lines 199-203."""
        user_id = str(uuid.uuid4())
        password = "new_password123"

        mock_user_data = MagicMock()
        mock_user_data.user.app_metadata = {"providers": ["email"]}
        mock_user_data.user.email = "test@example.com"
        mock_user_data.user.user_metadata = {}

        auth_error = AuthApiError(
            "User not allowed to change password", status=403, code="user_not_allowed"
        )

        with patch(
            "libs.shared_db.supabase_db.admin_operations.user.get_fresh_supabase_admin_client"
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.get_user_by_id = AsyncMock(return_value=mock_user_data)
            mock_supabase.auth.admin.update_user_by_id = AsyncMock(side_effect=auth_error)
            mock_get_client.return_value = mock_supabase

            from libs.shared_db.supabase_db.admin_operations.user import (
                update_password_with_link_identity,
            )

            with pytest.raises(HTTPException) as exc_info:
                await update_password_with_link_identity(user_id, password)

            assert exc_info.value.status_code == 403
            assert "User account is restricted" in exc_info.value.detail


class TestDeleteAuthUserErrorHandling:
    """Test cases for additional error handling paths in delete_auth_user function."""

    @pytest.mark.asyncio
    async def test_delete_auth_user_http_status_error(self):
        """Test delete_auth_user with HTTPStatusError."""
        user_id = str(uuid.uuid4())
        http_status_error = HTTPStatusError("User not found", request=None, response=MagicMock())
        http_status_error.response.status_code = 404

        with patch(
            ("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"),
            AsyncMock(),
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(side_effect=http_status_error)
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPException) as exc_info:
                await delete_auth_user(user_id)

            assert exc_info.value.status_code == 404
            assert f"No user found with ID {user_id}" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_delete_auth_user_auth_api_error(self):
        """Test delete_auth_user with AuthApiError."""
        user_id = str(uuid.uuid4())
        auth_error = AuthApiError("User not allowed", status=403, code="user_not_allowed")

        with (
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client"
            ) as mock_get_client,
            patch(
                "libs.shared_db.supabase_db.admin_operations.user.log_exception"
            ) as mock_log_exception,
        ):
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user = AsyncMock(side_effect=auth_error)
            mock_get_client.return_value = mock_supabase

            with pytest.raises(AuthApiError):
                await delete_auth_user(user_id)

            mock_log_exception.assert_called_once()
