# pylint: disable=all

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from supabase_auth.errors import AuthApiError
from postgrest import APIError
from httpx import HTTPError

from apps.user_service.app.schemas.users import CreateUserRequest
from apps.user_service.app.dependencies.common_utils import UserContext
from libs.shared_db.supabase_db.admin_operations.user import (
    ban_the_user,
    unban_the_user,
    delete_auth_user,
    update_email_of_user
)
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    update_supabase_user_email,
    generate_magic_link,
    create_admin_update_email_content,
    send_admin_update_email,
    create_supabase_user,
    sign_up_supabase_user,
    login_user,
    invite_user_with_email,
    reset_the_password_email,
    update_password_with_token,
    log_exception
)

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
            "email": "old@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
                   AsyncMock(return_value=mock_user_info)), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.update_email_of_user",
                   AsyncMock(return_value={"success": True})), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.update_user_email",
                   AsyncMock(return_value=True)), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.send_admin_update_email",
                   return_value=True):

            # Should not raise any exception
            await update_supabase_user_email(user_id, org_id, new_email)

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_user_not_found(self):
        """Test email update when user not found."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
                   AsyncMock(return_value=None)):

            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 404
            assert "User not found in organization" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_supabase_user_email_supabase_failure(self):
        """Test email update when Supabase update fails."""
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        new_email = "new@example.com"

        mock_user_info = {
            "user_id": user_id,
            "full_name": "Test User",
            "email": "old@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
                   AsyncMock(return_value=mock_user_info)), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.update_email_of_user",
                   AsyncMock(return_value=None)):

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
            "email": "old@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
                   AsyncMock(return_value=mock_user_info)), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.update_email_of_user",
                   AsyncMock(return_value={"success": True})), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.update_user_email",
                   AsyncMock(return_value=None)):

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

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_user_profile_by_id",
                   side_effect=Exception("Database error")):

            with pytest.raises(HTTPException) as exc_info:
                await update_supabase_user_email(user_id, org_id, new_email)

            assert exc_info.value.status_code == 500
            assert "Internal server error" in exc_info.value.detail


class TestGenerateMagicLink:
    """Test cases for generate_magic_link function."""

    @pytest.mark.asyncio
    async def test_generate_magic_link_success(self):
        """Test successful magic link generation."""
        email = "test@example.com"
        magic_link_url = "https://example.com/magic-link"

        mock_response = MagicMock()
        mock_response.properties.action_link = magic_link_url

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(generate_link=MagicMock(return_value=mock_response)))))):

            result = await generate_magic_link(email)
            assert result == magic_link_url

    @pytest.mark.asyncio
    async def test_generate_magic_link_no_properties(self):
        """Test magic link generation when response has no properties."""
        email = "test@example.com"

        mock_response = MagicMock()
        mock_response.properties = None

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(generate_link=MagicMock(return_value=mock_response)))))):

            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_no_action_link(self):
        """Test magic link generation when action_link is None."""
        email = "test@example.com"

        mock_response = MagicMock()
        mock_response.properties.action_link = None

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(generate_link=MagicMock(return_value=mock_response)))))):

            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_value_error(self):
        """Test magic link generation with ValueError."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=ValueError("Invalid email"))):

            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_attribute_error(self):
        """Test magic link generation with AttributeError."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=AttributeError("No attribute"))):

            result = await generate_magic_link(email)
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_magic_link_general_exception(self):
        """Test magic link generation with general exception."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Unexpected error"))):

            with pytest.raises(HTTPException) as exc_info:
                await generate_magic_link(email)

            assert exc_info.value.status_code == 500
            assert "Failed to generate magic link" in exc_info.value.detail

