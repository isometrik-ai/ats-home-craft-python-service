"""Unit tests for common utility functions."""

import datetime as dt
import uuid

import pytest

from apps.user_service.app.utils.common_utils import (
    format_iso_datetime,
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
