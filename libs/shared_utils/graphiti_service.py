"""Graphiti + FalkorDB client bootstrap and structured CRM graph operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.llm_client import LLMConfig, OpenAIClient
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.utils.datetime_utils import utc_now

from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.graphiti_crm_models import (
    CompanySnapshot,
    ContactSnapshot,
    CrmEntityType,
    CrmSnapshot,
    LeadSnapshot,
    custom_id_for_entity,
    deterministic_association_edge_uuid,
    deterministic_entity_uuid,
    deterministic_episode_uuid,
    entity_label_for_crm_type,
    falkordb_entity_attributes,
    snapshot_episode_name,
    work_history_display_lines,
)
from libs.shared_utils.graphiti_index_maintenance import (
    ensure_graphiti_indices,
    verify_graphiti_indices,
)
from libs.shared_utils.graphiti_noop_embedder import NullEmbedder
from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_service")


@dataclass(slots=True)
class _GraphitiState:
    """Process-global Graphiti client and FalkorDB driver."""

    client: Graphiti | None = None
    driver: FalkorDriver | None = None


_graphiti_state = _GraphitiState()


def container_tag_for_organization(organization_id: str) -> str:
    """Graphiti ``group_id`` scoped to one CRM tenant."""
    return f"org_{organization_id}"


def is_graphiti_configured(settings: SharedAppSettings | None = None) -> bool:
    """Return whether Graphiti API calls are allowed for this process."""
    cfg = settings or shared_settings
    graphiti_cfg = cfg.graphiti
    api_key = (cfg.openai_api_key or "").strip()
    return bool(graphiti_cfg.enabled and api_key and graphiti_cfg.falkor_host.strip())


def is_graphiti_initialized() -> bool:
    """Return True when the process-global Graphiti driver has been created."""
    return _graphiti_state.driver is not None


def _llm_config(
    *,
    api_key: str,
    model: str,
    small_model: str,
    temperature: float,
) -> LLMConfig:
    """Build the OpenAI LLM config shared by Graphiti clients."""
    return LLMConfig(
        api_key=api_key,
        model=model,
        temperature=temperature,
        small_model=small_model,
    )


async def init_graphiti_client(settings: SharedAppSettings | None = None) -> None:
    """Eagerly create the Graphiti client at application startup."""
    if _graphiti_state.client is not None:
        return

    cfg = settings or shared_settings
    if not is_graphiti_configured(cfg):
        logger.info("graphiti_client_init_skipped_not_configured")
        return

    graphiti_cfg = cfg.graphiti
    api_key = (cfg.openai_api_key or "").strip()

    llm = _llm_config(
        api_key=api_key,
        model=graphiti_cfg.llm_model,
        small_model=graphiti_cfg.llm_small_model,
        temperature=graphiti_cfg.llm_temperature,
    )
    client = OpenAIClient(config=llm)
    embedder = NullEmbedder()
    reranker = OpenAIRerankerClient(config=llm)
    driver = FalkorDriver(
        host=graphiti_cfg.falkor_host,
        port=graphiti_cfg.falkor_port,
        database=graphiti_cfg.falkor_database,
    )
    graphiti_client = Graphiti(
        graph_driver=driver,
        llm_client=client,
        embedder=embedder,
        cross_encoder=reranker,
        store_raw_episode_content=True,
    )
    await ensure_graphiti_indices(graphiti_client, driver=driver)
    index_summary = await verify_graphiti_indices(driver)
    if graphiti_cfg.strict_index_verify and not index_summary.get("ok"):
        raise RuntimeError(f"Graphiti index verification failed: {index_summary}")
    if not index_summary.get("ok"):
        logger.warning(
            "graphiti_index_verification_incomplete_at_startup summary=%s", index_summary
        )
    _graphiti_state.driver = driver
    _graphiti_state.client = graphiti_client
    logger.info(
        "graphiti_client_initialized database=%s",
        graphiti_cfg.falkor_database,
    )


def get_graphiti() -> Graphiti:
    """Return the process-global Graphiti instance."""
    if _graphiti_state.client is None:
        raise RuntimeError(
            "Graphiti is not initialized "
            "(set GRAPHITI_ENABLED=true and call init_graphiti_client())"
        )
    return _graphiti_state.client


def get_driver() -> FalkorDriver:
    """Return the process-global FalkorDB driver."""
    if _graphiti_state.driver is None:
        raise RuntimeError("Graphiti driver is not initialized")
    return _graphiti_state.driver


async def close_graphiti_client() -> None:
    """Close and clear the cached Graphiti client."""
    if _graphiti_state.client is not None:
        await _graphiti_state.client.close()
    _graphiti_state.client = None
    _graphiti_state.driver = None
    logger.info("graphiti_client_closed")


@dataclass(slots=True)
class GraphitiSearchHit:
    """One row from Graphiti hybrid search."""

    id: str
    text: str
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class EntityGraphContext:
    """Entity-scoped graph payload loaded in one FalkorDB round trip."""

    snapshot: CrmSnapshot | None
    edge_facts: list[str]
    email_bodies: list[str]

    def supplement_markdown(self) -> str:
        """Render inbound emails and association facts for LLM context."""
        sections: list[str] = []
        if self.email_bodies:
            sections.append("## Inbound emails\n" + "\n\n".join(self.email_bodies))
        if self.edge_facts:
            sections.append(
                "## Graph associations\n" + "\n".join(f"- {fact}" for fact in self.edge_facts)
            )
        return "\n\n".join(sections)

    @property
    def has_data(self) -> bool:
        """Return True when any snapshot, email, or edge fact is present."""
        return self.snapshot is not None or bool(self.email_bodies) or bool(self.edge_facts)


_ENTITY_GRAPH_CONTEXT_QUERY = """
OPTIONAL MATCH (snap:Episodic {uuid: $snapshot_episode_uuid})
WHERE snap IS NULL OR snap.group_id = $group_id
OPTIONAL MATCH (n:Entity {uuid: $entity_uuid})
WHERE n IS NULL OR n.group_id = $group_id
OPTIONAL MATCH (n)-[rel:RELATES_TO]-(:Entity)
WHERE rel IS NULL OR rel.group_id = $group_id
WITH snap.content AS snapshot_content, collect(DISTINCT rel.fact) AS raw_edge_facts
OPTIONAL MATCH (mail:Episodic)
WHERE mail.group_id = $group_id
  AND mail.name STARTS WITH 'email_'
  AND mail.content CONTAINS $entity_marker