class TestCreateAdminUpdateEmailContent:
    """Test cases for create_admin_update_email_content function."""

    def test_create_admin_update_email_content_success(self):
        """Test successful email content creation."""
        user = {
            "full_name": "John Doe",
            "email": "john@example.com"
        }
        magic_link = "https://example.com/magic-link"

        subject, html_message = create_admin_update_email_content(user, magic_link)

        assert subject == "Your Email Has Been Updated - XQtiv"
        assert "John Doe" in html_message
        assert magic_link in html_message
        assert "Magic Link" in html_message

    def test_create_admin_update_email_content_empty_name(self):
        """Test email content creation with empty full name."""
        user = {
            "full_name": "",
            "email": "john@example.com"
        }
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
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(return_value="https://example.com/magic-link")), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.send_email",
                   return_value=True):

            result = await send_admin_update_email(user)
            assert result is True

    @pytest.mark.asyncio
    async def test_send_admin_update_email_no_magic_link(self):
        """Test email sending when magic link generation fails."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(return_value=None)):

            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_send_failure(self):
        """Test email sending when send_email fails."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(return_value="https://example.com/magic-link")), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.send_email",
                   return_value=False):

            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_value_error(self):
        """Test email sending with ValueError."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(side_effect=ValueError("Invalid email"))):

            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_attribute_error(self):
        """Test email sending with AttributeError."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(side_effect=AttributeError("No attribute"))):

            result = await send_admin_update_email(user)
            assert result is False

    @pytest.mark.asyncio
    async def test_send_admin_update_email_general_exception(self):
        """Test email sending with general exception."""
        user = {
            "id": str(uuid.uuid4()),
            "full_name": "John Doe",
            "email": "john@example.com"
        }

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.generate_magic_link",
                   AsyncMock(side_effect=Exception("Unexpected error"))):

            with pytest.raises(HTTPException) as exc_info:
                await send_admin_update_email(user)

            assert exc_info.value.status_code == 500
            assert "Failed to send admin update email" in exc_info.value.detail

class TestCreateSupabaseUser:
    """Test cases for create_supabase_user function."""

    @pytest.mark.asyncio
    async def test_create_supabase_user_success(self):
        """Test successful user creation."""
        # Create a mock body object with all required fields
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.full_name = "Test User"
        body.phone = "1234567890"
        body.timezone = "UTC"
        body.role_id = str(uuid.uuid4())

        organization_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_response = MagicMock()
        mock_response.user.id = user_id

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(create_user=MagicMock(return_value=mock_response)))))):

            result = await create_supabase_user(body, organization_id)
            assert result == user_id

    @pytest.mark.asyncio
    async def test_create_supabase_user_duplicate_email(self):
        """Test user creation with duplicate email."""
        # Create a mock body object with all required fields
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.full_name = "Test User"
        body.phone = "1234567890"
        body.timezone = "UTC"
        body.role_id = str(uuid.uuid4())

        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=ConnectionError("already_exists"))):

            with pytest.raises(HTTPException) as exc_info:
                await create_supabase_user(body, organization_id)

            assert exc_info.value.status_code == 409
            assert "Email already exists" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_supabase_user_connection_error(self):
        """Test user creation with connection error."""
        # Create a mock body object with all required fields
        body = MagicMock()
        body.email = "test@example.com"
        body.password = "password123"
        body.full_name = "Test User"
        body.phone = "1234567890"
        body.timezone = "UTC"
        body.role_id = str(uuid.uuid4())

        organization_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=ConnectionError("Connection failed"))):

            with pytest.raises(HTTPException) as exc_info:
                await create_supabase_user(body, organization_id)

            assert exc_info.value.status_code == 500
            assert "Failed to create user account" in exc_info.value.detail

class TestSignUpSupabaseUser:
    """Test cases for sign_up_supabase_user function."""

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_success(self):
        """Test successful user signup."""
        from apps.user_service.app.schemas.auth import UserSignupData

        user_data = UserSignupData(
            email="test@example.com",
            password="password123"
        )
        body = MagicMock()
        body.user_data = user_data

        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user.id = user_id

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(sign_up=AsyncMock(return_value=mock_response))))):

            result = await sign_up_supabase_user(body)
            assert result == user_id

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_no_user_response(self):
        """Test user signup when response has no user."""
        from apps.user_service.app.schemas.auth import UserSignupData

        user_data = UserSignupData(
            email="test@example.com",
            password="password123"
        )
        body = MagicMock()
        body.user_data = user_data

        mock_response = MagicMock()
        mock_response.user = None

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(sign_up=AsyncMock(return_value=mock_response))))):

            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            assert exc_info.value.status_code == 400
            assert "Failed to create user account" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_duplicate_email(self):
        """Test user signup with duplicate email."""
        from apps.user_service.app.schemas.auth import UserSignupData

        user_data = UserSignupData(
            email="test@example.com",
            password="password123"
        )
        body = MagicMock()
        body.user_data = user_data

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=AuthApiError("already registered", 409, "USER_ALREADY_EXISTS"))):

            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            assert exc_info.value.status_code == 409
            assert "Email already registered" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_sign_up_supabase_user_connection_error(self):
        """Test user signup with connection error."""
        from apps.user_service.app.schemas.auth import UserSignupData

        user_data = UserSignupData(
            email="test@example.com",
            password="password123"
        )
        body = MagicMock()
        body.user_data = user_data

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=ConnectionError("Connection failed"))):

            with pytest.raises(HTTPException) as exc_info:
                await sign_up_supabase_user(body)

            assert exc_info.value.status_code == 500
            assert "Failed to create user account" in exc_info.value.detail


