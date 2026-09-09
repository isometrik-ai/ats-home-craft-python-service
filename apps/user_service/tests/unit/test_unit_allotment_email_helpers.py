"""Unit tests for unit allotment welcome email helpers."""

from __future__ import annotations

from apps.user_service.app.utils.unit_allotment_email_helpers import (
    build_allotment_location_label,
    build_app_store_url_context,
    build_unit_allotment_removed_body_context,
    build_unit_allotment_welcome_body_context,
    build_unit_display,
    find_owner_released_row,
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
    assert "app_store_links_plain" in context
    assert "allotment_status" not in context
    assert "app_download_fallback" not in context


def test_build_app_store_url_context() -> None:
    context = build_app_store_url_context(
        ios_url="https://apps.apple.com/example",
        android_url="",
    )

    assert context["ios_app_url"] == "https://apps.apple.com/example"
    assert context["android_app_url"] == ""
    assert "Download on the App Store" in context["app_store_links_plain"]
    assert "Google Play" not in context["app_store_links_plain"]
    assert "app_download_fallback" not in context


def test_find_owner_released_row() -> None:
    released = [
        {"id": "cu-family", "contact_id": "family-1", "relationship": "family"},
        {"id": "cu-owner", "contact_id": "owner-1", "relationship": "self"},
    ]
    row = find_owner_released_row(
        released_rows=released,
        previous_contact_id="owner-1",
    )
    assert row is not None
    assert row["id"] == "cu-owner"


def test_build_unit_allotment_removed_body_context() -> None:
    contact = {"first_name": "Jane", "emails": [], "phones": []}
    allotment_row = {
        "project_name": "Sunrise Towers",
        "tower_name": "Tower A",
        "unit_label": "1204",
        "code": "A-1204",
        "floor_name": "F18",
        "floor_level_number": 18,
    }

    context = build_unit_allotment_removed_body_context(
        contact=contact,
        allotment_row=allotment_row,
        community_name="Green Valley Residency",
        removal_reason="unassigned",
    )

    assert context["removal_reason"] == "unassigned"
    assert context["unit_display"] == "A-1204"
    assert "removal_reason_message" not in context
    assert "removed_status" not in context