WITH snapshot_content, raw_edge_facts, mail.content AS email_content, mail.valid_at AS email_valid_at
ORDER BY email_valid_at ASC
WITH snapshot_content, raw_edge_facts, collect(DISTINCT email_content) AS raw_email_bodies
RETURN snapshot_content, raw_edge_facts, raw_email_bodies
"""


def _entity_label_clause(node: EntityNode) -> str:
    """Return FalkorDB label clause for a CRM entity node (e.g. ``Contact:Entity``)."""
    labels = list(set(node.labels + ["Entity"]))
    return ":".join(labels)


async def _save_entity_without_embedding(driver: FalkorDriver, node: EntityNode) -> None:
    """Persist an entity node without computing or storing ``name_embedding``."""
    entity_data: dict[str, Any] = {
        "uuid": node.uuid,
        "name": node.name,
        "group_id": node.group_id,
        "summary": node.summary,
        "created_at": node.created_at,
    }
    entity_data.update(node.attributes or {})
    entity_data.pop("name_embedding", None)

    label_clause = _entity_label_clause(node)
    query = f"""
    MERGE (n:Entity {{uuid: $uuid}})
    SET n:{label_clause}
    SET n = $entity_data
    RETURN n.uuid AS uuid
    """
    await driver.execute_query(query, uuid=node.uuid, entity_data=entity_data)


_ASSOCIATION_EDGE_UPSERT_QUERY = """
MATCH (source:Entity {uuid: $source_uuid})
MATCH (target:Entity {uuid: $target_uuid})
MERGE (source)-[e:RELATES_TO {uuid: $uuid}]->(target)
SET e.name = $name,
    e.fact = $fact,
    e.group_id = $group_id,
    e.created_at = $created_at,
    e.valid_at = $valid_at
