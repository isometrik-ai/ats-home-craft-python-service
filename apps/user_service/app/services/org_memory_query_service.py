"""Natural-language CRM Q&A scoped to one org via Graphiti + OpenAI."""

from __future__ import annotations

import asyncio

import asyncpg

from apps.user_service.app.constants.ai_overview_defaults import (
    DEFAULT_OVERVIEW_PROMPTS,
    EntityOverviewType,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.schemas.ai_overview_settings import AiOverviewSettings
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import parse_json_field
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger
from libs.shared_utils.openai_chat_service import create_chat_completion
from libs.shared_utils.graphiti_crm_models import CrmEntityType
from libs.shared_utils.graphiti_service import (
    GraphitiCrmService,
    GraphitiSearchHit,
    container_tag_for_organization,
    snapshot_to_synthesis_text,
)

logger = get_logger("org_memory_query_service")

_INTENT_MAX_TOKENS = 2048
_SYNTH_MAX_TOKENS = 4096
_SYNTH_CONTEXT_CHAR_LIMIT = 14_000
_LOOKUP_SEARCH_LIMIT = 25
_AGGREGATION_SEARCH_LIMIT = 50
_MAX_SYNTH_ENTITY_SNIPPETS = 10
_ENTITY_HEADER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("# Contact:", "contact"),
    ("# Company:", "company"),
    ("# Lead:", "lead"),
)
_STRUCTURED_SECTION_MARKERS = ("## Profile", "## Companies", "## CRM company associations")

# ---------------------------------------------------------------------------
# Hardcoded search query templates
# ---------------------------------------------------------------------------
# Three queries per lookup give broad semantic coverage:
#   1. Identity recall — who the entity is
#   2. Notes / email signals — what has been said and committed
#   3. Leads / company associations — pipeline and org context
# {name} is substituted at runtime with the entity display name or user message.
_DEFAULT_QUERY_TEMPLATES: tuple[str, str, str] = (
    "{name}",
    "{name} notes emails follow-up objections interests business",
    "{name} pipeline stage company association lead",
)

_ENTITY_NAME_PLACEHOLDER = "{{entity_name}}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_overview_entity_type(entity_type: str | None) -> EntityOverviewType:
    """Map request entity_type to the overview prompt key (defaults to contact)."""
    normalized = (entity_type or "").strip().lower()
    if normalized in ("lead", "company", "contact"):
        return normalized  # type: ignore[return-value]
    return "contact"


def _overview_prompt_template(
    overview_settings: AiOverviewSettings,
    entity_type: str | None,
) -> str:
    """Return stored prompt for ``entity_type``, or the platform default when missing."""
    key = _resolve_overview_entity_type(entity_type)
    stored = getattr(overview_settings.overview_prompts, key, "")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return DEFAULT_OVERVIEW_PROMPTS[key]


def _build_synth_system_prompt(
    *,
    entity_type: str | None,
    entity_name: str,
    overview_settings: AiOverviewSettings,
) -> str:
    """Org-specific AI Overview agent prompt; falls back to platform defaults."""
    template = _overview_prompt_template(overview_settings, entity_type)
    return template.replace(_ENTITY_NAME_PLACEHOLDER, entity_name)


def _build_synth_user_message(
    *,
    user_message: str,
    notes: str,
    entity_id: str | None,
    entity_type: str | None,
    business_overview: str | None,
) -> str:
    """User turn: org background, scope, question, and retrieved CRM data."""
    parts: list[str] = []
    if business_overview and business_overview.strip():
        parts.append(
            "Organization background (context only — do not invent facts beyond CRM data):\n"
            f"{business_overview.strip()}"
        )
    if entity_id and entity_type:
        parts.append(f"Answer only about this CRM {entity_type} (id {entity_id}).")
    parts.append(f"Question: {user_message}")
    if notes:
        parts.append(
            "Use only the CRM data below. Follow the system instructions for "
            "sections and format.\n\n"
            f"CRM data:\n{notes}"
        )
    else:
        parts.append(
            "No matching CRM data was retrieved. "
            "Reply in one short neutral sentence that the information is not available."
        )
    return "\n\n".join(parts)


async def _load_effective_ai_overview_settings(
    db_connection: asyncpg.Connection,
    organization_id: str,
) -> AiOverviewSettings:
    """Load org AI overview settings; per-entity prompts fall back to platform defaults."""
    repo = OrganizationRepository(db_connection=db_connection)
    org = await repo.get_organization_by_id(organization_id)
    if not org:
        return OrganizationService.default_ai_overview_settings()
    settings = parse_json_field(org.get("settings"))
    return OrganizationService._resolve_effective_ai_overview_settings(settings)


