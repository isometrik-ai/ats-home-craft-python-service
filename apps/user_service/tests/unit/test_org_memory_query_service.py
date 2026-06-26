"""Unit tests for org memory query helpers (no external API calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from apps.user_service.app.services.org_memory_query_service import (
    OrgMemoryQueryService,
    _no_data_message,
    _prioritize_intel_sections_in_snapshot,
)


def test_prioritize_sections_sales_first() -> None:
    """Notes, linked leads, and companies precede profile/skills in synth context."""
    raw = (
        "# Contact: Preet Morbia\n"
        "## Profile\n- Email: preet@example.com\n"
        "## Skills\n- Python\n"
        "## Linked leads\n- Kommerce — stage: Proposal — amount: 50000\n"
        "## CRM company associations\n- Infosys (primary)\n"
        "## Notes\n- Interested in Kommerce project\n"
    )
    ordered = _prioritize_intel_sections_in_snapshot(raw)
    assert ordered.index("## Notes") < ordered.index("## Linked leads")
    assert ordered.index("## Linked leads") < ordered.index("## CRM company associations")
    assert ordered.index("## CRM company associations") < ordered.index("## Profile")
    assert ordered.index("## Profile") < ordered.index("## Skills")


def test_no_data_message_by_entity_type() -> None:
    """No-data copy is specific to the requested CRM entity type."""
    assert _no_data_message("contact") == "We don't have data for this contact."
    assert _no_data_message("company") == "We don't have data for this company."
    assert _no_data_message("lead") == "We don't have data for this lead."
    assert _no_data_message(None) == "We don't have data for this record."


@pytest.mark.asyncio
async def test_run_returns_no_data_when_snapshot_missing() -> None:
    """Entity-scoped overview does not call the LLM when no graph context exists."""
    from libs.shared_utils.graphiti_service import EntityGraphContext

    graphiti = AsyncMock()
    graphiti.get_entity_graph_context = AsyncMock(
        return_value=EntityGraphContext(snapshot=None, edge_facts=[], email_bodies=[])
    )
    service = OrgMemoryQueryService(graphiti=graphiti)

    answer = await service.run(
        user_message="overview",
        organization_id="org-1",
        entity_id="contact-1",
        entity_type="contact",
    )

    assert answer == "We don't have data for this contact."
    graphiti.get_entity_graph_context.assert_awaited_once()
    graphiti.search_hybrid.assert_not_called()


@pytest.mark.asyncio
async def test_run_includes_scoped_without_embeddings() -> None:
    """Overview context merges snapshot notes and inbound emails without hybrid search."""
    from libs.shared_utils.graphiti_crm_models import ContactSnapshot, CrmMetadata
    from libs.shared_utils.graphiti_service import EntityGraphContext

    snapshot = ContactSnapshot(
        crm_id="contact-1",
        display_name="Jane Doe",
        email="jane@example.com",
        notes=[],
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="contact-1",
            organization_id="org-1",
            status="active",
            display_name="Jane Doe",
            updated_at=1,
        ),
    )
    graphiti = AsyncMock()
    graphiti.get_entity_graph_context = AsyncMock(
        return_value=EntityGraphContext(
            snapshot=snapshot,
            edge_facts=[],
            email_bodies=["### 2026-01-01 — Hello\nFrom: jane@example.com\nBody"],
        )
    )
    service = OrgMemoryQueryService(graphiti=graphiti)

    with patch(
        "apps.user_service.app.services.org_memory_query_service.create_chat_completion",
        new_callable=AsyncMock,
        return_value="## Overview\nJane Doe",
    ) as mock_llm:
        answer = await service.run(
            user_message="overview",
            organization_id="org-1",
            entity_id="contact-1",
            entity_type="contact",
        )

    assert answer == "## Overview\nJane Doe"
    graphiti.search_hybrid.assert_not_called()
    user_message = mock_llm.await_args.kwargs["messages"][1]["content"]
    assert "Inbound emails" in user_message
    assert "jane@example.com" in user_message


def test_build_notes_context_merges_snapshot() -> None:
    """CRM notes from snapshot and inbound emails from graph supplements are both included."""
    notes, truncated = OrgMemoryQueryService._build_notes_context(
        "# Contact: Jane\n## Notes\n- Follow up Friday",
        "## Inbound emails\n\n### Email — Subject\nBody text",
    )
    assert "Follow up Friday" in notes
    assert "Inbound emails" in notes
    assert "Body text" in notes
    assert truncated is False


@pytest.mark.asyncio
async def test_run_requires_entity_scope() -> None:
    """Requests without entity_id and entity_type return a no-data response."""
    graphiti = AsyncMock()
    service = OrgMemoryQueryService(graphiti=graphiti)

    answer = await service.run(
        user_message="overview",
        organization_id="org-1",
    )

    assert answer == "We don't have data for this record."
    graphiti.get_entity_graph_context.assert_not_called()
