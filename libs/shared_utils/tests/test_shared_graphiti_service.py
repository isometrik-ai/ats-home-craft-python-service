"""Unit tests for Graphiti CRM service helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import libs.shared_utils.graphiti_service as graphiti_module
from libs.shared_utils.graphiti_crm_models import (
    CompanySnapshot,
    ContactSnapshot,
    CrmMetadata,
    LeadSnapshot,
    LinkedCompanyRef,
    LinkedContactRef,
)
from libs.shared_utils.graphiti_service import (
    EntityGraphContext,
    GraphitiCrmService,
    GraphitiSearchHit,
    container_tag_for_organization,
    is_graphiti_configured,
    is_graphiti_initialized,
    snapshot_to_synthesis_text,
)


@pytest.fixture(autouse=True)
def reset_graphiti_state():
    """Reset process-global Graphiti state between tests."""
    graphiti_module._graphiti_state.client = None
    graphiti_module._graphiti_state.driver = None
    yield
    graphiti_module._graphiti_state.client = None
    graphiti_module._graphiti_state.driver = None


def _contact_metadata(**overrides) -> CrmMetadata:
    """Build contact CRM metadata for snapshot fixtures."""
    base = {
        "entity_type": "contact",
        "entity_id": "c-1",
        "organization_id": "org-1",
        "status": "active",
        "display_name": "Jane Doe",
        "primary_email": "jane@example.com",
        "updated_at": 1_700_000_000,
    }
    base.update(overrides)
    return CrmMetadata(**base)


def test_container_tag_for_organization():
    """Organization ids map to org-scoped group tags."""
    assert container_tag_for_organization("abc-123") == "org_abc-123"


def test_is_graphiti_initialized_false_by_default():
    """Driver is not initialized before startup."""
    assert is_graphiti_initialized() is False


def test_entity_graph_context_supplement_markdown():
    """EntityGraphContext renders emails and edge facts."""
    ctx = EntityGraphContext(
        snapshot=None,
        edge_facts=["Contact linked to Acme"],
        email_bodies=["Subject: Hello\nBody text"],
    )
    text = ctx.supplement_markdown()
    assert "## Inbound emails" in text
    assert "Hello" in text
    assert "## Graph associations" in text
    assert "Acme" in text
    assert ctx.has_data is True


def test_entity_graph_context_empty_has_no_data():
    """Empty context reports has_data=False."""
    ctx = EntityGraphContext(snapshot=None, edge_facts=[], email_bodies=[])
    assert ctx.has_data is False
    assert ctx.supplement_markdown() == ""


def test_snapshot_to_synthesis_text_contact():
    """Contact snapshots render profile sections."""
    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane Doe",
        email="jane@example.com",
        tags=["vip"],
        linked_companies=[
            LinkedCompanyRef(name="Acme", company_id="co-1", is_primary=True),
        ],
        metadata=_contact_metadata(related_company_ids="co-1"),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "# Contact: Jane Doe" in text
    assert "jane@example.com" in text
    assert "Acme" in text


def test_snapshot_to_synthesis_text_company():
    """Company snapshots render industry and description."""
    snapshot = CompanySnapshot(
        crm_id="co-1",
        display_name="Acme Corp",
        industry="Technology",
        description="Widget maker",
        metadata=CrmMetadata(
            entity_type="company",
            entity_id="co-1",
            organization_id="org-1",
            status="active",
            display_name="Acme Corp",
            updated_at=1_700_000_000,
        ),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "# Company: Acme Corp" in text
    assert "Technology" in text


def test_snapshot_to_synthesis_text_lead():
    """Lead snapshots render deal fields."""
    snapshot = LeadSnapshot(
        crm_id="lead-1",
        display_name="Big Deal",
        stage_name="Qualified",
        amount=5000,
        linked_contacts=[
            LinkedContactRef(contact_id="c-1", contact_name="Jane Doe", label="primary"),
        ],
        metadata=CrmMetadata(
            entity_type="lead",
            entity_id="lead-1",
            organization_id="org-1",
            status="open",
            display_name="Big Deal",
            updated_at=1_700_000_000,
        ),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "# Lead: Big Deal" in text
    assert "Qualified" in text


@pytest.mark.asyncio
async def test_graphiti_crm_service_episode_exists(monkeypatch):
    """episode_exists returns True when driver finds a row."""

    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([{"uuid": "ep-1"}], None, None))
    graphiti_module._graphiti_state.driver = driver

    service = GraphitiCrmService()
    exists = await service.episode_exists(group_id="org_org-1", episode_name="crm:contact:c-1")
    assert exists is True


@pytest.mark.asyncio
async def test_graphiti_crm_service_get_entity_graph_context_uninit():
    """Uninitialized driver returns empty graph context."""
    service = GraphitiCrmService()
    ctx = await service.get_entity_graph_context(
        group_id="org_org-1",
        crm_type="contact",
        crm_id="c-1",
    )
    assert ctx.snapshot is None
    assert ctx.edge_facts == []
    assert ctx.email_bodies == []


def test_graphiti_crm_service_resolve_entity_uuid():
    """resolve_entity_uuid is deterministic for CRM ids."""
    service = GraphitiCrmService()
    uuid_a = service.resolve_entity_uuid(crm_type="contact", crm_id="c-1")
    uuid_b = service.resolve_entity_uuid(crm_type="contact", crm_id="c-1")
    assert uuid_a == uuid_b
    assert uuid_a


@pytest.mark.asyncio
async def test_graphiti_crm_service_search_hybrid(monkeypatch):
    """search_hybrid maps Graphiti edges and episodes to hits."""
    edge = MagicMock()
    edge.uuid = "edge-1"
    edge.fact = "  Jane works at Acme  "
    edge.name = "LinkedToCrmCompany"
    edge.group_id = "org_org-1"

    episode = MagicMock()
    episode.uuid = "ep-1"
    episode.content = "Snapshot body"
    episode.episode_metadata = {"crm_entity_type": "contact"}

    mock_graphiti = MagicMock()
    mock_graphiti.search_ = AsyncMock(return_value=MagicMock(edges=[edge], episodes=[episode]))

    service = GraphitiCrmService(graphiti=mock_graphiti)
    hits = await service.search_hybrid(
        query="Jane Acme",
        group_id="org_org-1",
        limit=5,
    )

    assert len(hits) == 2
    assert isinstance(hits[0], GraphitiSearchHit)
    assert hits[0].text == "Jane works at Acme"
    assert hits[1].text == "Snapshot body"


def test_is_graphiti_configured_disabled(monkeypatch):
    """is_graphiti_configured is False when graphiti disabled."""

    class _Cfg:
        enabled = False
        falkor_host = "localhost"
        falkor_database = "crm"

    class _Settings:
        openai_api_key = "sk-test"
        graphiti = _Cfg()

    assert is_graphiti_configured(_Settings()) is False


def test_is_graphiti_configured_enabled():
    """is_graphiti_configured is True when all prerequisites are met."""

    class _Cfg:
        enabled = True
        falkor_host = "localhost"
        falkor_database = "crm"

    class _Settings:
        openai_api_key = "sk-test"
        graphiti = _Cfg()

    assert is_graphiti_configured(_Settings()) is True


def test_canonical_falkor_database():
    """canonical_falkor_database returns configured graph name."""

    class _Cfg:
        falkor_database = "crm_graph"

    class _Settings:
        graphiti = _Cfg()

    assert graphiti_module.canonical_falkor_database(_Settings()) == "crm_graph"


def test_get_graphiti_and_driver_raise_when_uninitialized():
    """Accessors raise RuntimeError before init_graphiti_client."""
    with pytest.raises(RuntimeError, match="not initialized"):
        graphiti_module.get_graphiti()
    with pytest.raises(RuntimeError, match="driver is not initialized"):
        graphiti_module.get_driver()


@pytest.mark.asyncio
async def test_init_graphiti_client_skips_when_not_configured(monkeypatch):
    """init_graphiti_client is a no-op when Graphiti is not configured."""
    monkeypatch.setattr(graphiti_module, "is_graphiti_configured", lambda _cfg=None: False)
    await graphiti_module.init_graphiti_client()
    assert graphiti_module._graphiti_state.client is None


@pytest.mark.asyncio
async def test_init_and_close_graphiti_client(monkeypatch):
    """init_graphiti_client wires client/driver; close clears state."""
    mock_graphiti = MagicMock()
    mock_graphiti.close = AsyncMock()
    mock_driver = MagicMock()

    class _GraphitiCfg:
        enabled = True
        falkor_host = "localhost"
        falkor_port = 6379
        falkor_database = "crm"
        llm_model = "gpt-4"
        llm_small_model = "gpt-4-mini"
        llm_temperature = 0.0
        strict_index_verify = False

    class _Settings:
        openai_api_key = "sk-test"
        graphiti = _GraphitiCfg()

    monkeypatch.setattr(graphiti_module, "is_graphiti_configured", lambda _cfg=None: True)
    monkeypatch.setattr(graphiti_module, "OpenAIClient", lambda config: MagicMock())
    monkeypatch.setattr(graphiti_module, "NullEmbedder", lambda: MagicMock())
    monkeypatch.setattr(graphiti_module, "OpenAIRerankerClient", lambda config: MagicMock())
    monkeypatch.setattr(graphiti_module, "FalkorDriver", lambda **kwargs: mock_driver)
    monkeypatch.setattr(graphiti_module, "Graphiti", lambda **kwargs: mock_graphiti)
    monkeypatch.setattr(
        graphiti_module,
        "ensure_graphiti_indices",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        graphiti_module,
        "verify_graphiti_indices",
        AsyncMock(return_value={"ok": True}),
    )

    await graphiti_module.init_graphiti_client(_Settings())
    assert graphiti_module._graphiti_state.client is mock_graphiti
    assert graphiti_module._graphiti_state.driver is mock_driver

    await graphiti_module.close_graphiti_client()
    mock_graphiti.close.assert_awaited_once()
    assert graphiti_module._graphiti_state.client is None


def test_coerce_string_list_dedupes():
    """_coerce_string_list deduplicates non-empty strings."""
    from libs.shared_utils.graphiti_service import _coerce_string_list

    assert _coerce_string_list(["a", "a", "", "b", None]) == ["a", "b"]
    assert _coerce_string_list("not-a-list") == []


def test_parse_snapshot_from_json_contact_company_lead():
    """_parse_snapshot_from_json validates CRM snapshot JSON by type."""
    from libs.shared_utils.graphiti_service import _parse_snapshot_from_json

    meta = {
        "entity_type": "contact",
        "entity_id": "c-1",
        "organization_id": "org-1",
        "status": "active",
        "display_name": "Jane",
        "updated_at": 1_700_000_000,
    }
    contact_json = json.dumps({"crm_id": "c-1", "metadata": meta})
    parsed = _parse_snapshot_from_json(contact_json, crm_type="contact")
    assert parsed is not None
    assert parsed.crm_id == "c-1"

    assert _parse_snapshot_from_json("", crm_type="contact") is None
    assert _parse_snapshot_from_json("{bad", crm_type="contact") is None


def test_snapshot_to_synthesis_text_contact_full_sections():
    """Contact synthesis includes notes, custom fields, work history, and leads."""
    from libs.shared_utils.graphiti_crm_models import (
        LinkedLeadRef,
        NoteEntry,
        ResolvedCustomField,
        WorkHistoryEntry,
    )

    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane Doe",
        title="CEO",
        status="active",
        enrichment_done=True,
        intake_stage="qualified",
        preferred_language="en",
        tags=["vip"],
        linked_leads=[LinkedLeadRef(name="Deal A", stage_name="Open", amount=1000)],
        work_history=[WorkHistoryEntry(company_name="Acme", title="Engineer")],
        notes=[NoteEntry(title="Note", content="Important")],
        custom_fields=[ResolvedCustomField(label="Tier", field_key="tier", value="Gold")],
        metadata=_contact_metadata(),
    )
    text = snapshot_to_synthesis_text(snapshot)
    assert "Work history" in text
    assert "Linked leads" in text
    assert "Notes" in text
    assert "Custom fields" in text


@pytest.mark.asyncio
async def test_graphiti_crm_service_get_entity_graph_context_with_data():
    """get_entity_graph_context parses snapshot, emails, and edge facts."""
    meta = _contact_metadata()
    snapshot_json = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        metadata=meta,
    ).model_dump_json()

    driver = AsyncMock()
    driver.execute_query = AsyncMock(
        return_value=(
            [
                {
                    "snapshot_content": snapshot_json,
                    "raw_edge_facts": ["Linked to Acme", "Linked to Acme"],
                    "raw_email_bodies": ["Email body"],
                }
            ],
            None,
            None,
        )
    )
    graphiti_module._graphiti_state.driver = driver

    service = GraphitiCrmService()
    ctx = await service.get_entity_graph_context(
        group_id="org_org-1",
        crm_type="contact",
        crm_id="c-1",
    )
    assert ctx.snapshot is not None
    assert ctx.edge_facts == ["Linked to Acme"]
    assert ctx.email_bodies == ["Email body"]


@pytest.mark.asyncio
async def test_graphiti_crm_service_get_entity_graph_context_query_error():
    """Driver errors return empty context without raising."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(side_effect=RuntimeError("db down"))
    graphiti_module._graphiti_state.driver = driver

    ctx = await GraphitiCrmService().get_entity_graph_context(
        group_id="org_org-1",
        crm_type="contact",
        crm_id="c-1",
    )
    assert ctx.snapshot is None
    assert ctx.has_data is False


