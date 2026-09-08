"""Unit tests for email utility helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def test_unit_assignment_welcome_email_includes_phone_and_store_links() -> None:
    """Unit assignment welcome email includes phone, email, and store URLs."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_unit_assignment_welcome_email(
            email="john@example.com",
            first_name="John",
            organization_name="Green Valley Residency",
            project_name="Sunrise Towers",
            unit_display="Tower A — 1204",
            login_phone="+91 9876543210",
            login_email="john@example.com",
            ios_app_store_url="https://apps.apple.com/app/id123",
            android_play_store_url="https://play.google.com/store/apps/details?id=com.example",
        )
    assert ok is True
    plain_text = mock_send.call_args[0][2]
    html = mock_send.call_args[0][3]
    assert "+91 9876543210" in plain_text
    assert "john@example.com" in plain_text
    assert "Welcome to ATS Home Craft" in plain_text or "Welcome to" in plain_text
    assert "password" not in plain_text.lower()
    assert "https://apps.apple.com/app/id123" in html
    assert "https://play.google.com/store/apps/details?id=com.example" in html


def test_normalize_store_url_rejects_non_http_schemes() -> None:
    """Store URLs must be http(s) to avoid javascript/data links in email hrefs."""
    assert email_utils._normalize_store_url("javascript:alert(1)") is None
    assert email_utils._normalize_store_url("https://apps.apple.com/app/id123") == (
        "https://apps.apple.com/app/id123"
    )


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.send_email", return_value=True)
@patch("apps.user_service.app.utils.email_utils.resolve_unit_assignment_welcome_content")
async def test_send_unit_assignment_welcome_email_for_org_uses_thread_pool(
    mock_resolve,
    mock_send,
) -> None:
    """Async sender offloads blocking httpx email transport to a worker thread."""
    mock_resolve.return_value = ("Subject", "Plain", "<p>Hi</p>")

    ok = await email_utils.send_unit_assignment_welcome_email_for_org(
        db_connection=MagicMock(),
        organization_id="org-1",
        email="john@example.com",
        first_name="John",
        organization_name="Green Valley Residency",
        project_name="Sunrise Towers",
        unit_display="Tower A — 1204",
        login_phone="+91 9876543210",
        login_email="john@example.com",
    )

    assert ok is True
    mock_send.assert_called_once()


