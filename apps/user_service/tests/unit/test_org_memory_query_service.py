"""Unit tests for org memory query helpers (no external API calls)."""

from __future__ import annotations

from apps.user_service.app.services.org_memory_query_service import (
    _collapse_hits_by_entity,
    _dedupe_hits,
    _drop_deleted_and_empty,
    _prioritize_intel_sections_in_snapshot,
)
from libs.shared_utils.supermemory_service import SupermemorySearchHit


def test_prioritize_sections_sales_first() -> None:
    """Notes, linked leads, and companies precede profile/skills in synth context."""
    raw = (
        "# Contact: Preet Morbia\n"
        "## Profile\n- Email: preet@example.com\n"
        "## Skills\n- Python\n"
        "## Linked leads\n- Kommerce — stage: Proposal — amount: 50000\n"
        "## Companies\n- Infosys (primary)\n"
        "## Notes\n- Interested in Kommerce project\n"
    )
    ordered = _prioritize_intel_sections_in_snapshot(raw)
    assert ordered.index("## Notes") < ordered.index("## Linked leads")
    assert ordered.index("## Linked leads") < ordered.index("## Companies")
    assert ordered.index("## Companies") < ordered.index("## Profile")
    assert ordered.index("## Profile") < ordered.index("## Skills")


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


def test_collapse_hits_prefers_authoritative_snapshot() -> None:
    """Stale extracted memories are dropped when a full CRM snapshot is present."""
    hits = [
        SupermemorySearchHit(
            id="a",
            text="Rohit Marthak works at Appscrip",
            metadata={"entity_type": "contact", "entity_id": "c1", "updated_at": 1},
        ),
        SupermemorySearchHit(
            id="b",
            text=(
                "# Contact: Rohit Marthak\n## Profile\n- Email: rohit@tcs.co\n## Companies\n- TCS\n"
            ),
            metadata={"entity_type": "contact", "entity_id": "c1", "updated_at": 99},
        ),
    ]
    collapsed = _collapse_hits_by_entity(hits)
    assert len(collapsed) == 1
    assert "TCS" in collapsed[0].text
    assert "works at Appscrip" not in collapsed[0].text


def test_collapse_hits_merges_chunked_snapshots() -> None:
    """Multiple chunks from the same sync generation are combined, not truncated to one."""
    hits = [
        SupermemorySearchHit(
            id="chunk-profile",
            text="# Contact: Preet Morbia\n## Profile\n- Email: preet@hexwireless.com",
            metadata={"entity_type": "contact", "entity_id": "c1", "updated_at": 100},
        ),
        SupermemorySearchHit(
            id="chunk-notes",
            text="## Notes\n- Met at conference\n## Work history\n- Infosys 2020-2024",
            metadata={"entity_type": "contact", "entity_id": "c1", "updated_at": 100},
        ),
        SupermemorySearchHit(
            id="stale-memory",
            text="Preet Morbia works at Appscrip",
            metadata={"entity_type": "contact", "entity_id": "c1", "updated_at": 1},
        ),
    ]
    collapsed = _collapse_hits_by_entity(hits)
    assert len(collapsed) == 1
    text = collapsed[0].text
    assert "preet@hexwireless.com" in text
    assert "Met at conference" in text
    assert "Work history" in text
    assert "works at Appscrip" not in text


def test_collapse_hits_merges_when_no_snapshot() -> None:
    """Short fragments still merge when no authoritative CRM snapshot exists."""
    hits = [
        SupermemorySearchHit(
            id="a",
            text="Rohit Marthak works at Appscrip",
            metadata={"entity_type": "contact", "entity_id": "c1"},
        ),
        SupermemorySearchHit(
            id="b",
            text="Email: rohit@appscrip.co",
            metadata={"entity_type": "contact", "entity_id": "c1"},
        ),
    ]
    collapsed = _collapse_hits_by_entity(hits)
    assert len(collapsed) == 1
    assert "works at Appscrip" in collapsed[0].text
    assert "rohit@appscrip.co" in collapsed[0].text