RETURN e.uuid AS uuid
"""


async def _upsert_association_edge(
    driver: FalkorDriver,
    graphiti: Graphiti,
    *,
    group_id: str,
    source_uuid: str,
    target_uuid: str,
    edge_name: str,
    fact: str,
    reference_time: datetime,
) -> None:
    """Upsert one CRM association edge without embeddings or LLM deduplication."""
    try:
        await graphiti.nodes.entity.get_by_uuid(source_uuid)
        await graphiti.nodes.entity.get_by_uuid(target_uuid)
    except NodeNotFoundError:
        return

    edge_uuid = deterministic_association_edge_uuid(source_uuid, target_uuid, edge_name)
    await driver.execute_query(
        _ASSOCIATION_EDGE_UPSERT_QUERY,
        source_uuid=source_uuid,
        target_uuid=target_uuid,
        uuid=edge_uuid,
        name=edge_name,
        fact=fact,
        group_id=group_id,
        created_at=utc_now(),
        valid_at=reference_time,
    )


def _coerce_string_list(raw: Any) -> list[str]:
    """Return deduplicated non-empty strings from a FalkorDB list field."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    items: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _parse_snapshot_from_json(
    raw_content: Any,
    *,
    crm_type: CrmEntityType,
) -> CrmSnapshot | None:
    """Parse a CRM snapshot JSON episodic body."""
    content = str(raw_content or "").strip()
    if not content:
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if crm_type == "contact":
        return ContactSnapshot.model_validate(data)
    if crm_type == "company":
        return CompanySnapshot.model_validate(data)
    return LeadSnapshot.model_validate(data)


def _snapshot_crm_type(snapshot: CrmSnapshot) -> CrmEntityType:
    """Return the CRM entity type embedded in snapshot metadata."""
    return snapshot.metadata.entity_type


def _reference_time(snapshot: CrmSnapshot) -> datetime:
    """Resolve the authoritative update timestamp for a snapshot."""
    if snapshot.updated_at_db is not None:
        updated_at = snapshot.updated_at_db
        return updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
    return datetime.fromtimestamp(snapshot.metadata.updated_at, tz=UTC)


def _format_note_lines(notes: list) -> str:
    """Render note entries as a markdown bullet list."""
    return "\n".join(
        f"- {(n.title or '') + ': ' if n.title else ''}{n.content or ''}".strip("- :")
        for n in notes
        if n.title or n.content
    )


def _contact_synthesis_text(snapshot: ContactSnapshot) -> str:
    """Render a contact snapshot as readable synthesis context."""
    header = f"# Contact: {snapshot.display_name or snapshot.crm_id}\n"
    sections: list[str] = [header]
    profile = [
        f"ID: {snapshot.crm_id}",
        *(
            f"{k}: {v}"
            for k, v in [
                ("Email", snapshot.email),
                ("Title", snapshot.title),
                ("Date of birth", snapshot.date_of_birth),
                ("Status", snapshot.status),
                ("Enrichment done", snapshot.enrichment_done),
                ("Intake stage", snapshot.intake_stage),
                ("Preferred language", snapshot.preferred_language),
            ]
            if v is not None and str(v).strip()
        ),
    ]
    sections.append("## Profile\n" + "\n".join(f"- {line}" for line in profile))
    if snapshot.tags:
        sections.append("## Tags\n" + "\n".join(f"- {t}" for t in snapshot.tags))
    if snapshot.linked_companies:
        lines = []
        for company in snapshot.linked_companies:
            primary = " (primary)" if company.is_primary else ""
            industry = f" — {company.industry}" if company.industry else ""
            cid = f" [id: {company.company_id}]" if company.company_id else ""
            name = company.name or company.company_id or "Unknown company"
            lines.append(f"- {name}{primary}{industry}{cid}")
        sections.append(
            "## CRM company associations\n"
            "Linked via contact_companies (real company records in CRM).\n" + "\n".join(lines)
        )
    work_lines = work_history_display_lines(snapshot.work_history)
    if work_lines:
        sections.append(
            "## Work history\n"
            "Employment history only — companies here may not exist as CRM records.\n"
            + "\n".join(f"- {line}" for line in work_lines)
        )
    if snapshot.linked_leads:
        lines = []
        for lead in snapshot.linked_leads:
            bits = [lead.name or "", f"stage: {lead.stage_name}" if lead.stage_name else ""]
            if lead.amount is not None:
                bits.append(f"amount: {lead.amount}")
            lines.append("- " + " — ".join(b for b in bits if b))
        sections.append("## Linked leads\n" + "\n".join(lines))
    if snapshot.notes:
        note_lines = [
            f"- {n.title}: {n.content}" if n.title and n.content else f"- {n.title or n.content}"
            for n in snapshot.notes
            if (n.title or n.content)
        ]
        sections.append("## Notes\n" + "\n".join(note_lines))
    if snapshot.custom_fields:
        cf_lines = [
            f"- {cf.label or cf.field_key}: {cf.value}"
            for cf in snapshot.custom_fields
            if cf.label or cf.field_key
        ]
        sections.append("## Custom fields\n" + "\n".join(cf_lines))
    return "\n\n".join(sections)