def test_unit_assignment_welcome_email_omits_store_buttons_when_urls_missing() -> None:
    """Store download buttons are omitted when store URLs are not configured."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_unit_assignment_welcome_email(
            email="john@example.com",
            first_name="John",
            organization_name="Green Valley Residency",
            project_name="Sunrise Towers",
            unit_display="Tower A — 1204",
            login_phone="+91 9876543210",
            login_email="john@example.com",
        )
    assert ok is True
    html = mock_send.call_args[0][3]
    assert "Download on the App Store" not in html
    assert "Get it on Google Play" not in html


def test_unit_assignment_welcome_email_failure_when_send_returns_false() -> None:
    """Unit assignment welcome email returns False when transport fails."""
    with patch("apps.user_service.app.utils.email_utils.send_email", return_value=False):
        ok = email_utils.send_unit_assignment_welcome_email(
            email="john@example.com",
            first_name="John",
            organization_name="Green Valley Residency",
            project_name="Sunrise Towers",
            unit_display="Tower A — 1204",
            login_phone="+91 9876543210",
            login_email="john@example.com",
        )
    assert ok is False


def test_unit_assignment_welcome_email_failure_on_exception() -> None:
    """Unit assignment welcome email returns False when send_email raises."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email",
        side_effect=RuntimeError("smtp unavailable"),
    ):
        ok = email_utils.send_unit_assignment_welcome_email(
            email="john@example.com",
            first_name="John",
            organization_name="Green Valley Residency",
            project_name="Sunrise Towers",
            unit_display="Tower A — 1204",
            login_phone="+91 9876543210",
            login_email="john@example.com",
        )
    assert ok is False


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_resolve_unit_assignment_welcome_content_uses_db_template(
    mock_repo_cls,
) -> None:
    """Published org DB template overrides subject and HTML."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(
        return_value={
            "subject": "DB — {{ organization_name }}",
            "html_content": "<p>DB hello {{ greeting_name }}</p>",
        }
    )
    context = {
        "greeting_name": "John",
        "organization_name": "Green Valley Residency",
        "project_name": "Sunrise Towers",
        "unit_display": "Tower A — 1204",
        "phone_display": "+91 9876543210",
        "email_display": "john@example.com",
        "has_login_phone": True,
        "ios_app_store_url": None,
        "android_play_store_url": None,
        "app_name": "ATS Home Craft",
        "current_year": 2026,
        "company_name": "House of Apps AI",
        "company_address": "123 Main Street",
        "privacy_policy_url": "https://houseofapps.ai/privacy",
        "terms_url": "https://houseofapps.ai/terms",
    }

    subject, message, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id="org-1",
        context=context,
    )

    assert subject == "DB — Green Valley Residency"
    assert "DB hello John" in html
    assert "+91 9876543210" in message


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_resolve_unit_assignment_welcome_content_falls_back_to_files(
    mock_repo_cls,
) -> None:
    """Missing DB template uses file templates."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(return_value=None)
    context = {
        "greeting_name": "John",
        "app_name": "ATS Home Craft",
        "organization_name": "Green Valley Residency",
        "project_name": "Sunrise Towers",
        "unit_display": "Tower A — 1204",
        "phone_display": "+91 9876543210",
        "email_display": "john@example.com",
        "has_login_phone": True,
        "ios_app_store_url": None,
        "android_play_store_url": None,
        "current_year": 2026,
        "company_name": "House of Apps AI",
        "company_address": "123 Main Street",
        "privacy_policy_url": "https://houseofapps.ai/privacy",
        "terms_url": "https://houseofapps.ai/terms",
    }

    subject, message, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id="org-1",
        context=context,
    )

    assert "Green Valley Residency" in subject
    assert "+91 9876543210" in message
    assert "Tower A — 1204" in html


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_resolve_unit_assignment_welcome_content_falls_back_on_db_error(
    mock_repo_cls,
) -> None:
    """DB lookup failures fall back to file templates instead of aborting."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(
        side_effect=RuntimeError("db unavailable")
    )
    context = {
        "greeting_name": "John",
        "app_name": "ATS Home Craft",
        "organization_name": "Green Valley Residency",
        "project_name": "Sunrise Towers",
        "unit_display": "Tower A — 1204",
        "phone_display": "+91 9876543210",
        "email_display": "john@example.com",
        "has_login_phone": True,
        "ios_app_store_url": None,
        "android_play_store_url": None,
        "current_year": 2026,
        "company_name": "House of Apps AI",
        "company_address": "123 Main Street",
        "privacy_policy_url": "https://houseofapps.ai/privacy",
        "terms_url": "https://houseofapps.ai/terms",
    }

    subject, message, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id="org-1",
        context=context,
    )

    assert "Green Valley Residency" in subject
    assert "+91 9876543210" in message
    assert "Tower A — 1204" in html


def test_unit_assignment_welcome_email_shows_only_configured_store_button() -> None:
    """Only configured store URLs render as download buttons."""
    with patch(
        "apps.user_service.app.utils.email_utils.send_email", return_value=True
    ) as mock_send:
        ok = email_utils.send_unit_assignment_welcome_email(
            email="john@example.com",
            first_name="John",
            organization_name="Green Valley Residency",
            project_name="Sunrise Towers",
            unit_display="Tower A — 1204",
            login_phone="+91 9876543210",
            login_email="john@example.com",
            ios_app_store_url="https://apps.apple.com/app/id123",
            android_play_store_url="",
        )
    assert ok is True
    html = mock_send.call_args[0][3]
    assert "Download on the App Store" in html
    assert "Get it on Google Play" not in html


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