@pytest.mark.asyncio
async def test_graphiti_crm_service_upsert_snapshot_episode(monkeypatch):
    """upsert_snapshot_episode persists episodic node via driver."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([], None, None))
    graphiti_module._graphiti_state.driver = driver

    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        metadata=_contact_metadata(),
    )
    service = GraphitiCrmService()
    episode_uuid = await service.upsert_snapshot_episode(
        group_id="org_org-1",
        snapshot=snapshot,
    )
    assert episode_uuid
    driver.execute_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_graphiti_crm_service_upsert_entity_node(monkeypatch):
    """upsert_entity_node writes entity node without embeddings."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([], None, None))
    graphiti_module._graphiti_state.driver = driver

    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        metadata=_contact_metadata(),
    )
    entity_uuid = await GraphitiCrmService().upsert_entity_node(
        group_id="org_org-1",
        snapshot=snapshot,
    )
    assert entity_uuid
    driver.execute_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_graphiti_crm_service_upsert_association_edges_contact():
    """Contact snapshots create LinkedToCrmCompany association edges."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([{"uuid": "edge-1"}], None, None))
    graphiti_module._graphiti_state.driver = driver

    mock_graphiti = MagicMock()
    mock_graphiti.nodes.entity.get_by_uuid = AsyncMock(return_value=MagicMock())

    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        linked_companies=[
            LinkedCompanyRef(name="Acme", company_id="co-1", is_primary=True),
        ],
        metadata=_contact_metadata(related_company_ids="co-1"),
    )
    await GraphitiCrmService(graphiti=mock_graphiti).upsert_association_edges(
        group_id="org_org-1",
        snapshot=snapshot,
    )
    assert driver.execute_query.await_count >= 1


@pytest.mark.asyncio
async def test_graphiti_crm_service_upsert_association_edges_lead():
    """Lead snapshots create OwnsLead association edges."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([{"uuid": "edge-1"}], None, None))
    graphiti_module._graphiti_state.driver = driver

    mock_graphiti = MagicMock()
    mock_graphiti.nodes.entity.get_by_uuid = AsyncMock(return_value=MagicMock())

    snapshot = LeadSnapshot(
        crm_id="lead-1",
        display_name="Big Deal",
        linked_contacts=[
            LinkedContactRef(contact_id="c-1", contact_name="Jane", label="primary"),
        ],
        linked_companies=[
            LinkedCompanyRef(name="Acme", company_id="co-1"),
        ],
        metadata=CrmMetadata(
            entity_type="lead",
            entity_id="lead-1",
            organization_id="org-1",
            status="open",
            display_name="Big Deal",
            updated_at=1_700_000_000,
        ),
    )
    await GraphitiCrmService(graphiti=mock_graphiti).upsert_association_edges(
        group_id="org_org-1",
        snapshot=snapshot,
    )
    assert driver.execute_query.await_count >= 2


