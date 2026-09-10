"""Unit tests for file-based email template rendering."""

from __future__ import annotations

import pytest

from apps.user_service.app.utils.file_email_renderer import (
    BODY_CONTENT_TOKEN,
    EmailTemplateNotFoundError,
    render_app_store_cards_html,
    render_email,
)


def _sample_body_context() -> dict[str, str]:
    from apps.user_service.app.utils.unit_allotment_email_helpers import (
        build_app_store_url_context,
    )

    return {
        "app_name": "ATS Home Craft",
        "first_name": "John",
        "community_name": "Green Valley Residency",
        "project_name": "Sunrise Towers",
        "unit_display": "A-1204",
        "location_label": "Tower A · F18",
        "registered_email": "john.doe@example.com",
        "registered_phone": "+91 9876543210",
        **build_app_store_url_context(
            ios_url="https://apps.apple.com/example",
            android_url="https://play.google.com/example",
        ),
    }


def test_render_email_merges_layout_and_body() -> None:
    """Rendered email includes layout branding and body content."""
    plain_text, html, subject = render_email(
        body="unit_allotment_welcome",
        body_context=_sample_body_context(),
    )

    assert "Welcome to" in subject
    assert "Green Valley Residency" in subject
    assert "Welcome to ATS Home Craft!" in plain_text
    assert "On behalf of the entire" in plain_text
    assert "A-1204" in plain_text
    assert "Tower A · F18" in plain_text
    assert "+91 9876543210" in plain_text
    assert "john.doe@example.com" in plain_text
    assert "YOUR REGISTERED DETAILS" in plain_text
    assert "pending your confirmation in the app" in plain_text
    assert "Accept your unit when prompted" in plain_text
    assert BODY_CONTENT_TOKEN not in html
    assert "A-1204" in html
    assert "Download on the App Store" in html
    assert "Powered by" in html


def test_render_email_escapes_html_in_body_values() -> None:
    """HTML body escapes user-provided values."""
    context = _sample_body_context()
    context["first_name"] = "<script>alert(1)</script>"

    _plain_text, html, _subject = render_email(
        body="unit_allotment_welcome",
        body_context=context,
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_render_email_missing_template_raises() -> None:
    """Missing template files raise EmailTemplateNotFoundError."""
    with pytest.raises(EmailTemplateNotFoundError):
        render_email(body="does_not_exist", body_context={"first_name": "Jane"})


def test_render_email_omits_store_buttons_when_urls_unset() -> None:
    """HTML omits store cards and shows fallback when app URLs are not configured."""
    from apps.user_service.app.utils.unit_allotment_email_helpers import (
        build_app_store_url_context,
    )

    context = _sample_body_context()
    context.update(build_app_store_url_context(ios_url="", android_url=""))

    _plain_text, html, _subject = render_email(
        body="unit_allotment_welcome",
        body_context=context,
    )

    assert "Contact your community office for app download instructions." in _plain_text
    assert "Download on the App Store" not in html
    assert 'href="#"' not in html


def test_render_app_store_cards_html_partial_urls() -> None:
    """Only configured store cards are rendered from partial templates."""
    html = render_app_store_cards_html(
        ios_url="https://apps.apple.com/example",
        android_url="",
    )

    assert "Download on the App Store" in html
    assert "Google Play" not in html
    assert 'href="#"' not in html


def test_render_unit_allotment_removed_email() -> None:
    """Removed email renders informational copy without welcome onboarding sections."""
    plain_text, html, subject = render_email(
        body="unit_allotment_removed",
        body_context={
            "app_name": "ATS Home Craft",
            "first_name": "Jane",
            "community_name": "Green Valley Residency",
            "project_name": "Sunrise Towers",
            "unit_display": "A-1204",
            "location_label": "Tower A · F18",
            "removal_reason": "unassigned",
        },
    )

    assert "Unit allotment removed" in subject
    assert "Removed from your account" in plain_text
    assert "removed by your community administration team" in plain_text
    assert "Download on the App Store" not in html
    assert "What this means" in html


def test_render_email_excludes_login_credentials_copy() -> None:
    """Unit allotment welcome templates do not include credential wording."""
    plain_text, html, _subject = render_email(
        body="unit_allotment_welcome",
        body_context=_sample_body_context(),
    )

    for content in (plain_text, html):
        assert "login credentials" not in content.lower()
        assert "temporary password" not in content.lower()
        assert "Password:" not in content
        assert "registered details" in content.lower()
        assert "Kx9#nP2vLq" not in content
