"""Supplemental FalkorDB range indexes for CRM Graphiti workloads.

Graphiti's ``build_indices_and_constraints()`` already creates core range and
fulltext indexes on ``group_id``, ``uuid``, ``name``, and edge facts. This module
adds CRM-specific composite indexes so tenant-scoped lookups stay fast as the
shared ``default_db`` graph grows.

FalkorDB often reports composite indexes without expanding every field in
``CALL db.indexes()`` ``types`` maps, so we also create single-field indexes
that verification expects and parse composite field lists from index metadata.
"""

from __future__ import annotations

import re
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver

from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_index_maintenance")

_INDEX_PROPERTY_RE = re.compile(r"\bn\.(\w+)\b")

# Composite range indexes for multi-tenant CRM queries (group_id is always filtered).
CRM_SUPPLEMENTAL_RANGE_INDICES: tuple[str, ...] = (
    "CREATE INDEX FOR (n:Entity) ON (n.group_id, n.crm_id, n.crm_entity_type)",
    "CREATE INDEX FOR (n:Entity) ON (n.group_id, n.name)",
    "CREATE INDEX FOR (n:Episodic) ON (n.group_id, n.name)",
    "CREATE INDEX FOR (n:Episodic) ON (n.group_id, n.uuid)",
    # Single-field indexes so ``CALL db.indexes()`` lists fields verification expects.
    "CREATE INDEX FOR (n:Entity) ON (n.crm_id)",
    "CREATE INDEX FOR (n:Episodic) ON (n.name)",
)

# Minimum index fields we expect after Graphiti bootstrap + CRM supplements.
_EXPECTED_ENTITY_INDEX_FIELDS = frozenset({"group_id", "crm_id", "uuid", "name"})
_EXPECTED_EPISODIC_INDEX_FIELDS = frozenset({"group_id", "name", "uuid"})


async def _execute_index_query(driver: FalkorDriver, query: str) -> None:
    """Run one CREATE INDEX statement; ignore already-existing indexes."""
    await driver.execute_query(query)


async def ensure_crm_supplemental_indices(driver: FalkorDriver) -> int:
    """Create CRM-specific range indexes (idempotent). Returns count applied."""
    applied = 0
    for query in CRM_SUPPLEMENTAL_RANGE_INDICES:
        await _execute_index_query(driver, query)
        applied += 1
    return applied


async def ensure_graphiti_indices(
    graphiti: Graphiti,
    *,
    driver: FalkorDriver,
) -> None:
    """Ensure Graphiti core indices and CRM supplemental indices exist."""
    await graphiti.build_indices_and_constraints()
    count = await ensure_crm_supplemental_indices(driver)
    logger.info("graphiti_crm_supplemental_indices_ensured count=%s", count)


def _fields_from_index_text(text: str) -> set[str]:
    """Extract ``n.<field>`` property names from an index definition string."""
    return set(_INDEX_PROPERTY_RE.findall(text))


def _fields_from_index_record(record: dict[str, Any]) -> set[str]:
    """Collect indexed property names from one ``CALL db.indexes()`` row."""
    fields: set[str] = set()

    types = record.get("types")
    if isinstance(types, dict):
        for field_name, index_type in types.items():
            if "RANGE" in str(index_type).upper():
                fields.add(str(field_name))

    for key in ("properties", "attributes", "fields", "entityfields", "entityFields"):
        raw = record.get(key)
        if isinstance(raw, list):
            fields.update(str(item).strip() for item in raw if str(item).strip())
        elif isinstance(raw, str) and raw.strip():
            fields.add(raw.strip())

    for key in ("query", "signature", "type", "name", "label"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            fields.update(_fields_from_index_text(raw))

    return fields


def _indexed_fields_by_label(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Parse ``CALL db.indexes()`` rows into label -> indexed field names."""
    by_label: dict[str, set[str]] = {}
    for record in records:
        label = str(record.get("label") or "")
        if not label:
            continue
        fields = by_label.setdefault(label, set())
        fields.update(_fields_from_index_record(record))
    return by_label


async def verify_graphiti_indices(driver: FalkorDriver) -> dict[str, Any]:
    """Return a health summary of required FalkorDB range indexes."""
    result = await driver.execute_query("CALL db.indexes()")
    records = result[0] if result else []
    by_label = _indexed_fields_by_label(records)

    entity_fields = by_label.get("Entity", set())
    episodic_fields = by_label.get("Episodic", set())

    missing_entity = sorted(_EXPECTED_ENTITY_INDEX_FIELDS - entity_fields)
    missing_episodic = sorted(_EXPECTED_EPISODIC_INDEX_FIELDS - episodic_fields)
    ok = not missing_entity and not missing_episodic

    summary = {
        "ok": ok,
        "index_count": len(records),
        "entity_indexed_fields": sorted(entity_fields),
        "episodic_indexed_fields": sorted(episodic_fields),
        "missing_entity_fields": missing_entity,
        "missing_episodic_fields": missing_episodic,
    }
    if ok:
        logger.info("graphiti_index_verification_ok summary=%s", summary)
    else:
        logger.warning("graphiti_index_verification_incomplete summary=%s", summary)
    return summary