@pytest.mark.asyncio
async def test_graphiti_crm_service_sync_snapshot():
    """sync_snapshot runs episode, entity, and edge upserts."""
    service = GraphitiCrmService()
    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        metadata=_contact_metadata(related_company_ids=""),
    )
    service.upsert_snapshot_episode = AsyncMock(return_value="ep-1")
    service.upsert_entity_node = AsyncMock(return_value="ent-1")
    service.upsert_association_edges = AsyncMock()

    await service.sync_snapshot(group_id="org_org-1", snapshot=snapshot)

    service.upsert_snapshot_episode.assert_awaited_once()
    service.upsert_entity_node.assert_awaited_once()
    service.upsert_association_edges.assert_awaited_once()


@pytest.mark.asyncio
async def test_graphiti_crm_service_add_text_episode():
    """add_text_episode saves episodic node and MENTIONS edge."""
    driver = AsyncMock()
    driver.execute_query = AsyncMock(return_value=([{"uuid": "edge-1"}], None, None))
    graphiti_module._graphiti_state.driver = driver

    await GraphitiCrmService().add_text_episode(
        name="email_msg-1",
        body="Hello",
        group_id="org_org-1",
        reference_time=datetime(2026, 1, 1, tzinfo=UTC),
        source_description="inbound email",
        contact_crm_id="c-1",
    )
    assert driver.execute_query.await_count == 2


