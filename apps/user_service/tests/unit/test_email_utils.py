"""Unit tests for email utility helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from apps.user_service.app.utils import email_utils


def test_send_email_success() -> None:
    """Successful edge function call should return True."""
    response = MagicMock(status_code=200)
    with patch("apps.user_service.app.utils.email_utils.httpx.post", return_value=response):
        ok = email_utils.send_email(
            "user@example.com",
            "Subject",
            "Plain body",
            html="<p>Hi</p>",
            from_name="App",
        )
    assert ok is True


def test_send_email_failure_status() -> None:
    """Non-200 responses should return False."""
    response = MagicMock(status_code=500, text="error")
    with patch("apps.user_service.app.utils.email_utils.httpx.post", return_value=response):
        ok = email_utils.send_email("user@example.com", "Subject", "Body")
    assert ok is False


def test_password_reset_confirmation_email() -> None:
    """Password reset confirmation should send personalized HTML."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_password_reset_confirmation_email("user@example.com", "Jane Doe")
    assert ok is True
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == "user@example.com"


def test_send_email_http_error() -> None:
    """Network failures should return False."""
    with patch(
        "apps.user_service.app.utils.email_utils.httpx.post",
        side_effect=email_utils.httpx.HTTPError("network"),
    ):
        ok = email_utils.send_email("user@example.com", "Subject", "Body")
    assert ok is False


def test_welcome_email_success() -> None:
    """Welcome email delegates to send_email with sender name."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_welcome_email("user@example.com", "Jane")
    assert ok is True
    assert mock_send.call_args[0][0] == "user@example.com"
    assert mock_send.call_args.kwargs.get("from_name") == email_utils.ROSS_AI_FROM_NAME


def test_password_change_success_email() -> None:
    """Password change email uses personalized greeting."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_password_change_success_email("user@example.com", "Jane Doe")
    assert ok is True
    assert "Password Changed Successfully" in mock_send.call_args[0][1]


def test_password_reset_success_email() -> None:
    """Password reset success email sends confirmation copy."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_password_reset_success_email("user@example.com", "Jane")
    assert ok is True
    assert "Password Reset Successful" in mock_send.call_args[0][1]


def test_verification_code_email() -> None:
    """Verification code email includes OTP in body."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_verification_code_email("user@example.com", "123456")
    assert ok is True
    assert "123456" in mock_send.call_args[0][2]


def test_organization_invitation_email() -> None:
    """Organization invitation email formats expiry timestamp."""
    expires = "2026-12-01T10:00:00+00:00"
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_invitation_email(
            email="invitee@example.com",
            organization_name="Acme",
            inviter_name="Admin",
            invitee_name="Invitee",
            invite_url="https://example.com/invite",
            role_name="member",
            expires_at=expires,
        )
    assert ok is True
    assert mock_send.call_args[0][0] == "invitee@example.com"


def test_client_creation_email_with_password() -> None:
    """Client creation email includes credentials when provided."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_client_creation_email(
            email="client@example.com",
            organization_name="Acme",
            password="TempPass1!",
        )
    assert ok is True
    assert "TempPass1!" in mock_send.call_args[0][2]


def test_org_delete_request_email() -> None:
    """Delete request email notifies super admins."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_delete_request_email(
            email="admin@example.com",
            organization_name="Acme",
            requester_email="owner@example.com",
        )
    assert ok is True
    assert "Acme" in mock_send.call_args[0][1]


def test_org_deletion_approved_email() -> None:
    """Deletion approved email confirms permanent removal."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_deletion_approved_email(
            email="member@example.com",
            organization_name="Acme",
        )
    assert ok is True
    assert "Acme" in mock_send.call_args[0][2]


def test_org_deletion_rejected_email() -> None:
    """Deletion rejected email includes rejection reason."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_deletion_rejected_email(
            email="owner@example.com",
            organization_name="Acme",
            rejection_reason="Active subscriptions remain",
        )
    assert ok is True
    assert "Active subscriptions remain" in mock_send.call_args[0][2]


def test_org_member_banned_email() -> None:
    """Banned member email references organization and admin."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_org_member_banned_email(
            email="member@example.com",
            organization_name="Acme",
            banned_by_email="admin@example.com",
        )
    assert ok is True
    assert "Acme" in mock_send.call_args[0][2]


def test_org_member_unbanned_email() -> None:
    """Unbanned member email confirms restored access."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_org_member_unbanned_email(
            email="member@example.com",
            organization_name="Acme",
            unbanned_by_email="admin@example.com",
        )
    assert ok is True
    assert "restored" in mock_send.call_args[0][1].lower()


