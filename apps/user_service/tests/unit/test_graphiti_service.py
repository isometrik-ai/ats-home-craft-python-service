"""Unit tests for Graphiti CRM helpers (no FalkorDB connection)."""

from __future__ import annotations

import pytest

from libs.shared_utils.graphiti_crm_models import (
    ContactSnapshot,
    CrmMetadata,
    LinkedCompanyRef,
    LinkedLeadRef,
    NoteEntry,
    PhoneEntry,
    WorkHistoryEntry,
    custom_id_for_entity,
    deterministic_association_edge_uuid,
    deterministic_entity_uuid,
    falkordb_entity_attributes,
    snapshot_episode_name,
    work_history_display_lines,
    work_history_entry_display_line,
)
from libs.shared_utils.graphiti_service import (
    EntityGraphContext,
    _coerce_string_list,
    _parse_snapshot_from_json,
    container_tag_for_organization,
    is_graphiti_configured,
    snapshot_to_synthesis_text,
)


def test_container_tag_for_organization() -> None:
    """Organization tags should be prefixed with org_."""
    assert container_tag_for_organization("abc-123") == "org_abc-123"


def test_custom_id_for_entity() -> None:
    """Custom IDs should follow the crm:type:id format."""
    assert custom_id_for_entity("contact", "c1") == "crm:contact:c1"


def test_snapshot_episode_name() -> None:
    """Snapshot episode names should be stable per entity."""
    assert snapshot_episode_name("contact", "c1") == "crm_snapshot:contact:c1"


def test_deterministic_entity_uuid_stable() -> None:
    """Entity UUIDs should be deterministic for the same CRM id."""
    first = deterministic_entity_uuid("contact", "c1")
    second = deterministic_entity_uuid("contact", "c1")
    assert first == second


def test_deterministic_association_edge_uuid_stable() -> None:
    """Association edge UUIDs should be deterministic for the same endpoints."""
    source = deterministic_entity_uuid("contact", "c1")
    target = deterministic_entity_uuid("company", "co-1")
    first = deterministic_association_edge_uuid(source, target, "LinkedToCrmCompany")
    second = deterministic_association_edge_uuid(source, target, "LinkedToCrmCompany")
    assert first == second


def test_snapshot_to_synthesis_text_contact() -> None:
    """Contact synthesis text should include profile fields."""
    snapshot = ContactSnapshot(
        crm_id="c1",
        display_name="Jane Doe",
        email="jane@example.com",
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="c1",
            organization_id="org-1",
            status="active",
            display_name="Jane Doe",
            updated_at=1,
        ),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "Jane Doe" in text
    assert "jane@example.com" in text


def test_is_graphiti_configured_false_when_disabled() -> None:
    """Graphiti should be unconfigured when disabled in settings."""
    from libs.shared_config.app_settings import GraphitiSettings, SharedAppSettings

    settings = SharedAppSettings()
    settings.graphiti = GraphitiSettings(enabled=False)
    assert is_graphiti_configured(settings) is False


def test_falkordb_attrs_flattens_nested() -> None:
    """Nested snapshot fields should be flattened for FalkorDB."""
    snapshot = ContactSnapshot(
        crm_id="c1",
        display_name="Jane Doe",
        email="jane@example.com",
        tags=["vip", "legal"],
        phones=[
            PhoneEntry(phone_number="555", phone_isd_code="+1", label="mobile", is_primary=True)
        ],
        notes=[NoteEntry(title="Note", content="Follow up")],
        linked_companies=[
            LinkedCompanyRef(company_id="co-1", name="Acme", industry="Tech", is_primary=True)
        ],
        linked_leads=[LinkedLeadRef(lead_id="lead-1", name="Deal", stage_name="Prospect")],
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="c1",
            organization_id="org-1",
            status="active",
            display_name="Jane Doe",
            updated_at=1,
        ),
    )
    attrs = falkordb_entity_attributes(snapshot)

    assert attrs["crm_id"] == "c1"
    assert attrs["tags"] == ["vip", "legal"]
    assert isinstance(attrs["phones_json"], str)
    assert isinstance(attrs["notes_json"], str)
    assert isinstance(attrs["linked_companies_json"], str)
    assert isinstance(attrs["linked_leads_json"], str)
    assert isinstance(attrs["snapshot_json"], str)
    assert "Jane Doe" in attrs["snapshot_json"]
    assert "Follow up" in attrs["snapshot_json"]


