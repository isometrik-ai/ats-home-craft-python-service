"""Graphiti + FalkorDB client bootstrap and CRM graph operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.llm_client import LLMConfig, OpenAIClient
from graphiti_core.nodes import EntityNode, EpisodicNode, EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.utils.datetime_utils import utc_now

from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.graphiti_crm_models import (
    EDGE_TYPE_MAP,
    EDGE_TYPES,
    ENTITY_TYPES,
    CompanySnapshot,
    ContactSnapshot,
    CrmEntityType,
    CrmSnapshot,
    LeadSnapshot,
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
from libs.shared_utils.logger import get_logger

logger = get_logger("graphiti_service")

_graphiti_client: Graphiti | None = None
_driver: FalkorDriver | None = None


def container_tag_for_organization(organization_id: str) -> str:
    """Graphiti ``group_id`` scoped to one CRM tenant."""
    return f"org_{organization_id}"


def is_graphiti_configured(settings: SharedAppSettings | None = None) -> bool:
    """Return whether Graphiti API calls are allowed for this process."""
    cfg = settings or shared_settings
    g = cfg.graphiti
    api_key = (cfg.openai_api_key or "").strip()
    return bool(g.enabled and api_key and g.falkor_host.strip())


def is_graphiti_initialized() -> bool:
    """Return True when the process-global Graphiti driver has been created."""
    return _driver is not None


def _llm_config(
    *,
    api_key: str,
    model: str,
    small_model: str,
    temperature: float,
) -> LLMConfig:
    return LLMConfig(
        api_key=api_key,
        model=model,
        temperature=temperature,
        small_model=small_model,
    )


async def init_graphiti_client(settings: SharedAppSettings | None = None) -> None:
    """Eagerly create the Graphiti client at application startup."""
    global _graphiti_client, _driver
    if _graphiti_client is not None:
        return

    cfg = settings or shared_settings
    if not is_graphiti_configured(cfg):
        logger.info("graphiti_client_init_skipped_not_configured")
        return

    g = cfg.graphiti
    api_key = (cfg.openai_api_key or "").strip()

    llm = _llm_config(
        api_key=api_key,
        model=g.llm_model,
        small_model=g.llm_small_model,
        temperature=g.llm_temperature,
    )
    client = OpenAIClient(config=llm)
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(api_key=api_key, embedding_model=g.embedding_model)
    )
    reranker = OpenAIRerankerClient(config=llm)
    _driver = FalkorDriver(
        host=g.falkor_host,
        port=g.falkor_port,
        database=g.falkor_database,
    )
    _graphiti_client = Graphiti(
        graph_driver=_driver,
        llm_client=client,
        embedder=embedder,
        cross_encoder=reranker,
        store_raw_episode_content=True,
    )
    await ensure_graphiti_indices(_graphiti_client, driver=_driver)
    index_summary = await verify_graphiti_indices(_driver)
    if g.strict_index_verify and not index_summary.get("ok"):
        raise RuntimeError(f"Graphiti index verification failed: {index_summary}")
    if not index_summary.get("ok"):
        logger.warning("graphiti_index_verification_incomplete_at_startup summary=%s", index_summary)
    logger.info(
        "graphiti_client_initialized database=%s",
        g.falkor_database,
    )


def get_graphiti() -> Graphiti:
    """Return the process-global Graphiti instance."""
    if _graphiti_client is None:
        raise RuntimeError(
            "Graphiti is not initialized (set GRAPHITI_ENABLED=true and call init_graphiti_client())"
        )
    return _graphiti_client


def get_driver() -> FalkorDriver:
    """Return the process-global FalkorDB driver."""
    if _driver is None:
        raise RuntimeError("Graphiti driver is not initialized")
    return _driver


async def close_graphiti_client() -> None:
    """Close and clear the cached Graphiti client."""
    global _graphiti_client, _driver
    if _graphiti_client is not None:
        await _graphiti_client.close()
    _graphiti_client, _driver = None, None
    logger.info("graphiti_client_closed")


@dataclass(slots=True)
class GraphitiSearchHit:
    """One row from Graphiti hybrid search."""

    id: str
    text: str
    metadata: dict[str, Any] | None = None


def _snapshot_crm_type(snapshot: CrmSnapshot) -> CrmEntityType:
    return snapshot.metadata.entity_type


def _reference_time(snapshot: CrmSnapshot) -> datetime:
    if snapshot.updated_at_db is not None:
        ts = snapshot.updated_at_db
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    return datetime.fromtimestamp(snapshot.metadata.updated_at, tz=UTC)


def snapshot_to_synthesis_text(snapshot: CrmSnapshot) -> str:
    """Render a CRM snapshot as readable context for org-memory synthesis."""
    if isinstance(snapshot, ContactSnapshot):
        header = f"# Contact: {snapshot.display_name or snapshot.crm_id}\n"
        sections: list[str] = [header]
        profile = [
            f"ID: {snapshot.crm_id}",
            *(f"{k}: {v}" for k, v in [
                ("Email", snapshot.email),
                ("Title", snapshot.title),
                ("Date of birth", snapshot.date_of_birth),
                ("Status", snapshot.status),
                ("Enrichment done", snapshot.enrichment_done),
                ("Intake stage", snapshot.intake_stage),
                ("Preferred language", snapshot.preferred_language),
            ] if v is not None and str(v).strip()),
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
                "Linked via contact_companies (real company records in CRM).\n"
                + "\n".join(lines)
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

    if isinstance(snapshot, CompanySnapshot):
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
            sections.append(
                "## Notes\n"
                + "\n".join(
                    f"- {(n.title or '') + ': ' if n.title else ''}{n.content or ''}".strip("- :")
                    for n in snapshot.notes
                    if n.title or n.content
                )
            )
        return "\n\n".join(sections)

    header = f"# Lead: {snapshot.display_name or snapshot.crm_id}\n"
    deal_lines = [f"ID: {snapshot.crm_id}"]
    for label, val in [
        ("Stage", snapshot.stage_name or snapshot.stage_id),
        ("Priority", snapshot.priority),
        ("Amount", f"{snapshot.amount} {snapshot.currency or ''}".strip() if snapshot.amount else None),
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
        sections.append(
            "## Notes\n"
            + "\n".join(
                f"- {(n.title or '') + ': ' if n.title else ''}{n.content or ''}".strip("- :")
                for n in snapshot.notes
                if n.title or n.content
            )
        )
    return "\n\n".join(sections)


class GraphitiCrmService:
    """CRM graph operations on top of the shared Graphiti client."""

    def __init__(self, graphiti: Graphiti | None = None) -> None:
        self._graphiti_override = graphiti

    @property
    def _graphiti(self) -> Graphiti:
        if self._graphiti_override is not None:
            return self._graphiti_override
        return get_graphiti()

    @property
    def is_configured(self) -> bool:
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
        await self._graphiti.nodes.entity.save(node)
        return entity_uuid

    async def upsert_association_edges(
        self,
        *,
        group_id: str,
        snapshot: CrmSnapshot,
    ) -> None:
        """Create association edges from snapshot linkage fields."""
        ref_time = _reference_time(snapshot)

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
                primary_note = " (primary CRM link)" if primary_ref and primary_ref.is_primary else ""
                await self._add_triplet(
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
                await self._add_triplet(
                    group_id=group_id,
                    source_uuid=lead_uuid,
                    target_uuid=contact_uuid,
                    edge_name="OwnsLead",
                    fact=f"Lead {lead_name} is associated with contact {contact.contact_name or cid} ({label})",
                    reference_time=ref_time,
                )
            for company in snapshot.linked_companies:
                cid = (company.company_id or "").strip()
                if not cid:
                    continue
                company_uuid = deterministic_entity_uuid("company", cid)
                await self._add_triplet(
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
        """Ingest unstructured text with LLM entity extraction."""
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=UTC)
        await self._graphiti.add_episode(
            name=name,
            episode_body=body,
            source=EpisodeType.text,
            source_description=source_description,
            reference_time=reference_time,
            group_id=group_id,
            entity_types=ENTITY_TYPES,
            edge_types=EDGE_TYPES,
            edge_type_map=EDGE_TYPE_MAP,
            update_communities=False,
        )

    async def get_snapshot_episode(
        self,
        *,
        group_id: str,
        crm_type: CrmEntityType,
        crm_id: str,
    ) -> CrmSnapshot | None:
        """Load latest JSON snapshot episode for a CRM entity."""
        name = snapshot_episode_name(crm_type, crm_id)
        episode_uuid = deterministic_episode_uuid(name)
        try:
            episode = await self._graphiti.nodes.episode.get_by_uuid(episode_uuid)
        except NodeNotFoundError:
            return None
        if episode.group_id != group_id:
            return None
        try:
            data = json.loads(episode.content)
        except json.JSONDecodeError:
            return None
        if crm_type == "contact":
            return ContactSnapshot.model_validate(data)
        if crm_type == "company":
            return CompanySnapshot.model_validate(data)
        return LeadSnapshot.model_validate(data)

    def resolve_entity_uuid(self, *, crm_type: CrmEntityType, crm_id: str) -> str:
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

    async def _add_triplet(
        self,
        *,
        group_id: str,
        source_uuid: str,
        target_uuid: str,
        edge_name: str,
        fact: str,
        reference_time: datetime,
    ) -> None:
        """Best-effort association edge between two deterministic entity UUIDs."""
        try:
            source = await self._graphiti.nodes.entity.get_by_uuid(source_uuid)
            target = await self._graphiti.nodes.entity.get_by_uuid(target_uuid)
        except NodeNotFoundError:
            return

        edge = EntityEdge(
            group_id=group_id,
            source_node_uuid=source.uuid,
            target_node_uuid=target.uuid,
            created_at=utc_now(),
            name=edge_name,
            fact=fact,
            valid_at=reference_time,
            reference_time=reference_time,
        )
        try:
            await self._graphiti.add_triplet(source, edge, target)
        except Exception:
            logger.warning(
                "graphiti_add_triplet_failed source=%s target=%s edge=%s",
                source_uuid,
                target_uuid,
                edge_name,
                exc_info=True,
            )
