"""Unit tests for ContactsImportService static helpers."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.contacts_imports_service import (
    ContactsImportService,
)

SVC = ContactsImportService


def test_safe_parse_json_dict_valid() -> None:
    """Parse JSON string into dict."""
    assert SVC._safe_parse_json_dict('{"a": 1}') == {"a": 1}


def test_safe_parse_json_dict_invalid() -> None:
    """Invalid JSON returns empty dict."""
    assert SVC._safe_parse_json_dict("not-json") == {}


def test_build_reverse_mapping() -> None:
    """Reverse mapping swaps canonical to header keys."""
    mapping = {"email": "Email", "first_name": "First Name"}
    reverse = SVC._build_reverse_mapping(mapping=mapping)
    assert reverse["Email"] == "email"
    assert reverse["First Name"] == "first_name"


def test_canonicalize_row_maps_headers() -> None:
    """Canonicalize applies reverse mapping to CSV row."""
    reverse = {"Email": "email", "First Name": "first_name"}
    row = {"Email": "a@b.com", "First Name": "Jane", "Unknown": "x"}
    canonical = SVC._canonicalize_row(row=row, reverse_mapping=reverse)
    assert canonical["email"] == "a@b.com"
    assert canonical["first_name"] == "Jane"
    assert canonical["Unknown"] == "x"


def test_normalize_phone_digits() -> None:
    """Phone normalization strips non-digits."""
    assert SVC._normalize_phone_digits(phone_number="555-1234", phone_isd_code="+1") == "15551234"


def test_synthetic_email_from_phone() -> None:
    """Phone-only rows get synthetic email."""
    email = SVC._synthetic_email_from_phone(phone_number="5551234567", phone_isd_code="+1")
    assert email == "15551234567@email.com"


def test_synthetic_email_from_phone_empty() -> None:
    """Empty phone returns None synthetic email."""
    assert SVC._synthetic_email_from_phone(phone_number="", phone_isd_code=None) is None


def test_extract_phones_from_flat_columns() -> None:
    """Flat phone columns become primary phone list."""
    phones = SVC._extract_phones_from_canonical({"phone_number": "5551234", "phone_isd_code": "+1"})
    assert len(phones) == 1
    assert phones[0]["phone_number"] == "5551234"
    assert phones[0]["is_primary"] is True


def test_primary_phone_fields_prefers_primary() -> None:
    """Primary phone is selected when flagged."""
    phones = [
        {"phone_number": "111", "phone_isd_code": "+1", "is_primary": False},
        {"phone_number": "222", "phone_isd_code": "+1", "is_primary": True},
    ]
    result = SVC._primary_phone_fields(phones)
    assert result == ("222", "+1")


def test_row_item_missing_contact_identifier() -> None:
    """Missing identifier builds error row item."""
    item = SVC._row_item_missing_contact_identifier(
        row_number=3,
        raw_row={"col": "v"},
        message="missing",
    )
    assert item["row_number"] == 3
    assert item["error"]["code"] == "missing_email_or_phone"
    assert item["contact_model"] is None


def test_filter_claimed_by_status() -> None:
    """Rows already marked success are filtered out."""
    batch = [{"row_number": 1}, {"row_number": 2}, {"row_number": 3}]
    statuses = {1: "success", 2: "processing", 3: "success"}
    filtered = SVC._filter_claimed_by_status(batch=batch, statuses=statuses)
    assert [r["row_number"] for r in filtered] == [2]


def test_extract_claim_rows_with_error() -> None:
    """Claim rows pass None raw_row when item has error."""
    batch = [
        {"row_number": 1, "raw_row": {"a": 1}},
        {"row_number": 2, "raw_row": {"b": 2}, "error": {"code": "x"}},
    ]
    claim = SVC._extract_claim_rows(batch=batch)
    assert claim == [(1, None), (2, {"b": 2})]


def test_collect_emails_for_uniqueness() -> None:
    """Collect normalized emails from contact models."""

    class _Model:
        def __init__(self, email: str):
            self.email = email

    claimed = [
        {"row_number": 1, "contact_model": _Model("A@Example.com")},
        {"row_number": 2, "contact_model": _Model("")},
        {"row_number": 3, "error": True},
    ]
    emails, by_row = SVC._collect_emails_for_uniqueness_check(claimed=claimed)
    assert emails == ["a@example.com"]
    assert by_row[1] == "a@example.com"


def test_collect_in_file_duplicate_emails() -> None:
    """Duplicate emails in batch produce error tuples."""

    class _Model:
        def __init__(self, email: str):
            self.email = email

    claimed = [
        {"row_number": 1, "contact_model": _Model("dup@example.com")},
        {"row_number": 2, "contact_model": _Model("dup@example.com")},
        {"row_number": 3, "contact_model": _Model("unique@example.com")},
    ]
    errors = SVC._collect_in_file_duplicate_email_errors(claimed=claimed)
    assert len(errors) == 1
    assert errors[0][0] == 2
    assert errors[0][1] == "duplicate_email_in_file"


def test_remove_rows_by_row_numbers() -> None:
    """Remove helper drops rows by row number from both lists."""
    rows = [{"row_number": 1}, {"row_number": 2}, {"row_number": 3}]
    numbers = [1, 2, 3]
    filtered_rows, filtered_numbers = SVC._remove_rows_by_row_numbers(
        valid_rows=rows,
        valid_row_numbers=numbers,
        bad_rows={2},
    )
    assert [r["row_number"] for r in filtered_rows] == [1, 3]
    assert filtered_numbers == [1, 3]


def test_resolve_customer_list_name_from_options() -> None:
    """Customer list name comes from options when set."""
    name = SVC._resolve_customer_list_name(
        job_key="imp_abc",
        options={"customer_list_name": "My List"},
    )
    assert name == "My List"


def test_resolve_customer_list_name_default() -> None:
    """Default customer list name uses job key suffix."""
    name = SVC._resolve_customer_list_name(job_key="imp_abc123", options={})
    assert "imp_abc123" in name


def test_validate_file_url_rejects_localhost() -> None:
    """Localhost URLs are rejected."""
    with pytest.raises(ValueError, match="not allowed"):
        SVC._validate_file_url("http://localhost/file.csv")


def test_validate_file_url_accepts_https() -> None:
    """Public HTTPS URLs are accepted."""
    SVC._validate_file_url("https://cdn.example.com/contacts.csv")


def test_build_mark_error_tuple() -> None:
    """Error tuple matches repository contract."""
    tup = SVC._build_mark_error_tuple(
        row_number=5,
        code="bad_row",
        message="errors.bad",
        raw_row={"x": 1},
    )
    assert tup == (5, "bad_row", "errors.bad", {"x": 1})
