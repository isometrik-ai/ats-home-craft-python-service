"""Natural-language CRM Q&A scoped to one org via Supermemory + OpenAI."""

from __future__ import annotations

import asyncio
import json

from apps.user_service.app.schemas.org_memory import OrgMemoryIntentPlan
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger
from libs.shared_utils.openai_chat_service import create_chat_completion
from libs.shared_utils.supermemory_service import (
    SupermemorySearchHit,
    SupermemoryService,
    container_tag_for_organization,
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
_STRUCTURED_SECTION_MARKERS = ("## Profile", "## Companies")

# ---------------------------------------------------------------------------
# Hardcoded search query templates
# ---------------------------------------------------------------------------
# Three queries per lookup give broad semantic coverage:
#   1. Identity recall — who the entity is
#   2. Notes / email signals — what has been said and committed
#   3. Leads / company associations — pipeline and org context
# {name} is substituted at runtime with the entity display name or user message.
_HARDCODED_QUERY_TEMPLATES: tuple[str, str, str] = (
    "{name}",
    "{name} notes emails follow-up objections interests business",
    "{name} pipeline stage company association lead",
)

# ---------------------------------------------------------------------------
# Synthesis prompt
# ---------------------------------------------------------------------------
SYNTH_SYSTEM_PROMPT = (
    "You are a sales intelligence assistant. Write a markdown briefing "
    "for a sales professional using only the provided CRM data. "
    "Each section is a short flowing paragraph, not a list.\n\n"
    "SECTIONS — output exactly these headers in order. "
    "Omit a section only if it has zero data.\n\n"
    "## Overview\n"
    "Name, title, company or companies, location, status, "
    "email, phone, LinkedIn in natural prose.\n\n"
    "## Key Insights\n"
    "Weave all notes verbatim and email signals together with sharp analysis: "
    "what was discussed, what they want, what was committed, "
    "what the opportunity is, what the blocker is, and what needs to happen next.\n\n"
    "## Leads\n"
    "Every lead: name, stage, amount, close date, role, priority in natural prose. "
    "Omit section if no leads.\n\n"
    "## Companies\n"
    "Every linked company: name, industry, role in natural prose. "
    "Omit section if no companies.\n\n"
    "EXAMPLE INPUT:\n"
    "Contact: Rohit Marthak. Title: Python AI Engineer. "
    "Companies: Appscrip, Hex Wireless. Email: rohitmarthak@appscrip.co. "
    "Phone: +919823929922. LinkedIn: https://in.linkedin.com/in/rohitmarthak. "
    "Status: active. Location: Bengaluru, India. "
    "Leads: Appscrip Platform Renewal — Proposal — INR 450000 — close 2026-07-31 "
    "— Decision Maker — high. Hex Q3 Retainer — Qualified — Technical Lead — medium. "
    "Notes: Met at Reva College on intake. Follow up next Friday. "
    "Wants enterprise tier but needs SLA clause reviewed before signing. "
    "Email: Legal will revert by 25 May.\n\n"
    "EXAMPLE OUTPUT:\n\n"
    "## Overview\n"
    "Rohit Marthak is a Python AI Engineer working across Appscrip and Hex Wireless, "
    "based in Bengaluru, India. He can be reached at rohitmarthak@appscrip.co "
    "and +919823929922, with his LinkedIn at https://in.linkedin.com/in/rohitmarthak.\n\n"
    "## Key Insights\n"
    "Rohit was met at Reva College on initial intake with a follow-up scheduled for the "
    "following Friday, and has since expressed strong interest in the enterprise tier. "
    "The only blocker is a custom SLA clause he wants reviewed before signing — "
    "legal has confirmed they will revert by 25 May, making that the critical follow-up date. "
    "He holds Decision Maker status on the Appscrip Platform Renewal at INR 450,000 "
    "closing 31 July and is simultaneously Technical Lead on the Hex Q3 Retainer, "
    "making him a high-value contact across both accounts.\n\n"
    "## Leads\n"
    "Rohit is the Decision Maker on the Appscrip Platform Renewal, currently in Proposal "
    "at INR 450,000, targeting a close by 31 July 2026 and flagged high priority. "
    "He is also engaged as Technical Lead on the Hex Q3 Retainer, "
    "which is in Qualified at medium priority.\n\n"
    "## Companies\n"
    "Rohit is linked to Appscrip, a technology company where he is a primary contact, "
    "and to Hex Wireless where he is engaged in a technical capacity.\n\n"
    "END OF EXAMPLE.\n\n"
    "RULES:\n"
    "- Each section is one short paragraph. No bullets. No sub-headings.\n"
    "- Key Insights must open with the notes verbatim, then blend in "
    "email signals, blockers, and the next action with a date.\n"
    "- Amounts with currency: 'INR 450,000'. Dates natural: '31 July 2026'.\n"
    "- Latest value wins when a field repeats. Each fact appears once only.\n"
    "- Skip example.com URLs, raw timestamps, ISO strings, database IDs.\n"
    "- Omit fields with no value. Never say a field is missing.\n"
    "- Never use: 'not provided', 'based on', 'the CRM', 'updated_at', "
    "'here is', 'I can', 'please note', or any closing offer.\n"
    "- Never start with 'I', 'Here', or 'Based on'."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_code_fences(raw: str) -> str:
    """Remove optional markdown code fences from LLM JSON output."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_intent(plan_text: str, *, fallback_queries: list[str]) -> OrgMemoryIntentPlan:
    """Parse and validate the intent JSON plan; fall back to raw user queries on failure."""
    try:
        data = json.loads(_strip_code_fences(plan_text))
        if not isinstance(data, dict):
            raise ValueError("intent payload must be a JSON object")
        plan = OrgMemoryIntentPlan.model_validate(data)
    except Exception:
        logger.warning("org_memory_intent_json_parse_failed")
        return OrgMemoryIntentPlan(search_queries=fallback_queries[:3])

    if not plan.search_queries:
        return plan.model_copy(update={"search_queries": fallback_queries[:3]})
    return plan


def _build_hardcoded_queries(entity_name: str) -> list[str]:
    """Return three Supermemory search queries for ``entity_name``.

    Replaces the LLM intent planner for entity-scoped lookups. The three templates
    cover: identity recall, notes/email signals, and pipeline/company context.
    """
    name = entity_name.strip()
    return [template.format(name=name) for template in _HARDCODED_QUERY_TEMPLATES]


def _snapshot_section_sort_key(heading: str) -> int:
    """Order CRM sections for synthesis: notes -> leads/companies -> emails -> rest."""
    heading_lower = heading.casefold()
    if heading_lower.startswith("notes"):
        return 0
    if heading_lower.startswith("linked lead") or heading_lower.startswith("companies"):
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


def _dedupe_hits(hits: list[SupermemorySearchHit]) -> list[SupermemorySearchHit]:
    """Return hits in first-seen order, one row per Supermemory hit id."""
    seen: set[str] = set()
    ordered: list[SupermemorySearchHit] = []
    for hit in hits:
        if hit.id in seen:
            continue
        seen.add(hit.id)
        ordered.append(hit)
    return ordered


def _drop_deleted_and_empty(hits: list[SupermemorySearchHit]) -> list[SupermemorySearchHit]:
    """Omit empty text and tombstone records (metadata status deleted)."""
    kept: list[SupermemorySearchHit] = []
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


def _entity_key_from_hit(hit: SupermemorySearchHit) -> str | None:
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
    for marker in ("## Companies", "## Phones", "## Social", "## Tags", "## Custom fields"):
        if marker in text:
            score += 500
    return score


def _is_authoritative_crm_snapshot(hit: SupermemorySearchHit) -> bool:
    """True when the hit is a CRM sync snapshot header, not a short extracted memory line."""
    trimmed = hit.text.lstrip()
    for prefix, _ in _ENTITY_HEADER_PREFIXES:
        if trimmed.startswith(prefix):
            return any(marker in hit.text for marker in _STRUCTURED_SECTION_MARKERS)
    return False


def _metadata_updated_at(hit: SupermemorySearchHit) -> int:
    """Unix updated_at from sync metadata (0 when missing)."""
    meta = hit.metadata or {}
    raw = meta.get("updated_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    return 0


def _sync_generation_hits(hits: list[SupermemorySearchHit]) -> list[SupermemorySearchHit]:
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


def _merge_unique_snippet_texts(hits: list[SupermemorySearchHit]) -> str:
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


def _merge_entity_snippets(hits: list[SupermemorySearchHit]) -> str:
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


def _collapse_hits_by_entity(hits: list[SupermemorySearchHit]) -> list[SupermemorySearchHit]:
    """Merge fragments per contact/company/lead instead of dropping smaller chunks."""
    groups: dict[str, list[SupermemorySearchHit]] = {}
    ungrouped: list[SupermemorySearchHit] = []

    for hit in hits:
        key = _entity_key_from_hit(hit)
        if not key:
            ungrouped.append(hit)
            continue
        groups.setdefault(key, []).append(hit)

    merged: list[SupermemorySearchHit] = []
    for key, group in groups.items():
        combined = _merge_entity_snippets(group)
        if not combined:
            continue
        merged.append(
            SupermemorySearchHit(
                id=key,
                text=combined,
                metadata=group[0].metadata,
            )
        )

    merged.extend(ungrouped)
    merged.sort(key=lambda hit: _hit_quality_score(hit.text), reverse=True)
    return merged[:_MAX_SYNTH_ENTITY_SNIPPETS]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrgMemoryQueryService:
    """Hardcoded Supermemory search -> sales intelligence markdown synthesis."""

    def __init__(self) -> None:
        self._supermemory = SupermemoryService.from_settings()

    async def run(
        self,
        *,
        user_message: str,
        organization_id: str,
        entity_id: str | None = None,
        entity_type: str | None = None,
    ) -> str:
        """Return a sales intelligence markdown briefing for the queried entity."""
        user_message = user_message.strip()
        model = shared_settings.org_memory_llm_model

        # Build hardcoded search queries — no LLM intent planner needed.
        # entity_id is known when the caller passes a specific CRM record;
        # fall back to the raw user message as the entity name otherwise.
        entity_name = user_message
        search_queries = _build_hardcoded_queries(entity_name)

        # When entity_id + entity_type are known, scope search to that CRM record only.
        search_filters: dict[str, object] | None = None
        if entity_id and entity_type:
            search_filters = _metadata_filters_for_entity(entity_type, entity_id.strip())

        container = container_tag_for_organization(organization_id)

        all_search_sets = await asyncio.gather(
            *[
                self._supermemory.search_hybrid(
                    query=q,
                    container_tag=container,
                    limit=_LOOKUP_SEARCH_LIMIT,
                    filters=search_filters,
                )
                for q in search_queries
            ]
        )

        raw_hits: list[SupermemorySearchHit] = []
        for subset in all_search_sets:
            raw_hits.extend(subset)

        logger.info(
            "org_memory_search organization_id=%s entity_id=%s raw_hits=%s",
            organization_id,
            entity_id,
            len(raw_hits),
        )

        cleaned = _drop_deleted_and_empty(_dedupe_hits(raw_hits))
        usable = _collapse_hits_by_entity(cleaned)

        # Promote the specific entity's snapshot to position 0 for synthesis.
        if entity_id and entity_type:
            entity_key = f"{entity_type.strip().lower()}:{entity_id.strip()}"
            primary = [h for h in usable if h.id == entity_key]
            rest = [h for h in usable if h.id != entity_key]
            usable = primary + rest

        notes_truncated = False
        if usable:
            notes = "\n\n---\n\n".join(
                _prioritize_intel_sections_in_snapshot(hit.text) for hit in usable
            )
            if len(notes) > _SYNTH_CONTEXT_CHAR_LIMIT:
                notes = notes[:_SYNTH_CONTEXT_CHAR_LIMIT]
                notes_truncated = True
        else:
            notes = ""

        if notes:
            scope_line = ""
            if entity_id and entity_type:
                scope_line = f"Answer only about this CRM {entity_type} (id {entity_id}).\n\n"
            synth_user = (
                f"{scope_line}"
                f"Question: {user_message}\n\n"
                "Return the four markdown sections: "
                "Overview, Key Insights, Leads, Companies. "
                "Open Key Insights with every note verbatim then blend in email signals, "
                "blockers, and the next action with a date. "
                "Use only the latest value when a field repeats. "
                "Omit any field that has no value.\n\n"
                f"CRM data:\n{notes}"
            )
        else:
            synth_user = (
                f"Question: {user_message}\n\n"
                "No matching CRM data was retrieved. "
                "Reply in one short neutral sentence that the information is not available."
            )

        answer = (
            await create_chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": SYNTH_SYSTEM_PROMPT},
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
            "org_memory_query organization_id=%s search_hits=%s entities=%s "
            "notes_len=%s notes_truncated=%s used_fallback=%s answer_len=%s",
            organization_id,
            len(raw_hits),
            len(usable),
            len(notes),
            notes_truncated,
            used_fallback,
            len(answer),
        )

        return answer