def _build_hardcoded_queries(entity_name: str) -> list[str]:
    """Return three Supermemory search queries for ``entity_name``.

    Replaces the LLM intent planner for entity-scoped lookups. The three templates
    cover: identity recall, notes/email signals, and pipeline/company context.
    """
    name = entity_name.strip()
    return [template.format(name=name) for template in _DEFAULT_QUERY_TEMPLATES]


def _snapshot_section_sort_key(heading: str) -> int:
    """Order CRM sections for synthesis: notes -> leads/companies -> emails -> rest."""
    heading_lower = heading.casefold()
    if heading_lower.startswith("notes"):
        return 0
    if heading_lower.startswith("linked lead") or heading_lower.startswith(
        "companies"
    ) or heading_lower.startswith("crm company") or heading_lower.startswith("work history"):
        return 1
    if heading_lower.startswith("email"):
        return 2
    return 3


def _prioritize_intel_sections_in_snapshot(text: str) -> str:
    """Reorder CRM markdown so notes and sales sections precede profile/skills."""
    stripped = text.strip()
    if not stripped:
        return text
    lines = stripped.split("\n")
    section_starts = [idx for idx, line in enumerate(lines) if line.startswith("## ")]
    if not section_starts:
        return text

    preamble = "\n".join(lines[: section_starts[0]]).strip()
    blocks: list[tuple[int, str]] = []
    for idx, start in enumerate(section_starts):
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        block = "\n".join(lines[start:end])
        heading = lines[start][3:].strip()
        blocks.append((_snapshot_section_sort_key(heading), block))
    if all(priority == 3 for priority, _ in blocks):
        return text

    blocks.sort(key=lambda item: item[0])
    parts = [preamble] if preamble else []
    parts.extend(block for _, block in blocks)
    return "\n\n".join(parts)


def _dedupe_hits(hits: list[GraphitiSearchHit]) -> list[GraphitiSearchHit]:
    """Return hits in first-seen order, one row per Graphiti hit id."""
    seen: set[str] = set()
    ordered: list[GraphitiSearchHit] = []
    for hit in hits:
        if hit.id in seen:
            continue
        seen.add(hit.id)
        ordered.append(hit)
    return ordered


def _drop_deleted_and_empty(hits: list[GraphitiSearchHit]) -> list[GraphitiSearchHit]:
    """Omit empty text and tombstone records (metadata status deleted)."""
    kept: list[GraphitiSearchHit] = []
    for hit in hits:
        if not hit.text.strip():
            continue
        meta = hit.metadata or {}
        if str(meta.get("status") or "").lower() == "deleted":
            continue
        kept.append(hit)
    return kept


def _entity_key_from_header(text: str) -> str | None:
    """Parse # Contact: / # Company: / # Lead: header when metadata is missing."""
    trimmed = text.lstrip()
    for prefix, kind in _ENTITY_HEADER_PREFIXES:
        if trimmed.startswith(prefix):
            name = trimmed[len(prefix) :].strip()[:120]
            if name:
                return f"{kind}:{name}"
    return None


def _entity_key_from_hit(hit: GraphitiSearchHit) -> str | None:
    """Stable key per CRM record so fragments collapse to one richest snippet."""
    meta = hit.metadata or {}
    entity_id = str(meta.get("entity_id") or "").strip()
    entity_type = str(meta.get("entity_type") or "").strip().lower()
    if entity_id and entity_type:
        return f"{entity_type}:{entity_id}"
    return _entity_key_from_header(hit.text)


def _hit_quality_score(text: str) -> int:
    """Prefer full CRM markdown snapshots over short extracted memory lines."""
    score = len(text)
    if not text:
        return score
    trimmed = text.lstrip()
    for prefix, _ in _ENTITY_HEADER_PREFIXES:
        if trimmed.startswith(prefix):
            score += 10_000
            break
    if "## Profile" in text:
        score += 2_000
    for marker in (
        "## Companies",
        "## CRM company associations",
        "## Work history",
        "## Phones",
        "## Social",
        "## Tags",
        "## Custom fields",
    ):
        if marker in text:
            score += 500
    return score


def _is_authoritative_crm_snapshot(hit: GraphitiSearchHit) -> bool:
    """True when the hit is a CRM sync snapshot header, not a short extracted memory line."""
    trimmed = hit.text.lstrip()
    for prefix, _ in _ENTITY_HEADER_PREFIXES:
        if trimmed.startswith(prefix):
            return any(marker in hit.text for marker in _STRUCTURED_SECTION_MARKERS)
    return False


