"""Unit tests for common utility functions."""

import datetime as dt
import uuid

import pytest

from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import ContactType
from apps.user_service.app.utils.common_utils import (
    format_iso_datetime,
    parse_flexible_date,
    safe_json_loads,
    validate_uuid_format,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_format_iso_datetime_with_datetime():
    """Test format_iso_datetime with datetime object."""
    now = dt.datetime(2024, 1, 1, 12, 0, 0)
    assert format_iso_datetime(now) == now.isoformat()


def test_format_iso_datetime_with_string():
    """Test format_iso_datetime with string input."""
    iso_str = "2024-01-01T12:00:00"
    assert format_iso_datetime(iso_str) == iso_str


def test_format_iso_datetime_with_none():
    """Test format_iso_datetime with None input."""
    assert format_iso_datetime(None) is None


def test_safe_json_loads_valid():
    """Test safe_json_loads with valid JSON."""
    assert safe_json_loads('{"a":1}') == {"a": 1}


def test_safe_json_loads_invalid_returns_default():
    """Test safe_json_loads with invalid JSON returns default."""
    assert safe_json_loads("not-json", default={}) == {}


def test_validate_uuid_format_valid():
    """Test validate_uuid_format with valid UUID."""
    valid = str(uuid.uuid4())
    validate_uuid_format(valid)  # should not raise


def test_validate_uuid_format_invalid():
    """Test validate_uuid_format with invalid UUID raises exception."""
    with pytest.raises(ValidationException):
        validate_uuid_format("not-a-uuid")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1992-11-02", dt.date(1992, 11, 2)),
        ("11/2/1992", dt.date(1992, 11, 2)),
        ("11-02-1992", dt.date(1992, 11, 2)),
        ("31/12/1992", dt.date(1992, 12, 31)),
        ("", None),
        (None, None),
    ],
)
def test_parse_flexible_date(raw, expected):
    """Test parse_flexible_date accepts common input formats."""
    assert parse_flexible_date(raw) == expected


def test_parse_flexible_date_invalid_raises():
    """Test parse_flexible_date rejects unparseable values."""
    with pytest.raises(ValueError, match="Unable to parse date"):
        parse_flexible_date("not-a-date")


def test_create_contact_flexible_dob():
    """CreateContactRequest normalizes flexible DOB strings to date objects."""
    model = CreateContactRequest(
        email="user@example.com",
        contact_type=ContactType.OWNER,
        date_of_birth="11/2/1992",
    )
    assert model.date_of_birth == dt.date(1992, 11, 2)


def test_create_contact_accepts_crm_fields():
    """DB-mapped CRM fields are accepted on create."""
    model = CreateContactRequest(
        email="user@example.com",
        contact_type=ContactType.OWNER,
        websites=[],
        custom_fields=[{"field_id": "cf-1", "value": "test"}],
        additional_data={"source": "import"},
    )
    assert model.custom_fields[0]["field_id"] == "cf-1"


def test_contact_phone_rejects_extra_fields():
    """Contact phones only allow phone_number and is_primary."""
    with pytest.raises(ValueError):
        CreateContactRequest(
            email="user@example.com",
            contact_type=ContactType.OWNER,
            phones=[
                {
                    "phone_number": "+14155550100",
                    "phone_isd_code": "+1",
                    "is_primary": True,
                }
            ],
        )


def test_contact_phone_accepts_full_number():
    """Contact phones accept ISD code embedded in phone_number."""
    model = CreateContactRequest(
        email="user@example.com",
        contact_type=ContactType.OWNER,
        phones=[{"phone_number": "+14155550100", "is_primary": True}],
    )
    assert model.phones[0].phone_number == "+14155550100"
