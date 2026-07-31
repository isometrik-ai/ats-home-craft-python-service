"""Shared helpers for unit registry and plot list owner summaries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def format_contact_display_name(
    *,
    prefix: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    """Build a display name from contact name parts."""
    return " ".join(
        part
        for part in [
            (prefix or "").strip(),
            (first_name or "").strip(),
            (last_name or "").strip(),
        ]
        if part
    ).strip()


def _select_primary_jsonb_item(items: Any) -> dict[str, Any] | None:
    """Return the primary JSONB list item, or the first item when none is primary."""
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("is_primary"):
            return item
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def format_primary_contact_phone(phones: Any) -> str | None:
    """Return the primary phone number from a contact phones JSONB list."""
    phone = _select_primary_jsonb_item(phones)
    if not phone:
        return None
    isd_code = str(phone.get("phone_isd_code") or "").strip()
    number = str(phone.get("phone_number") or "").strip()
    if not number:
        return None
    return f"{isd_code}{number}".strip() if isd_code else number


def format_primary_contact_email(emails: Any) -> str | None:
    """Return the primary email address from a contact emails JSONB list."""
    email_item = _select_primary_jsonb_item(emails)
    if not email_item:
        return None
    email = str(email_item.get("email") or "").strip()
    return email or None


def format_assign_date(value: Any) -> str | None:
    """Format contact_units.assigned_at to YYYY-MM-DD for API responses."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10]
    return str(value)[:10]


def build_unit_list_owner(row: dict[str, Any]) -> dict[str, Any] | None:
    """Build owner summary from joined owner_* columns."""
    owner_contact_id = row.get("owner_contact_id")
    if not owner_contact_id:
        return None
    owner_display_name = format_contact_display_name(
        prefix=row.get("owner_prefix"),
        first_name=row.get("owner_first_name"),
        last_name=row.get("owner_last_name"),
    )
    return {
        "contact_id": str(owner_contact_id),
        "display_name": owner_display_name or None,
        "phone": format_primary_contact_phone(row.get("owner_phones")),
        "email": format_primary_contact_email(row.get("owner_emails")),
    }