def _company_synthesis_text(snapshot: CompanySnapshot) -> str:
    """Render a company snapshot as readable synthesis context."""
    header = f"# Company: {snapshot.display_name or snapshot.crm_id}\n"
    sections = [header, "## Profile"]
    for label, val in [
        ("ID", snapshot.crm_id),
        ("Industry", snapshot.industry),
        ("Email", snapshot.email),
        ("Status", snapshot.status),
        ("Description", snapshot.description),
    ]:
        if val:
            sections.append(f"- {label}: {val}")
    if snapshot.notes:
        sections.append("## Notes\n" + _format_note_lines(snapshot.notes))
    return "\n\n".join(sections)


def _lead_synthesis_text(snapshot: LeadSnapshot) -> str:
    """Render a lead snapshot as readable synthesis context."""
    header = f"# Lead: {snapshot.display_name or snapshot.crm_id}\n"
    deal_lines = [f"ID: {snapshot.crm_id}"]
    for label, val in [
        ("Stage", snapshot.stage_name or snapshot.stage_id),
        ("Priority", snapshot.priority),
        (
            "Amount",
            f"{snapshot.amount} {snapshot.currency or ''}".strip() if snapshot.amount else None,
        ),
        ("Owner", snapshot.owner_name or snapshot.owner_id),
        ("Close date", snapshot.close_date),
        ("Lead score", snapshot.lead_score),
    ]:
        if val is not None and str(val).strip():
            deal_lines.append(f"{label}: {val}")
    sections = [header, "## Deal\n" + "\n".join(f"- {line}" for line in deal_lines)]
    if snapshot.description:
        sections.append(f"## Description\n{snapshot.description}")
    if snapshot.notes:
        sections.append("## Notes\n" + _format_note_lines(snapshot.notes))
    return "\n\n".join(sections)


def snapshot_to_synthesis_text(snapshot: CrmSnapshot) -> str:
    """Render a CRM snapshot as readable context for org-memory synthesis."""
    if isinstance(snapshot, ContactSnapshot):
        return _contact_synthesis_text(snapshot)
    if isinstance(snapshot, CompanySnapshot):
        return _company_synthesis_text(snapshot)
    return _lead_synthesis_text(snapshot)


