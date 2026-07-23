"""Unit tests for shared custom-field filtering helpers."""

from __future__ import annotations

import json

import pytest

from libs.shared_utils.custom_field_filtering import (
    build_dropdown_jsonb_where,
    normalize_dropdown_filter_map,
    normalize_dropdown_filters_payload,
    parse_dropdown_filters_query_param,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_normalize_dropdown_filter_map_trims_and_dedupes() -> None:
    """Field values should be trimmed, deduped, and empty entries dropped."""
    result = normalize_dropdown_filter_map(
        {
            " field-a ": [" o1 ", "o2", "", None, "o2"],
            "": ["ignored"],
            None: ["ignored"],
        }
    )

    assert result == {"field-a": ["o1", "o2"]}


def test_normalize_dropdown_filter_map_rejects_non_list_values() -> None:
    """Non-list values should raise ValidationException."""
    with pytest.raises(ValidationException) as exc_info:
        normalize_dropdown_filter_map({"field-a": "not-a-list"})

    assert exc_info.value.params["field_id"] == "field-a"


def test_normalize_dropdown_filters_payload_none_returns_empty() -> None:
    """None payload should normalize to an empty map."""
    assert normalize_dropdown_filters_payload(None) == {}


def test_normalize_dropdown_filters_payload_array_form() -> None:
    """Array payload should merge duplicate field_ids."""
    parsed = normalize_dropdown_filters_payload(
        [
            {"field_id": "x", "values": [" o1 ", "o2", "", None, "o2"]},
            {"field_id": "y", "values": ["o2"]},
            {"field_id": "x", "values": ["o3"]},
        ]
    )

    assert parsed == {"x": ["o1", "o2", "o3"], "y": ["o2"]}


def test_normalize_dropdown_filters_payload_object_form() -> None:
    """Object payload should normalize values per field."""
    parsed = normalize_dropdown_filters_payload({"x": ["o1", "o2", "o1", " "], "y": ["o2"]})

    assert parsed == {"x": ["o1", "o2"], "y": ["o2"]}


def test_normalize_dropdown_filters_payload_rejects_invalid_top_level() -> None:
    """Top-level invalid payload types should raise ValidationException."""
    with pytest.raises(ValidationException):
        normalize_dropdown_filters_payload("not-a-dict-or-list")


def test_normalize_dropdown_filters_payload_rejects_invalid_array_items() -> None:
    """Array items must be objects with field_id and list values."""
    with pytest.raises(ValidationException):
        normalize_dropdown_filters_payload(["bad-item"])

    with pytest.raises(ValidationException):
        normalize_dropdown_filters_payload([{"values": ["o1"]}])

    with pytest.raises(ValidationException):
        normalize_dropdown_filters_payload([{"field_id": "x", "values": "bad"}])


def test_parse_dropdown_filters_query_param_empty_inputs() -> None:
    """Blank or missing query params should return an empty map."""
    assert parse_dropdown_filters_query_param(None) == {}
    assert parse_dropdown_filters_query_param("") == {}
    assert parse_dropdown_filters_query_param("   ") == {}


def test_parse_dropdown_filters_query_param_valid_json() -> None:
    """Valid JSON query params should normalize to field map."""
    raw = json.dumps([{"field_id": "x", "values": ["o1"]}])
    assert parse_dropdown_filters_query_param(raw) == {"x": ["o1"]}


def test_parse_dropdown_filters_query_param_invalid_json() -> None:
    """Invalid JSON should raise ValidationException."""
    with pytest.raises(ValidationException):
        parse_dropdown_filters_query_param("{not-json")


def test_build_dropdown_jsonb_where_empty_filters() -> None:
    """Empty filters should return no SQL fragment."""
    where_sql, args, next_idx = build_dropdown_jsonb_where(
        custom_fields_column_sql="ct.custom_fields",
        filters={},
        param_start_index=1,
    )

    assert where_sql == ""
    assert args == []
    assert next_idx == 1


def test_build_dropdown_jsonb_where_and_or_shape() -> None:
    """SQL builder should AND fields and OR values within a field."""
    where_sql, args, next_idx = build_dropdown_jsonb_where(
        custom_fields_column_sql="ct.custom_fields",
        filters={"x": ["o1", "o2"], "y": ["o2"]},
        param_start_index=3,
    )

    assert " AND " in where_sql
    assert " OR " in where_sql
    assert "jsonb_path_exists" in where_sql
    assert len(args) == 9
    assert next_idx == 12

    for probe in (args[0], args[3], args[6]):
        parsed = json.loads(probe)
        assert isinstance(parsed, list) and len(parsed) == 1
        assert "field_id" in parsed[0] and "value" in parsed[0]