class TestLoginUser:
    """Test cases for login_user function."""

    @pytest.mark.asyncio
    async def test_login_user_success(self):
        """Test successful user login."""
        email = "test@example.com"
        password = "password123"

        mock_result = {"access_token": "token123", "user": {"id": "user123"}}

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(sign_in_with_password=AsyncMock(return_value=mock_result))))), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.log_exception"):

            result = await login_user(email, password)
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_login_user_failure(self):
        """Test user login failure."""
        email = "test@example.com"
        password = "wrongpassword"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(sign_in_with_password=AsyncMock(side_effect=Exception("Invalid credentials")))))), \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.log_exception"):

            with pytest.raises(Exception) as exc_info:
                await login_user(email, password)

            assert "Invalid credentials" in str(exc_info.value)

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
            role_id=str(uuid.uuid4())
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member"
        )
        user_id = str(uuid.uuid4())

        mock_response = MagicMock()
        mock_response.user.id = user_id

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(invite_user_by_email=AsyncMock(return_value=mock_response)))))):

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
            role_id=str(uuid.uuid4())
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member"
        )

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("user already exists"))):

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
            role_id=str(uuid.uuid4())
        )
        user_context = UserContext(
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            email="admin@example.com",
            user_type="organization_member"
        )

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("General error"))):

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

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(reset_password_email=AsyncMock(return_value=mock_response))))):

            result = await reset_the_password_email(email)
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_reset_the_password_email_attribute_error(self):
        """Test password reset email with AttributeError."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=AttributeError("No attribute"))):

            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 500
            assert "Internal error while sending password reset email" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reset_the_password_email_value_error(self):
        """Test password reset email with ValueError."""
        email = "invalid-email"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=ValueError("Invalid email"))):

            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 400
            assert "Invalid email address provided" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reset_the_password_email_auth_api_error(self):
        """Test password reset email with AuthApiError."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=AuthApiError("Auth service error", 502, "AUTH_SERVICE_ERROR"))):

            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 502
            assert "Failed to send password reset email due to authentication service error" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_reset_the_password_email_general_exception(self):
        """Test password reset email with general exception."""
        email = "test@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Unexpected error"))):

            with pytest.raises(HTTPException) as exc_info:
                await reset_the_password_email(email)

            assert exc_info.value.status_code == 500
            assert "Unexpected error occurred while sending password reset email" in exc_info.value.detail

class TestUpdatePasswordWithToken:
    """Test cases for update_password_with_token function."""

    @pytest.mark.asyncio
    async def test_update_password_with_token_success(self):
        """Test successful password update with token."""
        token = "token123"
        new_password = "newpassword123"
        mock_response = {"success": True}

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(return_value=MagicMock(auth=MagicMock(admin=MagicMock(update_user_by_id=AsyncMock(return_value=mock_response)))))):

            result = await update_password_with_token(token, new_password)
            assert result == mock_response

    @pytest.mark.asyncio
    async def test_update_password_with_token_exception(self):
        """Test password update with token when exception occurs."""
        token = "token123"
        new_password = "newpassword123"

        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.get_supabase_admin_client",
                   AsyncMock(side_effect=Exception("Update failed"))):

            with pytest.raises(HTTPException) as exc_info:
                await update_password_with_token(token, new_password)

            assert exc_info.value.status_code == 500
            assert "Unexpected error occurred while updating password with token" in exc_info.value.detail

