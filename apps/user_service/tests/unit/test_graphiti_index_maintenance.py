"""Unit tests for Graphiti index maintenance helpers."""

from __future__ import annotations

import pytest

from libs.shared_utils.graphiti_index_maintenance import (
    CRM_SUPPLEMENTAL_RANGE_INDICES,
    _indexed_fields_by_label,
    ensure_crm_supplemental_indices,
    ensure_graphiti_indices,
    verify_graphiti_indices,
)


def test_crm_supplemental_indices_fields() -> None:
    """Supplemental indices should cover CRM lookup fields."""
    joined = "\n".join(CRM_SUPPLEMENTAL_RANGE_INDICES)
    assert "group_id" in joined
    assert "crm_id" in joined
    assert "crm_entity_type" in joined
    assert "Episodic" in joined
    assert "Entity" in joined
    assert "CREATE INDEX FOR (n:Entity) ON (n.crm_id)" in CRM_SUPPLEMENTAL_RANGE_INDICES
    assert "CREATE INDEX FOR (n:Episodic) ON (n.name)" in CRM_SUPPLEMENTAL_RANGE_INDICES


def test_indexed_fields_by_label_parses_range_indexes() -> None:
    """Range index metadata should be parsed into field sets by label."""
    records = [
        {
            "label": "Entity",
            "entitytype": "NODE",
            "types": {"group_id": "RANGE", "crm_id": "RANGE", "uuid": "RANGE"},
        },
        {
            "label": "Episodic",
            "entitytype": "NODE",
            "types": {"group_id": "RANGE", "name": "RANGE"},
        },
    ]
    by_label = _indexed_fields_by_label(records)
    assert by_label["Entity"] == {"group_id", "crm_id", "uuid"}
    assert by_label["Episodic"] == {"group_id", "name"}


def test_indexed_fields_parses_composite() -> None:
    """Composite indexes may expose fields via properties/query rather than types."""
    records = [
        {
            "label": "Entity",
            "types": {"group_id": "RANGE", "name": "RANGE", "uuid": "RANGE"},
            "properties": ["group_id", "crm_id", "crm_entity_type"],
            "query": "CREATE INDEX FOR (n:Entity) ON (n.crm_id)",
        },
        {
            "label": "Episodic",
            "types": {"group_id": "RANGE", "uuid": "RANGE", "valid_at": "RANGE"},
            "query": "CREATE INDEX FOR (n:Episodic) ON (n.group_id, n.name)",
        },
    ]
    by_label = _indexed_fields_by_label(records)
    assert "crm_id" in by_label["Entity"]
    assert "name" in by_label["Episodic"]


@pytest.mark.asyncio
async def test_ensure_crm_supplemental_runs_all() -> None:
    """All supplemental index queries should be executed."""
    queries: list[str] = []

    class _FakeDriver:
        """Driver stub that records executed index queries."""

        async def execute_query(
            self, query: str, **_kwargs: object
        ) -> tuple[list[object], list[str], None]:
            """Record and acknowledge supplemental index queries."""
            queries.append(query)
            return [], [], None

    count = await ensure_crm_supplemental_indices(_FakeDriver())  # type: ignore[arg-type]
    assert count == len(CRM_SUPPLEMENTAL_RANGE_INDICES)
    assert queries == list(CRM_SUPPLEMENTAL_RANGE_INDICES)


@pytest.mark.asyncio
async def test_ensure_graphiti_indices_core_and_sup() -> None:
    """Core and supplemental index setup should both run."""

    class _FakeGraphiti:
        """Graphiti stub that records core index build."""

        async def build_indices_and_constraints(self) -> None:
            """Record core Graphiti index build."""
            calls.append("core")

    class _FakeDriver:
        """Driver stub that records supplemental index execution."""

        async def execute_query(
            self, _query: str, **_kwargs: object
        ) -> tuple[list[object], list[str], None]:
            """Record supplemental index execution."""
            calls.append("supplement")
            return [], [], None

    calls: list[str] = []
    await ensure_graphiti_indices(_FakeGraphiti(), driver=_FakeDriver())  # type: ignore[arg-type]
    assert calls[0] == "core"
    assert calls.count("supplement") == len(CRM_SUPPLEMENTAL_RANGE_INDICES)


@pytest.mark.asyncio
async def test_verify_indices_ok_required_fields() -> None:
    """Verification should pass when required indexed fields are present."""

    class _FakeDriver:
        """Driver stub returning complete index metadata."""

        async def execute_query(
            self, query: str, **_kwargs: object
        ) -> tuple[list[dict], list[str], None]:
            """Return complete FalkorDB index metadata."""
            assert query == "CALL db.indexes()"
            return (
                [
                    {
                        "label": "Entity",
                        "types": {
                            "group_id": "RANGE",
                            "crm_id": "RANGE",
                            "uuid": "RANGE",
                            "name": "RANGE",
                        },
                    },
                    {
                        "label": "Episodic",
                        "types": {"group_id": "RANGE", "name": "RANGE", "uuid": "RANGE"},
                    },
                ],
                [],
                None,
            )

    summary = await verify_graphiti_indices(_FakeDriver())  # type: ignore[arg-type]
    assert summary["ok"] is True
    assert summary["missing_entity_fields"] == []
    assert summary["missing_episodic_fields"] == []


@pytest.mark.asyncio
async def test_verify_graphiti_indices_reports_missing_fields() -> None:
    """Verification should report missing required indexed fields."""

    class _FakeDriver:
        """Driver stub returning incomplete index metadata."""

        async def execute_query(
            self, _query: str, **_kwargs: object
        ) -> tuple[list[dict], list[str], None]:
            """Return incomplete FalkorDB index metadata."""
            return [{"label": "Entity", "types": {"uuid": "RANGE"}}], [], None

    summary = await verify_graphiti_indices(_FakeDriver())  # type: ignore[arg-type]
    assert summary["ok"] is False
    assert "group_id" in summary["missing_entity_fields"]
    assert "crm_id" in summary["missing_entity_fields"]


@pytest.mark.asyncio
async def test_verify_indices_ok_falkordb_fields() -> None:
    """Regression: FalkorDB may omit crm_id/name from types until single-field indexes exist."""

    class _FakeDriver:
        """Driver stub returning FalkorDB-like index metadata."""

        async def execute_query(
            self, _query: str, **_kwargs: object
        ) -> tuple[list[dict], list[str], None]:
            """Return FalkorDB-like index metadata with query hints."""
            return (
                [
                    {
                        "label": "Entity",
                        "types": {
                            "created_at": "RANGE",
                            "group_id": "RANGE",
                            "name": "RANGE",
                            "uuid": "RANGE",
                        },
                        "query": "CREATE INDEX FOR (n:Entity) ON (n.crm_id)",
                    },
                    {
                        "label": "Episodic",
                        "types": {
                            "created_at": "RANGE",
                            "group_id": "RANGE",
                            "uuid": "RANGE",
                            "valid_at": "RANGE",
                        },
                        "query": "CREATE INDEX FOR (n:Episodic) ON (n.name)",
                    },
                ],
                [],
                None,
            )

    summary = await verify_graphiti_indices(_FakeDriver())  # type: ignore[arg-type]
    assert summary["ok"] is True
