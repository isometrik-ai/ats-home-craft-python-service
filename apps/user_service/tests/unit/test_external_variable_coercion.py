"""Unit tests for external variable coercion helpers."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from apps.user_service.app.schemas.enums import FieldType
from apps.user_service.app.services.external_variable_coercion import (
    coerce_external_variable_value,
    format_address_variable_value,
    format_currency_variable_value,
    format_number_variable_value,
)


def test_format_address_variable_value_full():
    """Address dict formats as comma-separated parts."""
    formatted = format_address_variable_value(
        {
            "address_line1": "500 Market St",
            "address_line2": "Suite 10",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94104",
            "country": "US",
        }
    )
    assert formatted == "500 Market St, Suite 10, San Francisco, CA 94104, US"


def test_format_address_variable_value_line_aliases():
    """line1/line2 aliases are accepted."""
    formatted = format_address_variable_value(
        {"line1": "1 Main", "city": "Austin", "state": "TX", "postal_code": "78701"}
    )
    assert formatted == "1 Main, Austin, TX 78701"


def test_format_number_variable_value():
    """Numeric values drop unnecessary decimals."""
    assert format_number_variable_value(True) == "Yes"
    assert format_number_variable_value(False) == "No"
    assert format_number_variable_value(42) == "42"
    assert format_number_variable_value(10.0) == "10"
    assert format_number_variable_value(10.5) == "10.5"
    assert format_number_variable_value(Decimal("99.5000")) == "99.5"


def test_format_currency_variable_value():
    """Currency dicts render amount and code."""
    assert format_currency_variable_value({"amount": 100, "currency_code": "USD"}) == "100 USD"
    assert format_currency_variable_value({"amount": 50}) == "50"
    assert format_currency_variable_value({"currency_code": "EUR"}) == "EUR"
    assert format_currency_variable_value({}) == ""


def test_coerce_none_returns_empty_string():
    """None values serialize to empty string."""
    assert coerce_external_variable_value(None) == ""


def test_coerce_yes_no_field_type():
    """Yes/no field type coerces booleans and strings."""
    assert coerce_external_variable_value(True, field_type=FieldType.YES_NO.value) == "Yes"
    assert coerce_external_variable_value("yes", field_type=FieldType.YES_NO.value) == "Yes"
    assert coerce_external_variable_value("0", field_type=FieldType.YES_NO.value) == "No"


def test_coerce_number_and_currency_field_types():
    """Number and currency handlers format scalars."""
    assert coerce_external_variable_value(12.0, field_type=FieldType.NUMBER.value) == "12"
    assert (
        coerce_external_variable_value(
            {"amount": Decimal("10.00"), "currency_code": "INR"},
            field_type=FieldType.CURRENCY.value,
        )
        == "10 INR"
    )


def test_coerce_address_field_type():
    """Address field type formats dict values."""
    value = {"address_line1": "10 Downing", "city": "London", "country": "UK"}
    assert coerce_external_variable_value(value, field_type=FieldType.ADDRESS.value) == (
        "10 Downing, London, UK"
    )


def test_coerce_date_field_type():
    """Date field type uses ISO formatting."""
    assert (
        coerce_external_variable_value(date(2024, 1, 2), field_type=FieldType.DATE.value)
        == "2024-01-02"
    )


def test_coerce_text_like_joins_lists():
    """Text-like field types join list items."""
    assert (
        coerce_external_variable_value(["alpha", "beta"], field_type=FieldType.TEXT.value)
        == "alpha, beta"
    )


def test_coerce_default_for_untyped_values():
    """Default coercion handles common scalar and container types."""
    assert coerce_external_variable_value(False) == "No"
    assert coerce_external_variable_value(datetime(2024, 6, 1, 12, 0, 0)) == "2024-06-01T12:00:00"
    assert coerce_external_variable_value([1, 2]) == "1, 2"
    assert coerce_external_variable_value({"amount": 5, "currency_code": "USD"}) == "5 USD"


def test_coerce_default_dict_as_address():
    """Untyped dict with address keys formats as address."""
    assert coerce_external_variable_value({"line1": "Park Ave", "city": "NYC"}) == "Park Ave, NYC"