class TestLogException:
    """Test cases for log_exception function."""

    def test_log_exception(self):
        """Test log_exception function."""
        with patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.sys.exc_info") as mock_exc_info, \
             patch("libs.shared_db.supabase_db.admin_operations.user_utility_admin.logger") as mock_logger:

            # Create mock traceback
            mock_tb = MagicMock()
            mock_tb.tb_frame.f_code.co_filename = "/test/path/file.py"
            mock_tb.tb_lineno = 42
            mock_exc_info.return_value = (ValueError, ValueError("test error"), mock_tb)

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

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.return_value = mock_response
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

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = APIError({"message": "API Error", "code": "23505"})
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_ban_the_user_network_error(self):
        """Test user ban with network error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = HTTPError("Network Error")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_ban_the_user_validation_error(self):
        """Test user ban with validation error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = ValueError("Invalid user ID")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await ban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_success(self):
        """Test successful user unban."""
        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user = {"id": user_id}

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.return_value = mock_response
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

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = APIError({"message": "API Error", "code": "23505"})
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_network_error(self):
        """Test user unban with network error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = HTTPError("Network Error")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_unban_the_user_validation_error(self):
        """Test user unban with validation error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = ValueError("Invalid user ID")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await unban_the_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_success(self):
        """Test successful auth user deletion."""
        user_id = str(uuid.uuid4())
        mock_response = MagicMock()
        mock_response.user = {"id": user_id}

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user.return_value = mock_response
            mock_get_client.return_value = mock_supabase

            result = await delete_auth_user(user_id)
            assert result is True
            mock_supabase.auth.admin.delete_user.assert_called_once_with(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_api_error(self):
        """Test auth user deletion with API error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user.side_effect = APIError({"message": "API Error", "code": "23505"})
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await delete_auth_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_network_error(self):
        """Test auth user deletion with network error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user.side_effect = HTTPError("Network Error")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await delete_auth_user(user_id)

    @pytest.mark.asyncio
    async def test_delete_auth_user_validation_error(self):
        """Test auth user deletion with validation error."""
        user_id = str(uuid.uuid4())

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.delete_user.side_effect = ValueError("Invalid user ID")
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

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.return_value = mock_response
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

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = APIError({"message": "API Error", "code": "23505"})
            mock_get_client.return_value = mock_supabase

            with pytest.raises(APIError):
                await update_email_of_user(user_id, new_email)

    @pytest.mark.asyncio
    async def test_update_email_of_user_network_error(self):
        """Test user email update with network error."""
        user_id = str(uuid.uuid4())
        new_email = "new@example.com"

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = HTTPError("Network Error")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(HTTPError):
                await update_email_of_user(user_id, new_email)

    @pytest.mark.asyncio
    async def test_update_email_of_user_validation_error(self):
        """Test user email update with validation error."""
        user_id = str(uuid.uuid4())
        new_email = "invalid-email"

        with patch("libs.shared_db.supabase_db.admin_operations.user.get_supabase_admin_client") as mock_get_client:
            mock_supabase = MagicMock()
            mock_supabase.auth.admin.update_user_by_id.side_effect = ValueError("Invalid email")
            mock_get_client.return_value = mock_supabase

            with pytest.raises(ValueError):
                await update_email_of_user(user_id, new_email)


class TestEmailUtils:
    """Test cases for email_utils.py module."""

    def test_send_email_success(self):
        """Test successful email sending."""
        from libs.shared_utils.email_utils import send_email
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
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
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            result = send_email(
                "test@example.com", 
                "Test Subject", 
                "Test message", 
                html="<h1>Test HTML</h1>"
            )
            
            assert result is True
            call_args = mock_post.call_args
            assert call_args[1]["json"]["html"] == "<h1>Test HTML</h1>"

    def test_send_email_failure(self):
        """Test email sending failure."""
        from libs.shared_utils.email_utils import send_email
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_post.return_value = mock_response

            result = send_email("test@example.com", "Test Subject", "Test message")
            
            assert result is False

    def test_send_email_network_error(self):
        """Test email sending with network error."""
        from libs.shared_utils.email_utils import send_email
        import requests
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("Network error")

            result = send_email("test@example.com", "Test Subject", "Test message")
            
            assert result is False

    def test_send_email_timeout_error(self):
        """Test email sending with timeout error."""
        from libs.shared_utils.email_utils import send_email
        import requests
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout("Request timeout")

            result = send_email("test@example.com", "Test Subject", "Test message")
            
            assert result is False

    def test_send_email_connection_error(self):
        """Test email sending with connection error."""
        from libs.shared_utils.email_utils import send_email
        import requests
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("Connection failed")

            result = send_email("test@example.com", "Test Subject", "Test message")
            
            assert result is False

    def test_send_email_http_error(self):
        """Test email sending with HTTP error."""
        from libs.shared_utils.email_utils import send_email
        import requests
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_post.side_effect = requests.HTTPError("HTTP error")

            result = send_email("test@example.com", "Test Subject", "Test message")
            
            assert result is False

    def test_send_email_headers_verification(self):
        """Test that correct headers are sent with email request."""
        from libs.shared_utils.email_utils import send_email
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
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
        from libs.shared_utils.email_utils import send_email
        from libs.shared_utils.email_utils import SUPABASE_URL
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
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
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
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
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
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
        
        with patch("libs.shared_utils.email_utils.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "Email sent successfully"
            mock_post.return_value = mock_response

            send_email("test@example.com", "Test Subject", "Test message", html="<p>HTML content</p>")
            
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            
            assert "html" in payload
            assert payload["html"] == "<p>HTML content</p>"


class TestSendPasswordResetConfirmationEmail:
    """Test cases for send_password_reset_confirmation_email function."""

    def test_send_password_reset_confirmation_email_success(self):
        """Test successful password reset confirmation email - covers lines 195-196."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email
        
        email = "test@example.com"
        user_name = "Test User"
        
        with patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email, \
             patch("libs.shared_utils.email_utils.logger") as mock_logger:
            
            result = send_password_reset_confirmation_email(email, user_name)
            
            assert result is True
            mock_send_email.assert_called_once()
            
            # Verify the email was sent with correct parameters
            call_args = mock_send_email.call_args
            assert call_args[0][0] == email  # email
            assert call_args[0][1] == "Password Changed Successfully"  # subject
            assert "password for your House of App AI account was successfully updated" in call_args[0][2]  # message
            assert "Password Changed" in call_args[0][3]  # html_message
            
            # Verify success logging - covers line 195
            mock_logger.info.assert_called_with("Password reset confirmation email sent successfully to %s", email)

    def test_send_password_reset_confirmation_email_failure(self):
        """Test password reset confirmation email failure - covers line 198."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email
        
        email = "test@example.com"
        user_name = "Test User"
        
        with patch("libs.shared_utils.email_utils.send_email", return_value=False) as mock_send_email, \
             patch("libs.shared_utils.email_utils.logger") as mock_logger:
            
            result = send_password_reset_confirmation_email(email, user_name)
            
            assert result is False
            mock_send_email.assert_called_once()
            
            # Verify error logging - covers line 198
            mock_logger.error.assert_called_with("Failed to send password reset confirmation email to %s", email)

    def test_send_password_reset_confirmation_email_exception(self):
        """Test password reset confirmation email with exception - covers lines 201-203."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email
        
        email = "test@example.com"
        user_name = "Test User"
        
        with patch("libs.shared_utils.email_utils.send_email", side_effect=Exception("SMTP Error")) as mock_send_email, \
             patch("libs.shared_utils.email_utils.logger") as mock_logger:
            
            result = send_password_reset_confirmation_email(email, user_name)
            
            assert result is False
            mock_send_email.assert_called_once()
            
            # Verify exception logging - covers lines 202-203
            mock_logger.error.assert_called_with("Error sending password reset confirmation email: %s", "SMTP Error")

    def test_send_password_reset_confirmation_email_without_user_name(self):
        """Test password reset confirmation email without user name."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email
        
        email = "test@example.com"
        
        with patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email:
            
            result = send_password_reset_confirmation_email(email)
            
            assert result is True
            mock_send_email.assert_called_once()
            
            # Verify the email content includes "User" as fallback greeting
            call_args = mock_send_email.call_args
            message_content = call_args[0][2]  # message
            html_content = call_args[0][3]  # html_message
            assert "Hello User," in message_content
            assert "Hello User," in html_content

    def test_send_password_reset_confirmation_email_content_validation(self):
        """Test password reset confirmation email content validation."""
        from libs.shared_utils.email_utils import send_password_reset_confirmation_email
        
        email = "test@example.com"
        user_name = "John Doe"
        
        with patch("libs.shared_utils.email_utils.send_email", return_value=True) as mock_send_email:
            
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
