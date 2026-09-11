"""Unit tests for record serialization helpers."""

from datetime import date, datetime, timezone
from uuid import UUID

from apps.work_order_service.app.utils.records import (
    jsonb_bind,
    jsonb_bind_required,
    parse_json_field,
    record_to_dict,
)


class _FakeRow:
    def __init__(self, data: dict) -> None:
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key: str):
        return self._data[key]


def test_record_to_dict_parses_jsonb_strings():
    """JSONB columns double-encoded as strings should decode to arrays/objects."""
    row = _FakeRow(
        {
            "timeline": '[{"type": "created", "note": "hello"}]',
            "form_values": "{}",
            "line_items": "[]",
            "title": "Fix HVAC",
        }
    )
    out = record_to_dict(row)
    assert out["timeline"] == [{"type": "created", "note": "hello"}]
    assert out["form_values"] == {}
    assert out["line_items"] == []
    assert out["title"] == "Fix HVAC"


def test_record_to_dict_serializes_dates_and_uuids():
    """Native Postgres types should remain API-friendly."""
    row = _FakeRow(
        {
            "id": UUID("80ddd2d8-ef12-4ca1-a70f-2e67ed076a99"),
            "scheduled_date": date(2026, 9, 22),
            "created_at": datetime(2026, 9, 11, 6, 50, 21, tzinfo=timezone.utc),
        }
    )
    out = record_to_dict(row)
    assert out["id"] == "80ddd2d8-ef12-4ca1-a70f-2e67ed076a99"
    assert out["scheduled_date"] == "2026-09-22"
    assert out["created_at"] == "2026-09-11T06:50:21+00:00"


def test_parse_json_field_leaves_plain_text_untouched():
    """Regular text fields must not be treated as JSON."""
    assert parse_json_field("Vendor token issued") == "Vendor token issued"


def test_jsonb_bind_serializes_collections_for_asyncpg():
    """asyncpg jsonb parameters must be JSON text, not Python lists."""
    assert jsonb_bind([]) is None
    assert jsonb_bind({}) is None
    assert jsonb_bind([{"type": "created"}]) == '[{"type": "created"}]'
    assert jsonb_bind_required(None) == "[]"
    assert jsonb_bind_required({"a": 1}, default="{}") == '{"a": 1}'