class GraphitiCrmService:
    """CRM graph operations: JSON snapshots, entity nodes, and edges without vector embeddings."""

    def __init__(self, graphiti: Graphiti | None = None) -> None:
        self._graphiti_override = graphiti

    @property
    def _graphiti(self) -> Graphiti:
        """Return the injected or process-global Graphiti client."""
        if self._graphiti_override is not None:
            return self._graphiti_override
        return get_graphiti()

    @property
    def is_configured(self) -> bool:
        """Return whether Graphiti is configured for API calls."""
        return is_graphiti_configured()

    async def upsert_snapshot_episode(
        self,
        *,
        group_id: str,
        snapshot: CrmSnapshot,
    ) -> str:
        """Persist canonical JSON snapshot without LLM extraction."""
        crm_type = _snapshot_crm_type(snapshot)
        crm_id = snapshot.crm_id
        name = snapshot_episode_name(crm_type, crm_id)
        episode_uuid = deterministic_episode_uuid(name)
        ref_time = _reference_time(snapshot)
        now = utc_now()

        episode = EpisodicNode(
            uuid=episode_uuid,
            name=name,
            group_id=group_id,
            labels=[],
            source=EpisodeType.json,
            source_description="CRM canonical snapshot",
            content=snapshot.model_dump_json(),
            valid_at=ref_time,
            created_at=now,
            episode_metadata=snapshot.metadata.model_dump(),
        )
        await self._graphiti.nodes.episode.save(episode)
        return episode_uuid

    async def upsert_entity_node(self, *, group_id: str, snapshot: CrmSnapshot) -> str:
        """Upsert deterministic CRM entity node with FalkorDB-safe attributes."""
        crm_type = _snapshot_crm_type(snapshot)
        entity_uuid = deterministic_entity_uuid(crm_type, snapshot.crm_id)
        label = entity_label_for_crm_type(crm_type)
        display = snapshot.display_name or snapshot.crm_id

        node = EntityNode(
            uuid=entity_uuid,
            name=display,
            group_id=group_id,
            labels=[label],
            created_at=utc_now(),
            summary="",
            attributes=falkordb_entity_attributes(snapshot),
        )
        await _save_entity_without_embedding(get_driver(), node)
        return entity_uuid

    async def upsert_association_edges(
        self,
        *,
        group_id: str,
        snapshot: CrmSnapshot,
    ) -> None:
        """Create association edges from snapshot linkage fields."""
        ref_time = _reference_time(snapshot)
        driver = get_driver()
        graphiti = self._graphiti

        if isinstance(snapshot, ContactSnapshot):
            contact_uuid = deterministic_entity_uuid("contact", snapshot.crm_id)
            contact_name = snapshot.display_name or snapshot.crm_id
            # CRM contact_companies only — never work_history company names.
            company_ids = [
                cid.strip()
                for cid in snapshot.metadata.related_company_ids.split(",")
                if cid.strip()
            ]
            company_names = {
                (c.company_id or "").strip(): c.name
                for c in snapshot.linked_companies
                if (c.company_id or "").strip()
            }
            for company_id in company_ids:
                company_uuid = deterministic_entity_uuid("company", company_id)
                company_label = company_names.get(company_id) or company_id
                primary_ref = next(
                    (c for c in snapshot.linked_companies if c.company_id == company_id),
                    None,
                )
                primary_note = (
                    " (primary CRM link)" if primary_ref and primary_ref.is_primary else ""
                )
                await _upsert_association_edge(
                    driver,
                    graphiti,
                    group_id=group_id,
                    source_uuid=contact_uuid,
                    target_uuid=company_uuid,
                    edge_name="LinkedToCrmCompany",
                    fact=(
                        f"{contact_name} is associated with CRM company "
                        f"{company_label} ({company_id}){primary_note}"
                    ),
                    reference_time=ref_time,
                )
            return

        if isinstance(snapshot, LeadSnapshot):
            lead_uuid = deterministic_entity_uuid("lead", snapshot.crm_id)
            lead_name = snapshot.display_name or snapshot.crm_id
            for contact in snapshot.linked_contacts:
                cid = (contact.contact_id or "").strip()
                if not cid:
                    continue
                contact_uuid = deterministic_entity_uuid("contact", cid)
                label = contact.label or "associated"
                await _upsert_association_edge(
                    driver,
                    graphiti,
                    group_id=group_id,
                    source_uuid=lead_uuid,
                    target_uuid=contact_uuid,
                    edge_name="OwnsLead",
                    fact=(
                        f"Lead {lead_name} is associated with contact "
                        f"{contact.contact_name or cid} ({label})"
                    ),
                    reference_time=ref_time,
                )
            for company in snapshot.linked_companies:
                cid = (company.company_id or "").strip()
                if not cid:
                    continue
                company_uuid = deterministic_entity_uuid("company", cid)
                await _upsert_association_edge(
                    driver,
                    graphiti,
                    group_id=group_id,
                    source_uuid=lead_uuid,
                    target_uuid=company_uuid,
                    edge_name="OwnsLead",
                    fact=f"Lead {lead_name} is associated with company {company.name or cid}",
                    reference_time=ref_time,
                )

    async def sync_snapshot(self, *, group_id: str, snapshot: CrmSnapshot) -> None:
        """Full structured sync: JSON episode + entity node + association edges."""
        await self.upsert_snapshot_episode(group_id=group_id, snapshot=snapshot)
        await self.upsert_entity_node(group_id=group_id, snapshot=snapshot)
        await self.upsert_association_edges(group_id=group_id, snapshot=snapshot)

    async def episode_exists(self, *, group_id: str, episode_name: str) -> bool:
        """Return True when an episodic node with *episode_name* exists."""
        episode_uuid = deterministic_episode_uuid(episode_name)
        try:
            episode = await self._graphiti.nodes.episode.get_by_uuid(episode_uuid)
        except NodeNotFoundError:
            return False
        return episode.group_id == group_id

    async def add_text_episode(
        self,
        *,
        name: str,
        body: str,
        group_id: str,
        reference_time: datetime,
        source_description: str,
    ) -> None:
        """Persist inbound email text as a structured episodic node (no LLM/embeddings)."""
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=UTC)
        episode_uuid = deterministic_episode_uuid(name)
        episode = EpisodicNode(
            uuid=episode_uuid,
            name=name,
            group_id=group_id,
            labels=[],
            source=EpisodeType.text,
            source_description=source_description,
            content=body,
            valid_at=reference_time,
            created_at=utc_now(),
            episode_metadata={},
        )
        await self._graphiti.nodes.episode.save(episode)

    async def get_snapshot_episode(
        self,
        *,
        group_id: str,
        crm_type: CrmEntityType,
        crm_id: str,
    ) -> CrmSnapshot | None:
        """Load latest JSON snapshot episode for a CRM entity."""
        context = await self.get_entity_graph_context(
            group_id=group_id,
            crm_type=crm_type,
            crm_id=crm_id,
        )
        return context.snapshot

    async def get_entity_graph_context(
        self,
        *,
        group_id: str,
        crm_type: CrmEntityType,
        crm_id: str,
    ) -> EntityGraphContext:
        """Load snapshot JSON, inbound emails, and edge facts for one CRM entity.

        Uses a single FalkorDB query scoped by deterministic UUIDs and
        ``crm:{type}:{id}`` markers (no embedding search).
        """
        empty = EntityGraphContext(snapshot=None, edge_facts=[], email_bodies=[])
        if not is_graphiti_initialized():
            return empty

        entity_uuid = deterministic_entity_uuid(crm_type, crm_id)
        snapshot_episode_uuid = deterministic_episode_uuid(snapshot_episode_name(crm_type, crm_id))
        entity_marker = custom_id_for_entity(crm_type, crm_id)

        try:
            driver = get_driver()
            result = await driver.execute_query(
                _ENTITY_GRAPH_CONTEXT_QUERY,
                group_id=group_id,
                entity_uuid=entity_uuid,
                snapshot_episode_uuid=snapshot_episode_uuid,
                entity_marker=entity_marker,
            )
        except Exception:
            logger.warning(
                "graphiti_entity_context_load_failed group_id=%s crm_type=%s crm_id=%s",
                group_id,
                crm_type,
                crm_id,
                exc_info=True,
            )
            return empty

        records = result[0] if result else []
        if not records:
            return empty

        record = records[0]
        if not isinstance(record, dict):
            return empty

        snapshot = _parse_snapshot_from_json(
            record.get("snapshot_content"),
            crm_type=crm_type,
        )
        return EntityGraphContext(
            snapshot=snapshot,
            edge_facts=_coerce_string_list(record.get("raw_edge_facts")),
            email_bodies=_coerce_string_list(record.get("raw_email_bodies")),
        )

    def resolve_entity_uuid(self, *, crm_type: CrmEntityType, crm_id: str) -> str:
        """Return the deterministic Graphiti entity UUID for a CRM record."""
        return deterministic_entity_uuid(crm_type, crm_id)

    async def search_hybrid(
        self,
        *,
        query: str,
        group_id: str,
        center_node_uuid: str | None = None,
        limit: int = 25,
    ) -> list[GraphitiSearchHit]:
        """Hybrid graph search scoped to one organization."""
        config = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
        config.limit = max(1, min(limit, 100))
        results = await self._graphiti.search_(
            query=query,
            config=config,
            group_ids=[group_id],
            center_node_uuid=center_node_uuid,
        )
        hits: list[GraphitiSearchHit] = []
        for edge in results.edges:
            if not edge.fact.strip():
                continue
            hits.append(
                GraphitiSearchHit(
                    id=edge.uuid,
                    text=edge.fact.strip(),
                    metadata={"edge_name": edge.name, "group_id": edge.group_id},
                )
            )
        for episode in results.episodes:
            text = (episode.content or "").strip()
            if not text:
                continue
            meta = episode.episode_metadata if isinstance(episode.episode_metadata, dict) else None
            hits.append(GraphitiSearchHit(id=episode.uuid, text=text, metadata=meta))
        return hits
