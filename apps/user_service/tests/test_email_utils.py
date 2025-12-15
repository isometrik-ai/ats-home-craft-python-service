"""Unit tests for email utilities."""

from unittest.mock import MagicMock, patch

import httpx

from libs.shared_utils.email_utils import (
    send_email,
    send_organization_invitation_email,
    send_password_change_success_email,
    send_password_reset_confirmation_email,
    send_password_reset_success_email,
    send_verification_code_email,
    send_welcome_email,
)


class TestSendEmail:
    """Test cases for send_email function."""

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_success_without_from_name(self, mock_post):
        """Test send_email successfully without from_name."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = send_email(
            email="test@example.com", subject="Test Subject", message="Test message"
        )

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["to"] == "test@example.com"
        assert call_kwargs["json"]["subject"] == "Test Subject"
        assert call_kwargs["json"]["message"] == "Test message"
        assert "from_name" not in call_kwargs["json"]

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_success_with_from_name(self, mock_post):
        """Test send_email successfully with from_name (covers if from_name branch)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = send_email(
            email="test@example.com",
            subject="Test Subject",
            message="Test message",
            from_name="Ross.Ai",
        )

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["to"] == "test@example.com"
        assert call_kwargs["json"]["subject"] == "Test Subject"
        assert call_kwargs["json"]["message"] == "Test message"
        assert call_kwargs["json"]["from_name"] == "Ross.Ai"

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_success_with_html(self, mock_post):
        """Test send_email successfully with HTML content."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        html_content = "<html><body>Test HTML</body></html>"
        result = send_email(
            email="test@example.com",
            subject="Test Subject",
            message="Test message",
            html=html_content,
        )

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["html"] == html_content

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_success_with_html_and_from_name(self, mock_post):
        """Test send_email successfully with both HTML and from_name."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        html_content = "<html><body>Test HTML</body></html>"
        result = send_email(
            email="test@example.com",
            subject="Test Subject",
            message="Test message",
            html=html_content,
            from_name="Ross.Ai",
        )

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["html"] == html_content
        assert call_kwargs["json"]["from_name"] == "Ross.Ai"

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_failure(self, mock_post):
        """Test send_email when API returns error."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        result = send_email(
            email="test@example.com", subject="Test Subject", message="Test message"
        )

        assert result is False

    @patch("libs.shared_utils.email_utils.httpx.post")
    @patch("libs.shared_utils.email_utils.SUPABASE_URL", "https://test.supabase.co")
    @patch("libs.shared_utils.email_utils.SERVICE_ROLE_KEY", "test-service-key")
    def test_send_email_http_error(self, mock_post):
        """Test send_email when HTTP error occurs."""
        mock_post.side_effect = httpx.HTTPError("Connection error")

        result = send_email(
            email="test@example.com", subject="Test Subject", message="Test message"
        )

        assert result is False


class TestEmailFunctions:
    """Test cases for email helper functions."""

    @patch("libs.shared_utils.email_utils.send_email")
    def test_send_password_reset_confirmation_email(self, mock_send_email):
        """Test send_password_reset_confirmation_email."""
        mock_send_email.return_value = True

        result = send_password_reset_confirmation_email(
            email="test@example.com", user_name="Test User"
        )

        assert result is True
        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args
        assert call_args[0][0] == "test@example.com"
        assert "Password Changed Successfully" in call_args[0][1]

    @patch("libs.shared_utils.email_utils.send_email")
    def test_send_organization_invitation_email(self, mock_send_email):
        """Test send_organization_invitation_email."""
        mock_send_email.return_value = True

        result = send_organization_invitation_email(
            email="test@example.com",
            organization_name="Test Org",
            inviter_name="John Doe",
            invitee_name="Jane Doe",
            invite_url="https://example.com/invite",
            role_name="admin",
            expires_at="2024-12-31",
        )

        assert result is True
        mock_send_email.assert_called_once()

    @patch("libs.shared_utils.email_utils.send_email")
    def test_send_welcome_email(self, mock_send_email):
        """Test send_welcome_email (covers from_name usage)."""
        mock_send_email.return_value = True

        result = send_welcome_email(email="test@example.com", first_name="Test")

        assert result is True
        mock_send_email.assert_called_once()
        call_kwargs = mock_send_email.call_args[1]
        assert call_kwargs["from_name"] == "Ross.Ai"

    @patch("libs.shared_utils.email_utils.send_email")
    def test_send_verification_code_email(self, mock_send_email):
        """Test send_verification_code_email (covers from_name usage)."""
        mock_send_email.return_value = True

        result = send_verification_code_email(email="test@example.com", otp_code="1234")

        assert result is True
        mock_send_email.assert_called_once()
        # Check positional arguments (email, subject, message, html)
        call_args = mock_send_email.call_args[0]
        assert call_args[0] == "test@example.com"
        assert "1234" in call_args[3]  # html is 4th positional argument
        # Check keyword arguments (from_name)
        call_kwargs = mock_send_email.call_args[1]
        assert call_kwargs["from_name"] == "Ross.Ai"

    @patch("libs.shared_utils.email_utils.send_email")
    @patch("libs.shared_utils.email_utils.logger")
    def test_send_password_change_success_email_failure(self, mock_logger, mock_send_email):
        """Test send_password_change_success_email."""
        mock_send_email.return_value = False

        result = send_password_change_success_email(email="test@example.com", user_name="Test User")

        assert result is False
        mock_send_email.assert_called_once()
        mock_logger.error.assert_called_with(
            "Failed to send password change success email to %s", "test@example.com"
        )

    @patch("libs.shared_utils.email_utils.send_email")
    @patch("libs.shared_utils.email_utils.logger")
    def test_send_password_change_success_email_exception(self, mock_logger, mock_send_email):
        """Test send_password_change_success_email when exception occurs."""
        mock_send_email.side_effect = Exception("SMTP Error")

        result = send_password_change_success_email(email="test@example.com", user_name="Test User")

        assert result is False
        mock_logger.error.assert_called_with(
            "Error sending password change success email: %s", "SMTP Error"
        )

    @patch("libs.shared_utils.email_utils.send_email")
    @patch("libs.shared_utils.email_utils.logger")
    def test_send_password_reset_success_email_exception(self, mock_logger, mock_send_email):
        """Test send_password_reset_success_email when exception occurs (covers lines 771-773)."""
        mock_send_email.side_effect = Exception("SMTP Error")

        result = send_password_reset_success_email(email="test@example.com", user_name="Test User")

        assert result is False
        mock_logger.error.assert_called_with(
            "Error sending password reset success email: %s", "SMTP Error"
        )

    @patch("libs.shared_utils.email_utils.send_email")
    @patch("libs.shared_utils.email_utils.logger")
    def test_send_verification_code_email_failure(self, mock_logger, mock_send_email):
        """Test send_verification_code_email when email sending fails (covers lines 882-883)."""
        mock_send_email.return_value = False

        result = send_verification_code_email(email="test@example.com", otp_code="1234")

        assert result is False
        mock_send_email.assert_called_once()
        mock_logger.error.assert_called_with(
            "Failed to send verification code email to %s", "test@example.com"
        )

    @patch("libs.shared_utils.email_utils.send_email")
    @patch("libs.shared_utils.email_utils.logger")
    def test_send_verification_code_email_exception(self, mock_logger, mock_send_email):
        """Test send_verification_code_email when exception occurs (covers lines 885-887)."""
        mock_send_email.side_effect = Exception("SMTP Error")

        result = send_verification_code_email(email="test@example.com", otp_code="1234")

        assert result is False
        mock_logger.error.assert_called_with(
            "Error sending verification code email: %s", "SMTP Error"
        )
