"""Unit tests for unit allotment welcome email helpers."""

from __future__ import annotations

from apps.user_service.app.utils.unit_allotment_email_helpers import (
    APP_DOWNLOAD_FALLBACK_MESSAGE,
    build_allotment_location_label,
    build_app_store_url_context,
    build_unit_allotment_welcome_body_context,
    build_unit_display,
)


def test_build_unit_display_prefers_unit_code() -> None:
    row = {
        "tower_name": "Tower B",
        "unit_label": "Luxury Marquee — LUX-B6101",
        "code": "LUX-B6101",
    }
    assert build_unit_display(row) == "LUX-B6101"


def test_build_unit_display_falls_back_to_unit_label() -> None:
    row = {"tower_name": "Tower A", "unit_label": "1204", "code": ""}
    assert build_unit_display(row) == "1204"


def test_build_allotment_location_label() -> None:
    row = {
        "tower_name": "Tower A",
        "floor_name": "F18",
        "floor_level_number": 18,
    }
    assert build_allotment_location_label(row) == "Tower A · F18"


def test_build_unit_allotment_welcome_body_context() -> None:
    contact = {
        "first_name": "John",
        "emails": [{"email": "john@example.com", "is_primary": True}],
        "phones": [{"phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": True}],
    }
    allotment_row = {
        "project_name": "Sunrise Towers",
        "tower_name": "Tower A",
        "unit_label": "1204",
        "code": "A-1204",
        "floor_name": "F18",
        "floor_level_number": 18,
    }

    context = build_unit_allotment_welcome_body_context(
        contact=contact,
        allotment_row=allotment_row,
        community_name="Green Valley Residency",
    )

    assert context["registered_email"] == "john@example.com"
    assert context["registered_phone"] == "+91 9876543210"
    assert context["location_label"] == "Tower A · F18"
    assert context["unit_display"] == "A-1204"
    assert "ios_app_url" in context
    assert "android_app_url" in context
    assert "app_download_fallback" in context
    assert "app_store_links_plain" in context
    assert "app_store_cards_html" not in context


def test_build_app_store_url_context_fallback(monkeypatch) -> None:
    """When store URLs are unset, templates receive a fallback message only."""
    monkeypatch.setattr(
        "apps.user_service.app.utils.unit_allotment_email_helpers.shared_settings.mobile_app_ios_url",
        "",
    )
    monkeypatch.setattr(
        "apps.user_service.app.utils.unit_allotment_email_helpers.shared_settings.mobile_app_android_url",
        "",
    )

    context = build_app_store_url_context()

    assert context["ios_app_url"] == ""
    assert context["android_app_url"] == ""
    assert context["app_download_fallback"] == APP_DOWNLOAD_FALLBACK_MESSAGE
    assert context["app_store_links_plain"] == ""


def test_build_app_store_url_context_partial_urls() -> None:
    """Plain text includes only configured store links."""
    context = build_app_store_url_context(
        ios_url="https://apps.apple.com/example",
        android_url="",
    )

    assert context["app_download_fallback"] == ""
    assert "Download on the App Store" in context["app_store_links_plain"]
    assert "Google Play" not in context["app_store_links_plain"]