def _metadata_updated_at(hit: GraphitiSearchHit) -> int:
    """Unix updated_at from sync metadata (0 when missing)."""
    meta = hit.metadata or {}
    raw = meta.get("updated_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    return 0


def _sync_generation_hits(hits: list[GraphitiSearchHit]) -> list[GraphitiSearchHit]:
    """Return every search hit from the newest CRM sync generation for one entity.

    Hybrid search often returns multiple chunks of the same document. Keeping only the
    highest-scoring chunk dropped Notes, pipeline, and profile sections in sibling
    chunks. When updated_at is present, all hits at that timestamp are merged;
    otherwise only scored snapshot fragments are used.
    """
    snapshots = [hit for hit in hits if _is_authoritative_crm_snapshot(hit)]
    if not snapshots:
        return []
    newest = max(_metadata_updated_at(hit) for hit in snapshots)
    if newest > 0:
        return [hit for hit in hits if _metadata_updated_at(hit) == newest]
    return snapshots


def _merge_unique_snippet_texts(hits: list[GraphitiSearchHit]) -> str:
    """Join hit texts in quality order, skipping exact duplicates."""
    ordered = sorted(hits, key=lambda hit: _hit_quality_score(hit.text), reverse=True)
    seen: set[str] = set()
    parts: list[str] = []
    for hit in ordered:
        text = hit.text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return "\n\n".join(parts)


def _should_append_supplemental_fragment(text: str, base: str) -> bool:
    """Allow extra search chunks that add detail without stale one-line associations."""
    if not text or text in base:
        return False
    if len(text) < 150 and not text.lstrip().startswith("#"):
        for marker in _STRUCTURED_SECTION_MARKERS:
            if marker in base:
                return False
    return True


def _metadata_filters_for_entity(
    entity_type: str,
    entity_id: str,
) -> dict[str, object]:
    """Supermemory metadata filter matching sync _base_metadata fields."""
    return {
        "AND": [
            {"key": "entity_type", "value": entity_type},
            {"key": "entity_id", "value": entity_id},
        ]
    }


def _merge_entity_snippets(hits: list[GraphitiSearchHit]) -> str:
    """Combine search fragments for one CRM record.

    When sync snapshots exist, merge every chunk from the newest updated_at so hybrid
    search recall is not truncated to a single section. Short unstructured lines from
    older extracted memories (e.g. removed company associations) are still excluded.
    """
    snapshot_hits = _sync_generation_hits(hits)
    if snapshot_hits:
        sync_ids = {hit.id for hit in snapshot_hits}
        base = _merge_unique_snippet_texts(snapshot_hits)
        extras: list[str] = []
        for hit in sorted(hits, key=lambda h: _hit_quality_score(h.text), reverse=True):
            if hit.id in sync_ids:
                continue
            text = hit.text.strip()
            if not _should_append_supplemental_fragment(text, base):
                continue
            extras.append(text)
        if extras:
            return base + "\n\n" + "\n\n".join(extras)
        return base

    return _merge_unique_snippet_texts(hits)


def _collapse_hits_by_entity(hits: list[GraphitiSearchHit]) -> list[GraphitiSearchHit]:
    """Merge fragments per contact/company/lead instead of dropping smaller chunks."""
    groups: dict[str, list[GraphitiSearchHit]] = {}
    ungrouped: list[GraphitiSearchHit] = []

    for hit in hits:
        key = _entity_key_from_hit(hit)
        if not key:
            ungrouped.append(hit)
            continue
        groups.setdefault(key, []).append(hit)

    merged: list[GraphitiSearchHit] = []
    for key, group in groups.items():
        combined = _merge_entity_snippets(group)
        if not combined:
            continue
        merged.append(
            GraphitiSearchHit(
                id=key,
                text=combined,
                metadata=group[0].metadata,
            )
        )

    merged.extend(ungrouped)
    merged.sort(key=lambda hit: _hit_quality_score(hit.text), reverse=True)
    return merged[:_MAX_SYNTH_ENTITY_SNIPPETS]


def _unique_graph_fact_lines(hits: list[GraphitiSearchHit]) -> list[str]:
    """Return deduplicated short fact lines from hybrid search edge hits."""
    seen: set[str] = set()
    facts: list[str] = []
    for hit in hits:
        text = hit.text.strip()
        if not text or text.startswith("{"):
            continue
        if text in seen:
            continue
        seen.add(text)
        facts.append(text)
    return facts


def _normalize_crm_type(entity_type: str | None) -> CrmEntityType | None:
    normalized = (entity_type or "").strip().lower()
    if normalized in ("contact", "company", "lead"):
        return normalized  # type: ignore[return-value]
    return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrgMemoryQueryService:
    """Graphiti focal search + snapshot JSON → markdown AI Overview."""

    def __init__(self, *, graphiti: GraphitiCrmService | None = None) -> None:
        self._graphiti = graphiti or GraphitiCrmService()

    async def run(
        self,
        *,
        user_message: str,
        organization_id: str,
        entity_id: str | None = None,
        entity_type: str | None = None,
        db_connection: asyncpg.Connection | None = None,
    ) -> str:
        """Return a sales intelligence markdown briefing for the queried entity."""
        user_message = user_message.strip()
        model = shared_settings.org_memory_llm_model

        if db_connection is not None:
            overview_settings = await _load_effective_ai_overview_settings(
                db_connection,
                organization_id,
            )
        else:
            overview_settings = OrganizationService.default_ai_overview_settings()

        prompt_entity_type = _resolve_overview_entity_type(entity_type)

        group_id = container_tag_for_organization(organization_id)
        crm_type = _normalize_crm_type(entity_type)
        center_node_uuid: str | None = None
        snapshot_text = ""
        entity_name = user_message

        if entity_id and crm_type:
            snapshot = await self._graphiti.get_snapshot_episode(
                group_id=group_id,
                crm_type=crm_type,
                crm_id=entity_id.strip(),
            )
            if snapshot is not None:
                if str(snapshot.metadata.status).lower() != "deleted":
                    snapshot_text = snapshot_to_synthesis_text(snapshot)
                    if snapshot.display_name:
                        entity_name = snapshot.display_name
            center_node_uuid = self._graphiti.resolve_entity_uuid(
                crm_type=crm_type,
                crm_id=entity_id.strip(),
            )

        search_queries = _build_hardcoded_queries(entity_name)

        all_search_sets = await asyncio.gather(
            *[
                self._graphiti.search_hybrid(
                    query=q,
                    group_id=group_id,
                    center_node_uuid=center_node_uuid,
                    limit=_LOOKUP_SEARCH_LIMIT,
                )
                for q in search_queries
            ]
        )

        raw_hits: list[GraphitiSearchHit] = []
        for subset in all_search_sets:
            raw_hits.extend(subset)

        logger.info(
            "org_memory_search organization_id=%s entity_id=%s raw_hits=%s",
            organization_id,
            entity_id,
            len(raw_hits),
        )

        cleaned = _drop_deleted_and_empty(_dedupe_hits(raw_hits))
        fact_lines = _unique_graph_fact_lines(cleaned)

        notes_parts: list[str] = []
        if snapshot_text:
            notes_parts.append(_prioritize_intel_sections_in_snapshot(snapshot_text))
        if fact_lines:
            notes_parts.append("## Graph facts\n" + "\n".join(f"- {line}" for line in fact_lines))

        notes_truncated = False
        if notes_parts:
            notes = "\n\n---\n\n".join(notes_parts)
            if len(notes) > _SYNTH_CONTEXT_CHAR_LIMIT:
                notes = notes[:_SYNTH_CONTEXT_CHAR_LIMIT]
                notes_truncated = True
        else:
            notes = ""

        usable_count = (1 if snapshot_text else 0) + len(fact_lines)

        synth_system = _build_synth_system_prompt(
            entity_type=entity_type,
            entity_name=entity_name,
            overview_settings=overview_settings,
        )
        synth_user = _build_synth_user_message(
            user_message=user_message,
            notes=notes,
            entity_id=entity_id,
            entity_type=entity_type,
            business_overview=overview_settings.business_overview,
        )

        answer = (
            await create_chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": synth_system},
                    {"role": "user", "content": synth_user},
                ],
                max_completion_tokens=_SYNTH_MAX_TOKENS,
            )
        ).strip()

        used_fallback = False
        if not answer:
            used_fallback = True
            answer = (
                "No matching information is available."
                if not notes
                else "No answer could be formed from the available records."
            )

        logger.info(
            "org_memory_query organization_id=%s prompt_entity_type=%s search_hits=%s "
            "entities=%s notes_len=%s notes_truncated=%s used_fallback=%s answer_len=%s",
            organization_id,
            prompt_entity_type,
            len(raw_hits),
            usable_count,
            len(notes),
            notes_truncated,
            used_fallback,
            len(answer),
        )

        return answer
