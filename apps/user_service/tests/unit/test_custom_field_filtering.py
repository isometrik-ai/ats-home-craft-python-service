"""Unit tests for shared custom-field filtering helpers."""

import json

import pytest

from libs.shared_utils.custom_field_filtering import (
    build_dropdown_jsonb_where,
    normalize_dropdown_filters_payload,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_normalize_dropdown_filters_array_form() -> None:
    """Test parse_dropdown_filters_query_param with array form."""
    parsed = normalize_dropdown_filters_payload(
        [
            {"field_id": "x", "values": [" o1 ", "o2", "", None, "o2"]},
            {"field_id": "y", "values": ["o2"]},
        ]
    )
    assert parsed == {"x": ["o1", "o2"], "y": ["o2"]}


def test_normalize_dropdown_filters_object_form() -> None:
    """Test parse_dropdown_filters_query_param with object form."""
    parsed = normalize_dropdown_filters_payload({"x": ["o1", "o2", "o1", " "], "y": ["o2"]})
    assert parsed == {"x": ["o1", "o2"], "y": ["o2"]}


def test_normalize_dropdown_filters_reject_invalid() -> None:
    """Test parse_dropdown_filters_query_param rejects invalid JSON."""
    with pytest.raises(ValidationException):
        normalize_dropdown_filters_payload("not-a-dict-or-list")


def test_build_dropdown_jsonb_where_and_or_shape() -> None:
    """Test build_dropdown_jsonb_where with AND and OR shape."""
    where_sql, args, next_idx = build_dropdown_jsonb_where(
        custom_fields_column_sql="ct.custom_fields",
        filters={"x": ["o1", "o2"], "y": ["o2"]},
        param_start_index=3,
    )
    # AND across fields.
    assert " AND " in where_sql
    # OR within a field.
    assert " OR " in where_sql
    # Each option binds 3 args: probe json + field_id + value
    # 3 options total: x(o1,o2) + y(o2) => 9 args
    assert len(args) == 9
    assert next_idx == 12
    # First of each triplet is the JSON probe.
    for probe in (args[0], args[3], args[6]):
        parsed = json.loads(probe)
        assert isinstance(parsed, list) and len(parsed) == 1
        assert "field_id" in parsed[0] and "value" in parsed[0]