def test_custom_fields_from_resolved_dicts() -> None:
    """Resolved custom-field dicts should become snapshot entries."""
    from libs.shared_utils.graphiti_snapshot_builders import (
        _custom_fields_from_resolved,
    )

    resolved = [{"label": "Insurer", "field_key": "insurer", "value": "ACKO"}]
    fields = _custom_fields_from_resolved(resolved)
    assert len(fields) == 1
    assert fields[0].label == "Insurer"
    assert fields[0].value == "ACKO"


def test_work_history_line_current_vs_past() -> None:
    """Work history lines should use works at vs worked at."""
    current = WorkHistoryEntry(
        job_title="Engineer",
        company="Acme",
        start_date="Jan 2023",
        current=True,
    )
    past = WorkHistoryEntry(
        job_title="Analyst",
        company="OldCo",
        start_date="2018",
        end_date="2022",
        current=False,
    )
    assert work_history_entry_display_line(current) == "Engineer works at Acme (Jan 2023)"
    assert work_history_entry_display_line(past) == "Analyst worked at OldCo (2018 – 2022)"


def test_work_history_display_lines_skips_empty() -> None:
    """Empty work-history rows should be omitted from display lines."""
    lines = work_history_display_lines(
        [
            WorkHistoryEntry(company="SoloCo", current=False),
            WorkHistoryEntry(),
        ]
    )
    assert lines == ["worked at SoloCo"]


def test_synthesis_text_separates_crm_work() -> None:
    """Synthesis text should separate CRM companies from work history."""
    snapshot = ContactSnapshot(
        crm_id="c1",
        display_name="Jane Doe",
        linked_companies=[
            LinkedCompanyRef(company_id="co-1", name="Acme CRM", is_primary=True),
        ],
        work_history=[
            WorkHistoryEntry(job_title="Engineer", company="Past Inc", current=False),
            WorkHistoryEntry(job_title="Lead", company="Acme CRM", current=True),
        ],
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="c1",
            organization_id="org-1",
            status="active",
            display_name="Jane Doe",
            updated_at=1,
        ),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "## CRM company associations" in text
    assert "Acme CRM" in text and "co-1" in text
    assert "## Work history" in text
    assert "Engineer worked at Past Inc" in text
    assert "Lead works at Acme CRM" in text
    assert "## Companies" not in text


def test_entity_graph_context_supplement_markdown() -> None:
    """Supplement markdown includes inbound emails and association facts."""
    context = EntityGraphContext(
        snapshot=None,
        edge_facts=["Jane is associated with CRM company Acme (co-1)"],
        email_bodies=["### Email — Hello\nFrom: jane@example.com"],
    )
    markdown = context.supplement_markdown()
    assert "Inbound emails" in markdown
    assert "jane@example.com" in markdown
    assert "Graph associations" in markdown
    assert "Acme" in markdown


def test_coerce_string_list_dedupes() -> None:
    """FalkorDB list fields are normalized to unique non-empty strings."""
    assert _coerce_string_list(["a", "a", "", None, "b"]) == ["a", "b"]


def test_parse_snapshot_from_json_contact() -> None:
    """Snapshot JSON is parsed into a typed contact snapshot."""
    snapshot = ContactSnapshot(
        crm_id="c1",
        display_name="Jane",
        metadata=CrmMetadata(
            entity_type="contact",
            entity_id="c1",
            organization_id="org-1",
            status="active",
            display_name="Jane",
            updated_at=1,
        ),
    )
    parsed = _parse_snapshot_from_json(snapshot.model_dump_json(), crm_type="contact")
    assert parsed is not None
    assert parsed.crm_id == "c1"
    assert parsed.display_name == "Jane"


@pytest.mark.asyncio
async def test_add_text_episode_uses_episodic_without_llm() -> None:
    """Inbound email episodes are saved as structured episodic nodes."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock

    from libs.shared_utils.graphiti_service import GraphitiCrmService

    graphiti = MagicMock()
    graphiti.nodes.episode.save = AsyncMock()
    service = GraphitiCrmService(graphiti=graphiti)

    await service.add_text_episode(
        name="email_msg-1",
        body="Contact: Jane (crm:contact:c1)\n\nHello",
        group_id="org_org-1",
        reference_time=datetime(2026, 1, 1, tzinfo=UTC),
        source_description="Inbound email",
    )

    graphiti.nodes.episode.save.assert_awaited_once()
    episode = graphiti.nodes.episode.save.await_args.args[0]
    assert episode.name == "email_msg-1"
    assert "crm:contact:c1" in episode.content
