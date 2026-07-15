"""Unit tests for contacts bulk import row email/phone resolution."""

from __future__ import annotations

from apps.user_service.app.services.contacts_imports_service import (
    ContactsImportService,
)


def test_resolve_row_uses_provided_email() -> None:
    """Prefer CSV email when both email and phone are present."""
    email, phones, err = ContactsImportService._resolve_row_email_and_phones(
        canonical={"email": "jane@example.com", "phone_number": "4155550100"}
    )
    assert err is None
    assert email == "jane@example.com"
    assert phones[0]["phone_number"] == "4155550100"


def test_resolve_row_synthetic_email_from_flat_phone() -> None:
    """Build ``{digits}@email.com`` from flat phone_number and phone_isd_code."""
    email, phones, err = ContactsImportService._resolve_row_email_and_phones(
        canonical={"phone_number": "4155550100", "phone_isd_code": "+1"}
    )
    assert err is None
    assert email == "14155550100@email.com"
    assert phones[0]["is_primary"] is True


def test_resolve_row_synthetic_email_from_phones_json() -> None:
    """Build synthetic email from primary phone in phones_json."""
    email, phones, err = ContactsImportService._resolve_row_email_and_phones(
        canonical={
            "phones_json": (
                '[{"phone_number":"2125550199","phone_isd_code":"+1","is_primary":true}]'
            )
        }
    )
    assert err is None
    assert email == "12125550199@email.com"
    assert len(phones) == 1


def test_resolve_row_fails_without_email_or_phone() -> None:
    """Reject rows that have neither email nor phone."""
    email, phones, err = ContactsImportService._resolve_row_email_and_phones(
        canonical={"first_name": "Jane"}
    )
    assert email == ""
    assert phones == []
    assert err == "either email or phone number is required"
