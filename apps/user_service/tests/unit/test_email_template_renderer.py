"""Unit tests for file-based transactional email templates."""

from __future__ import annotations

from apps.user_service.app.utils.email_template_renderer import (
    render_email_template,
    render_transactional_email,
)


def _sample_context() -> dict:
    return {
        "greeting_name": "John",
        "app_name": "ATS Home Craft",
        "organization_name": "Green Valley Residency",
        "project_name": "Sunrise Towers",
        "unit_display": "Tower A — 1204",
        "phone_display": "+91 9876543210",
        "email_display": "john@example.com",
        "has_login_phone": True,
        "ios_app_store_url": "https://apps.apple.com/app/id123",
        "android_play_store_url": "https://play.google.com/store/apps/details?id=com.example",
        "current_year": 2026,
        "company_name": "House of Apps AI",
        "company_address": "123 Main Street",
        "privacy_policy_url": "https://houseofapps.ai/privacy",
        "terms_url": "https://houseofapps.ai/terms",
    }


def test_render_string_template_supports_jinja_logic() -> None:
    """Runtime string templates compile Jinja conditionals."""
    from apps.user_service.app.utils.email_template_renderer import (
        render_string_template,
    )

    rendered = render_string_template(
        "{% if show %}Hi {{ name }}{% else %}Bye{% endif %}",
        {"show": True, "name": "Ada"},
        autoescape=False,
    )
    assert rendered == "Hi Ada"


def test_render_transactional_email_from_db_row() -> None:
    """DB row overrides subject and HTML while plain text uses the file template."""
    from apps.user_service.app.utils.email_template_renderer import (
        render_transactional_email_from_db_row,
    )

    subject, message, html = render_transactional_email_from_db_row(
        "unit_assignment_welcome",
        _sample_context(),
        {
            "subject": "Custom — {{ organization_name }}",
            "html_content": "<p>Hello {{ greeting_name }} at {{ unit_display }}</p>",
        },
    )
    assert subject == "Custom — Green Valley Residency"
    assert "Tower A — 1204" in html
    assert "+91 9876543210" in message


def test_render_transactional_email_from_db_row_falls_back_without_html() -> None:
    """Empty DB html_content falls back to file templates."""
    from apps.user_service.app.utils.email_template_renderer import (
        render_transactional_email,
        render_transactional_email_from_db_row,
    )

    expected = render_transactional_email("unit_assignment_welcome", _sample_context())
    assert (
        render_transactional_email_from_db_row(
            "unit_assignment_welcome",
            _sample_context(),
            {"subject": "Ignored", "html_content": "   "},
        )
        == expected
    )


def test_render_unit_assignment_subject() -> None:
    """Subject template renders organization name."""
    subject = render_email_template(
        "unit_assignment_welcome",
        "subject.txt",
        _sample_context(),
    )
    assert "Green Valley Residency" in subject
    assert "ATS Home Craft" in subject


def test_render_unit_assignment_html_includes_store_buttons() -> None:
    """HTML template includes configured store download links."""
    _, _, html = render_transactional_email("unit_assignment_welcome", _sample_context())
    assert "Tower A — 1204" in html
    assert "https://apps.apple.com/app/id123" in html
    assert "Download on the App Store" in html


def test_render_unit_assignment_text_omits_password() -> None:
    """Plain-text template never includes password wording."""
    _, message, _ = render_transactional_email("unit_assignment_welcome", _sample_context())
    assert "password" not in message.lower()
    assert "+91 9876543210" in message


def test_render_unit_assignment_html_without_store_urls() -> None:
    """Store buttons are omitted when URLs are not configured."""
    context = _sample_context()
    context["ios_app_store_url"] = None
    context["android_play_store_url"] = None
    _, _, html = render_transactional_email("unit_assignment_welcome", context)
    assert "Download on the App Store" not in html
    assert "Get it on Google Play" not in html
