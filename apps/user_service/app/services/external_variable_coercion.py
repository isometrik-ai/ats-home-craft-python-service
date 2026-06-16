"""String coercion helpers for external integration variable values."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from apps.user_service.app.schemas.enums import FieldType


def format_address_variable_value(value: dict[str, Any]) -> str:
    """Format an address object as a single comma-separated string."""
    line1 = value.get("address_line1") or value.get("line1") or ""
    line2 = value.get("address_line2") or value.get("line2")
    city = value.get("city")
    state = value.get("state")
    postal_code = value.get("postal_code")
    country = value.get("country")

    city_state_postal = ", ".join(
        part for part in [city, " ".join(p for p in [state, postal_code] if p)] if part
    )
    parts = [part for part in [line1, line2, city_state_postal, country] if part]
    return ", ".join(str(part) for part in parts)


def format_number_variable_value(value: Any) -> str:
    """Format numeric values without unnecessary trailing decimals."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, Decimal):
        normalized = format(value, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized or "0"
    return str(value)


def format_currency_variable_value(value: Any) -> str:
    """Format a currency object as ``amount currency_code``."""
    if not isinstance(value, dict):
        return format_number_variable_value(value)
    amount = value.get("amount")
    currency_code = value.get("currency_code")
    if amount is None and currency_code is None:
        return ""
    if amount is not None and currency_code is not None:
        return f"{format_number_variable_value(amount)} {currency_code}".strip()
    if amount is not None:
        return format_number_variable_value(amount)
    return str(currency_code or "")


def _coerce_yes_no_value(value: Any) -> str:
    """Serialize a yes/no field value as ``Yes`` or ``No``."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return "Yes" if value.strip().lower() in {"true", "yes", "1"} else "No"
    return "Yes" if bool(value) else "No"


def _coerce_address_field_value(value: Any) -> str:
    """Serialize an address field value as a comma-separated string."""
    if isinstance(value, dict):
        return format_address_variable_value(value)
    return str(value)


def _coerce_date_field_value(value: Any) -> str:
    """Serialize a date field value as an ISO-8601 string."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _coerce_text_like_value(value: Any) -> str:
    """Serialize text-like field values, joining list items with commas."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None and str(item).strip())
    return str(value)


def _coerce_default_dict_value(value: dict[str, Any]) -> str:
    """Serialize an untyped dict as currency, address, or plain text."""
    if value.get("amount") is not None and value.get("currency_code") is not None:
        return format_currency_variable_value(value)
    if any(
        key in value
        for key in ("address_line1", "line1", "city", "state", "postal_code", "country")
    ):
        return format_address_variable_value(value)
    return str(value)


def _coerce_default_variable_value(value: Any) -> str:
    """Serialize a value when no field type is provided."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format_number_variable_value(value)
    if isinstance(value, dict):
        return _coerce_default_dict_value(value)
    if isinstance(value, list):
        return ", ".join(coerce_external_variable_value(item) for item in value)
    if isinstance(value, float):
        return format_number_variable_value(value)
    return str(value)


_FIELD_TYPE_HANDLERS: dict[str, Callable[[Any], str]] = {
    FieldType.YES_NO.value: _coerce_yes_no_value,
    FieldType.NUMBER.value: format_number_variable_value,
    FieldType.RANGE_SLIDER.value: format_number_variable_value,
    FieldType.CURRENCY.value: format_currency_variable_value,
    FieldType.ADDRESS.value: _coerce_address_field_value,
    FieldType.DATE.value: _coerce_date_field_value,
    FieldType.TEXT.value: _coerce_text_like_value,
    FieldType.LONG_TEXT.value: _coerce_text_like_value,
    FieldType.RICH_TEXT.value: _coerce_text_like_value,
    FieldType.URL.value: _coerce_text_like_value,
    FieldType.DROPDOWN.value: _coerce_text_like_value,
}


def coerce_external_variable_value(value: Any, *, field_type: str | None = None) -> str:
    """Serialize a resolved variable value to a string for external integrations."""
    if value is None:
        return ""

    normalized_field_type = (field_type or "").strip().lower()
    handler = _FIELD_TYPE_HANDLERS.get(normalized_field_type)
    if handler is not None:
        return handler(value)
    return _coerce_default_variable_value(value)
