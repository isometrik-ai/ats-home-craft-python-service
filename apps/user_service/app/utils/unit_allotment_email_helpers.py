"""Helpers for building unit allotment welcome email context."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.services.units_service import (
    build_location_label as build_unit_location_label,
)
from apps.user_service.app.utils.unit_list_serialization import (
    format_primary_contact_email,
    format_primary_contact_phone_display,
)
from libs.shared_config.app_settings import shared_settings


def build_unit_display(row: dict[str, Any]) -> str:
    """Return the unit code for email copy (location is shown separately)."""
    code = str(row.get("code") or "").strip()
    if code:
        return code
    return str(row.get("unit_label") or "").strip() or "—"


def build_allotment_location_label(row: dict[str, Any]) -> str:
    """Build tower/floor location label from a contact_units join row."""
    floor_level = row.get("floor_level_number")
    label = build_unit_location_label(
        tower_name=row.get("tower_name"),
        floor_display_name=row.get("floor_name"),
        floor_level_number=int(floor_level) if floor_level is not None else None,
    )
    return label or "—"


def _build_app_store_links_plain(ios_url: str, android_url: str) -> str:
    """Build plain-text store links; omit sections when URL is unset."""
    lines: list[str] = []
    if ios_url:
        lines.extend(["Download on the App Store", ios_url, ""])
    if android_url:
        lines.extend(["Get it on Google Play", android_url, ""])
    return "\n".join(lines).strip()


def build_app_store_url_context(
    *,
    ios_url: str | None = None,
    android_url: str | None = None,
) -> dict[str, str]:
    """Return app-store data placeholders for email templates."""
    resolved_ios = (
        ios_url if ios_url is not None else (shared_settings.mobile_app_ios_url or "")
    ).strip()
    resolved_android = (
        android_url if android_url is not None else (shared_settings.mobile_app_android_url or "")
    ).strip()
    return {
        "ios_app_url": resolved_ios,
        "android_app_url": resolved_android,
        "app_store_links_plain": _build_app_store_links_plain(resolved_ios, resolved_android),
    }


def find_owner_released_row(
    *,
    released_rows: list[dict[str, Any]],
    previous_contact_id: str | None,
) -> dict[str, Any] | None:
    """Return the released owner (relationship=self) row from a vacate result."""
    owner_rows = [row for row in released_rows if str(row.get("relationship") or "") == "self"]
    if not owner_rows:
        return None
    if previous_contact_id:
        for row in owner_rows:
            if str(row.get("contact_id") or "") == previous_contact_id:
                return row
    return owner_rows[0]


def build_unit_allotment_removed_body_context(
    *,
    contact: dict[str, Any],
    allotment_row: dict[str, Any],
    community_name: str,
    removal_reason: str,
) -> dict[str, str]:
    """Build template variables for the unit allotment removed email body."""
    first_name = str(contact.get("first_name") or "").strip() or "there"

    return {
        "app_name": shared_settings.app_name,
        "first_name": first_name,
        "community_name": community_name or shared_settings.app_name,
        "project_name": str(allotment_row.get("project_name") or "").strip() or "—",
        "unit_display": build_unit_display(allotment_row),
        "location_label": build_allotment_location_label(allotment_row),
        "removal_reason": removal_reason,
    }


def build_unit_allotment_welcome_body_context(
    *,
    contact: dict[str, Any],
    allotment_row: dict[str, Any],
    community_name: str,
) -> dict[str, str]:
    """Build template variables for the unit allotment welcome email body."""
    first_name = str(contact.get("first_name") or "").strip() or "there"
    registered_email = format_primary_contact_email(contact.get("emails")) or "—"
    registered_phone = format_primary_contact_phone_display(contact.get("phones")) or "—"

    return {
        "app_name": shared_settings.app_name,
        "first_name": first_name,
        "community_name": community_name or shared_settings.app_name,
        "project_name": str(allotment_row.get("project_name") or "").strip() or "—",
        "unit_display": build_unit_display(allotment_row),
        "location_label": build_allotment_location_label(allotment_row),
        "registered_email": registered_email,
        "registered_phone": registered_phone,
        **build_app_store_url_context(),
    }