def test_password_reset_confirmation_email_failure() -> None:
    """Password reset confirmation returns False when send fails."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        ok = email_utils.send_password_reset_confirmation_email("user@example.com", "Jane")
    assert ok is False


def test_password_reset_confirmation_email_exception() -> None:
    """Password reset confirmation catches unexpected errors."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email",
        side_effect=RuntimeError("smtp down"),
    ):
        ok = email_utils.send_password_reset_confirmation_email("user@example.com", "Jane")
    assert ok is False


def test_organization_invitation_email_datetime_object() -> None:
    """Organization invitation accepts datetime expires_at values."""
    expires = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_invitation_email(
            email="invitee@example.com",
            organization_name="Acme",
            inviter_name="Admin",
            invitee_name="Invitee",
            invite_url="https://example.com/invite",
            role_name="member",
            expires_at=expires,
        )
    assert ok is True
    assert "December" in mock_send.call_args[0][2]


def test_organization_invitation_email_invalid_date_fallback() -> None:
    """Organization invitation falls back when expires_at is unparseable."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_organization_invitation_email(
            email="invitee@example.com",
            organization_name="Acme",
            inviter_name="Admin",
            invitee_name="Invitee",
            invite_url="https://example.com/invite",
            role_name="member",
            expires_at="not-a-date",
        )
    assert ok is True
    assert "not-a-date" in mock_send.call_args[0][2]


def test_organization_invitation_email_failure_and_exception() -> None:
    """Organization invitation handles send failure and exceptions."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert (
            email_utils.send_organization_invitation_email(
                email="invitee@example.com",
                organization_name="Acme",
                inviter_name="Admin",
                invitee_name="Invitee",
                invite_url="https://example.com/invite",
                role_name="member",
                expires_at="2026-12-01T10:00:00",
            )
            is False
        )
    with patch(
        "apps.user_service.app.utils.email_utils.send_email",
        side_effect=RuntimeError("fail"),
    ):
        assert (
            email_utils.send_organization_invitation_email(
                email="invitee@example.com",
                organization_name="Acme",
                inviter_name="Admin",
                invitee_name="Invitee",
                invite_url="https://example.com/invite",
                role_name="member",
                expires_at="2026-12-01T10:00:00",
            )
            is False
        )


def test_welcome_email_failure() -> None:
    """Welcome email returns False when send fails."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert email_utils.send_welcome_email("user@example.com", "Jane") is False


def test_password_change_success_email_failure() -> None:
    """Password change email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert email_utils.send_password_change_success_email("user@example.com") is False


def test_password_reset_success_email_failure() -> None:
    """Password reset success email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert email_utils.send_password_reset_success_email("user@example.com") is False


def test_verification_code_email_failure() -> None:
    """Verification code email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert email_utils.send_verification_code_email("user@example.com", "123456") is False


def test_org_delete_request_email_failure() -> None:
    """Delete request email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert (
            email_utils.send_organization_delete_request_email(
                email="admin@example.com",
                organization_name="Acme",
                requester_email="owner@example.com",
            )
            is False
        )


def test_org_deletion_approved_email_failure() -> None:
    """Deletion approved email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert (
            email_utils.send_organization_deletion_approved_email(
                email="member@example.com",
                organization_name="Acme",
            )
            is False
        )


def test_org_deletion_rejected_email_failure() -> None:
    """Deletion rejected email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert (
            email_utils.send_organization_deletion_rejected_email(
                email="owner@example.com",
                organization_name="Acme",
                rejection_reason="Active subscriptions remain",
            )
            is False
        )


def test_client_creation_email_without_password() -> None:
    """Client creation email omits credentials section when password absent."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_client_creation_email(
            email="client@example.com",
            organization_name="Acme",
        )
    assert ok is True
    assert "Password:" not in mock_send.call_args[0][2]


def test_client_creation_email_failure() -> None:
    """Client creation email returns False on send failure."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        assert (
            email_utils.send_client_creation_email(
                email="client@example.com",
                organization_name="Acme",
            )
            is False
        )


def test_org_member_banned_email_exception() -> None:
    """Banned member email catches send exceptions."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email",
        side_effect=RuntimeError("fail"),
    ):
        assert (
            email_utils.send_org_member_banned_email(
                email="member@example.com",
                organization_name="Acme",
                banned_by_email="admin@example.com",
            )
            is False
        )


def test_org_member_unbanned_email_exception() -> None:
    """Unbanned member email catches send exceptions."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email",
        side_effect=RuntimeError("fail"),
    ):
        assert (
            email_utils.send_org_member_unbanned_email(
                email="member@example.com",
                organization_name="Acme",
                unbanned_by_email="admin@example.com",
            )
            is False
        )
