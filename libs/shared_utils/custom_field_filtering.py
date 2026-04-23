"""Shared helpers for filtering entities by custom-field dropdown values.

This module is intentionally DB-agnostic except for building SQL fragments and
asyncpg arg lists. It does not depend on any single entity (contacts/companies/...).
"""

from __future__ import annotations

import json
from typing import Any

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


def normalize_dropdown_filter_map(
    raw: dict[str, Any],
) -> dict[str, list[str]]:
    """Normalize {field_id: values} into trimmed, deduped lists.

    Drops empty/blank values. Drops fields with no remaining values.
    """
    out: dict[str, list[str]] = {}

    for field_id, values in raw.items():
        fid = (str(field_id) if field_id is not None else "").strip()
        if not fid:
            continue
        if not isinstance(values, list):
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"expected_type": "array(values)", "field_id": fid},
            )
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            str_value = (str(value) if value is not None else "").strip()
            if not str_value or str_value in seen:
                continue
            seen.add(str_value)
            normalized.append(str_value)
        if normalized:
            out[fid] = normalized
    return out


def normalize_dropdown_filters_payload(payload: Any) -> dict[str, list[str]]:
    """Normalize a dropdown filter payload into {field_id: [values]} map.

    Accepted shapes:
    - array: [{ "field_id": "<uuid>", "values": ["o1","o2"] }, ...]
    - object: { "<uuid>": ["o1","o2"], "<uuid2>": ["o2"] }
    """
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return normalize_dropdown_filter_map(payload)
    if not isinstance(payload, list):
        raise ValidationException(
            message_key="custom_fields.errors.invalid_filter_payload",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"expected_type": "array"},
        )

    as_map: dict[str, list[Any]] = {}
    for idx, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"expected_type": "object", "index": idx},
            )
        fid = (str(item.get("field_id") or "")).strip()
        values = item.get("values")
        if not fid:
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"details": "field_id is required", "index": idx},
            )
        if not isinstance(values, list):
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={
                    "details": "values must be an array",
                    "field_id": fid,
                    "index": idx,
                },
            )
        as_map.setdefault(fid, []).extend(values)
    return normalize_dropdown_filter_map(as_map)


def parse_dropdown_filters_query_param(raw: str | None) -> dict[str, list[str]]:
    """Parse JSON query param into normalized {field_id: [values]} map.

    Accepted input shapes:
    - JSON array: [{ "field_id": "<uuid>", "values": ["o1","o2"] }, ...]
    - JSON object: { "<uuid>": ["o1","o2"], "<uuid2>": ["o2"] }
    """
    if raw is None:
        return {}
    raw_s = raw.strip()
    if not raw_s:
        return {}
    try:
        parsed = json.loads(raw_s)
    except Exception as exc:
        raise ValidationException(
            message_key="custom_fields.errors.invalid_filter_payload",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"details": "Invalid JSON in dropdown_filters."},
        ) from exc
    return normalize_dropdown_filters_payload(parsed)


def build_dropdown_jsonb_where(
    *,
    custom_fields_column_sql: str,
    filters: dict[str, list[str]],
    param_start_index: int,
) -> tuple[str, list[Any], int]:
    """Build SQL WHERE fragment for dropdown filters.

    Semantics:
    - AND across field_ids
    - OR within values for the same field_id

    Matching strategy (efficient + works with nested custom_fields):
    - First try fast root-array containment: `custom_fields @> '[{"field_id":"..","value":".."}]'`
      (can use GIN jsonb_path_ops when present).
    - Also check nested matches with `jsonb_path_exists(custom_fields, '$.** ? (...)', vars)`.

    Returns:
    - where_sql: string (no leading/trailing AND)
    - args: asyncpg args list to append
    - next_param_index: next available $ param index
    """
    if not filters:
        return "", [], param_start_index

    clauses: list[str] = []
    args: list[Any] = []
    next_idx = param_start_index

    for field_id, values in filters.items():
        if not values:
            continue
        or_parts: list[str] = []
        for value in values:
            # Root array match (fast path).
            probe = json.dumps(
                [{"field_id": field_id, "value": value}],
                separators=(",", ":"),
            )
            probe_param = next_idx
            args.append(probe)
            next_idx += 1

            # Nested match (handles sub_fields/items anywhere in the tree).
            # We bind field_id/value as vars to avoid string interpolation.
            fid_param = next_idx
            args.append(field_id)
            next_idx += 1

            val_param = next_idx
            args.append(value)
            next_idx += 1

            nested = (
                "jsonb_path_exists("
                f"{custom_fields_column_sql}, "
                "'$.** ? (@.field_id == $fid && @.value == $val)', "
                f"jsonb_build_object('fid', ${fid_param}::text, 'val', ${val_param}::text)"
                ")"
            )
            or_parts.append(f"({custom_fields_column_sql} @> ${probe_param}::jsonb OR {nested})")
        if or_parts:
            clauses.append("(" + " OR ".join(or_parts) + ")")

    return " AND ".join(clauses), args, next_idx