@pytest.mark.asyncio
async def test_graphiti_crm_service_get_snapshot_episode():
    """get_snapshot_episode delegates to get_entity_graph_context."""
    snapshot = ContactSnapshot(
        crm_id="c-1",
        display_name="Jane",
        metadata=_contact_metadata(),
    )
    service = GraphitiCrmService()
    service.get_entity_graph_context = AsyncMock(
        return_value=EntityGraphContext(snapshot=snapshot, edge_facts=[], email_bodies=[])
    )
    result = await service.get_snapshot_episode(
        group_id="org_org-1",
        crm_type="contact",
        crm_id="c-1",
    )
    assert result is snapshot


@pytest.mark.asyncio
async def test_graphiti_crm_service_search_hybrid_skips_empty():
    """search_hybrid skips edges/episodes with empty text."""
    edge = MagicMock(uuid="e1", fact="   ", name="n", group_id="g")
    episode = MagicMock(uuid="ep1", content="", episode_metadata={})
    mock_graphiti = MagicMock()
    mock_graphiti.search_ = AsyncMock(return_value=MagicMock(edges=[edge], episodes=[episode]))

    hits = await GraphitiCrmService(graphiti=mock_graphiti).search_hybrid(
        query="x",
        group_id="org_org-1",
    )
    assert hits == []
