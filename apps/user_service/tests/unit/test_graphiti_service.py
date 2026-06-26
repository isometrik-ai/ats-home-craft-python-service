"""Unit tests for Graphiti CRM helpers (no FalkorDB connection)."""

from __future__ import annotations

from libs.shared_utils.graphiti_crm_models import (
    ContactSnapshot,
    CrmMetadata,
    LinkedCompanyRef,
    LinkedLeadRef,
    NoteEntry,
    PhoneEntry,
    WorkHistoryEntry,
    custom_id_for_entity,
    deterministic_entity_uuid,
    falkordb_entity_attributes,
    snapshot_episode_name,
    work_history_entry_display_line,
    work_history_display_lines,
)
from libs.shared_utils.graphiti_service import (
    container_tag_for_organization,
    is_graphiti_configured,
    snapshot_to_synthesis_text,
)


def test_container_tag_for_organization() -> None:
    assert container_tag_for_organization("abc-123") == "org_abc-123"


def test_custom_id_for_entity() -> None:
    assert custom_id_for_entity("contact", "c1") == "crm:contact:c1"


def test_snapshot_episode_name() -> None:
    assert snapshot_episode_name("contact", "c1") == "crm_snapshot:contact:c1"


def test_deterministic_entity_uuid_stable() -> None:
    first = deterministic_entity_uuid("contact", "c1")
    second = deterministic_entity_uuid("contact", "c1")
    assert first == second


def test_snapshot_to_synthesis_text_contact() -> None:
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
    from libs.shared_config.app_settings import SharedAppSettings, GraphitiSettings

    settings = SharedAppSettings()
    settings.graphiti = GraphitiSettings(enabled=False)
    assert is_graphiti_configured(settings) is False


def test_falkordb_entity_attributes_flattens_nested_fields() -> None:
    snapshot = ContactSnapshot(
        crm_id="c1",
        display_name="Jane Doe",
        email="jane@example.com",
        tags=["vip", "legal"],
        phones=[PhoneEntry(phone_number="555", phone_isd_code="+1", label="mobile", is_primary=True)],
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
    from libs.shared_utils.graphiti_snapshot_builders import _custom_fields_from_resolved

    resolved = [{"label": "Insurer", "field_key": "insurer", "value": "ACKO"}]
    fields = _custom_fields_from_resolved(resolved)
    assert len(fields) == 1
    assert fields[0].label == "Insurer"
    assert fields[0].value == "ACKO"


def test_work_history_entry_display_line_current_vs_past() -> None:
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
    lines = work_history_display_lines(
        [
            WorkHistoryEntry(company="SoloCo", current=False),
            WorkHistoryEntry(),
        ]
    )
    assert lines == ["worked at SoloCo"]


def test_snapshot_to_synthesis_text_separates_crm_and_work_history() -> None:
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
