"""Unit tests for shared list filter schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.list_filters import DropdownCustomFieldFilter
from libs.shared_utils.http_exceptions import ValidationException


def test_dropdown_filter_normalizes_and_deduplicates_values():
    """Whitespace is trimmed and duplicate values removed."""
    filt = DropdownCustomFieldFilter(
        field_id="  field-1  ",
        values=[" Option A ", "Option A", "Option B", ""],
    )

    assert filt.field_id == "field-1"
    assert filt.values == ["Option A", "Option B"]


def test_dropdown_filter_rejects_empty_field_id():
    """Blank field_id raises validation error."""
    with pytest.raises(ValidationException):
        DropdownCustomFieldFilter(field_id="   ", values=["x"])


def test_dropdown_filter_rejects_all_empty_values():
    """At least one non-empty value is required."""
    with pytest.raises(ValidationException):
        DropdownCustomFieldFilter(field_id="field-1", values=["", "  "])


def test_dropdown_filter_rejects_missing_values():
    """Pydantic enforces min_length on values list."""
    with pytest.raises(ValidationError):
        DropdownCustomFieldFilter(field_id="field-1", values=[])
