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

ALLOTMENT_WELCOME_STATUS = "Assigned — pending your confirmation in the app"
APP_DOWNLOAD_FALLBACK_MESSAGE = "Contact your community office for app download instructions."


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


def build_app_store_url_context() -> dict[str, str]:
    """Return app-store URL placeholders for email templates."""
    ios_url = (shared_settings.mobile_app_ios_url or "").strip()
    android_url = (shared_settings.mobile_app_android_url or "").strip()
    return {
        "ios_app_url": ios_url,
        "android_app_url": android_url,
        "ios_app_href": ios_url or "#",
        "android_app_href": android_url or "#",
        "app_download_fallback": (
            APP_DOWNLOAD_FALLBACK_MESSAGE if not ios_url and not android_url else ""
        ),
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
        "allotment_status": ALLOTMENT_WELCOME_STATUS,
        "registered_email": registered_email,
        "registered_phone": registered_phone,
        **build_app_store_url_context(),
    }
