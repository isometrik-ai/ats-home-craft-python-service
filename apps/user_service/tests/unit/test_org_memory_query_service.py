"""Unit tests for org memory query helpers (no external API calls)."""

from __future__ import annotations

from apps.user_service.app.services.org_memory_query_service import (
    _collapse_hits_by_entity,
    _dedupe_hits,
    _drop_deleted_and_empty,
    _parse_intent,
)
from libs.shared_utils.supermemory_service import SupermemorySearchHit


def test_parse_intent_strips_json_fence() -> None:
    """JSON wrapped in markdown fences is parsed into a plan."""
    raw = """```json
{"is_aggregation": false, "search_queries": ["Alice"], "synthesize_instruction": "Summarize"}
```"""
    plan = _parse_intent(raw, fallback_queries=["fallback"])
    assert plan.search_queries == ["Alice"]
    assert plan.is_aggregation is False


def test_parse_intent_fallback_on_bad_json() -> None:
    """Invalid JSON falls back to the user's original query strings."""
    plan = _parse_intent("not json", fallback_queries=["hello", "world"])
    assert plan.search_queries == ["hello", "world"]


def test_dedupe_hits_by_id() -> None:
    """Duplicate hit ids are collapsed while preserving first occurrence."""
    hits = [
        SupermemorySearchHit(id="a", text="one"),
        SupermemorySearchHit(id="a", text="dup"),
        SupermemorySearchHit(id="b", text="two"),
    ]
    assert len(_dedupe_hits(hits)) == 2
    assert _dedupe_hits(hits)[0].text == "one"


def test_drop_deleted_and_empty() -> None:
    """Empty text and deleted metadata status rows are removed."""
    hits = [
        SupermemorySearchHit(id="1", text="ok"),
        SupermemorySearchHit(id="2", text=""),
        SupermemorySearchHit(id="3", text="gone", metadata={"status": "deleted"}),
    ]
    kept = _drop_deleted_and_empty(hits)
    assert len(kept) == 1
    assert kept[0].id == "1"


def test_collapse_hits_by_entity_merges_fragments() -> None:
    """Fragments for the same contact are merged, not replaced by a single short line."""
    hits = [
        SupermemorySearchHit(
            id="a",
            text="Rohit Marthak works at Appscrip",
            metadata={"entity_type": "contact", "entity_id": "c1"},
        ),
        SupermemorySearchHit(
            id="b",
            text="# Contact: Rohit Marthak\nEmail: rohit@appscrip.co\nTitle: Python AI Engineer",
            metadata={"entity_type": "contact", "entity_id": "c1"},
        ),
    ]
    collapsed = _collapse_hits_by_entity(hits)
    assert len(collapsed) == 1
    assert "Title: Python AI Engineer" in collapsed[0].text
    assert "works at Appscrip" in collapsed[0].text
