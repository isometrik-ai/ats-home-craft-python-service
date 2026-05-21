"""Natural-language CRM Q&A scoped to one org via Supermemory + OpenAI."""

from __future__ import annotations

import asyncio
import json
import re

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

_INTENT_MAX_TOKENS = 600
# gpt-5-nano bills reasoning + visible output against max_completion_tokens;
# 900 was too low when CRM notes are large (reasoning consumed the whole budget).
_SYNTH_MAX_TOKENS = 4096
_SYNTH_CONTEXT_CHAR_LIMIT = 14_000
_LOOKUP_SEARCH_LIMIT = 25
_AGGREGATION_SEARCH_LIMIT = 5
_MAX_SYNTH_ENTITY_SNIPPETS = 5
_ENTITY_HEADER_RE = re.compile(r"^#\s*(Contact|Company|Lead):\s*(.+)", re.MULTILINE | re.IGNORECASE)
# Full CRM markdown snapshots from sync (entity header + structured sections).
_AUTHORITATIVE_SNAPSHOT_MIN_SCORE = 10_000
SYNTH_SYSTEM_PROMPT = (
    "You write a single AI-generated overview of a CRM entity. "
    "Your output must read like a professionally written bio or company summary.\n\n"
    "EXAMPLE INPUT — fragments about Rohit Marthak across multiple notes:\n"
    "Contact: Rohit Marthak. Title: Python AI Engineer. Company: Appscrip. "
    "Email: rohitmarthak@appscrip.co. Phone: +919823929922. "
    "Address: 54 RBI Colony, Bengaluru. LinkedIn: https://in.linkedin.com/in/rohitmarthak. "
    "Health checkup: No. Insurance: Policy Bazaar. "
    "Notes: Met at Reva College, Bengaluru. Follow up next Friday. "
    "updated_at: 2026-05-20T07:44:57. "
    "Contact: Rohit Marthak. Title: Python AI Engineer. Company: Appscrip. "
    "Email: rohitmarthak@appscrip.co. Tags: AI engineer, Homespark.\n\n"
    "EXAMPLE OUTPUT:\n"
    "Rohit Marthak is a Python AI Engineer at Appscrip, based in Bengaluru, Karnataka. "
    "He can be reached at rohitmarthak@appscrip.co and +919823929922, "
    "and his LinkedIn profile is at https://in.linkedin.com/in/rohitmarthak. "
    "His insurance is through Policy Bazaar. "
    "He was initially met at Reva College, Bengaluru, with a follow-up scheduled for "
    "the following Friday.\n\n"
    "EXAMPLE INPUT — fragments about Appscrip:\n"
    "Company: Appscrip. Industry: Technology. Location: Bengaluru, Karnataka, India. "
    "Status: active. Website: appscrip.co. "
    "Contacts: Rohit Marthak, Avinash Singh. updated_at: 2026-05-20T09:00:00.\n\n"
    "EXAMPLE OUTPUT:\n"
    "Appscrip is an active technology company headquartered in Bengaluru, Karnataka, India. "
    "Its website is appscrip.co. "
    "Key contacts at the company include Rohit Marthak and Avinash Singh, "
    "both Python AI Engineers.\n\n"
    "END OF EXAMPLES.\n\n"
    "RULES:\n"
    "1. Output exactly one paragraph for a single entity query. "
    "If multiple entities are requested, one paragraph per entity, separated by a blank line.\n"
    "2. Never write about the same entity twice. "
    "If the notes repeat the same person or company, merge everything silently into "
    "one paragraph.\n"
    "3. Every fact appears exactly once. Never repeat a name, field, or detail.\n"
    "4. First sentence: name, title, company, location. "
    "Second sentence: email and phone together. "
    "Remaining sentences: LinkedIn, education, notes context, custom fields — grouped naturally.\n"
    "5. Write flowing prose. Never list fields mechanically one per sentence.\n"
    "6. Omit fields with no value. Never mention they are missing.\n"
    "7. Use pronouns after first mention: He, She, They for people. The company for companies.\n"
    "8. Convert notes into natural context: "
    "'He was met at Reva College' not 'Notes: Met at Reva College'.\n"
    "9. Convert custom fields into natural prose: "
    "'his insurance is through ICICI' not 'Insurance Company: ICICI'.\n"
    "10. Ignore and never output: raw timestamps, ISO datetime strings, updated_at values, "
    "last updated dates, database IDs, internal system fields.\n"
    "11. Ignore and never output: any URL or domain containing 'example.com'.\n"
    "12. Never explain what you omitted, skipped, or ignored.\n"
    "13. Never mention the domain rule, the CRM, the notes, the records, or the database.\n"
    "14. Never start with 'I', 'Here', 'Based on', or 'According to'.\n"
    "15. Never end with an offer to help, a closing sentence, or a question.\n"
    "16. Stop writing immediately after the last sentence of the last entity."
)
INTENT_SYSTEM_PROMPT = (
    "You are a CRM query planner. Parse the user query and return ONLY valid JSON. "
    "No markdown. No explanation.\n\n"
    "JSON shape:\n"
    "{\n"
    '  "is_aggregation": true | false,\n'
    '  "search_queries": ["<query1>", "<query2>"],\n'
    '  "synthesize_instruction": "<instruction>"\n'
    "}\n\n"
    "RULES:\n"
    "- is_aggregation: true only for counts or full-list requests.\n"
    "- search_queries: 2 to 3 phrasings. Include the entity name verbatim in at least one.\n"
    "- synthesize_instruction: exactly one sentence. "
    "Must end with: 'Write only one paragraph total. No repetition. No commentary.'\n\n"
    "synthesize_instruction EXAMPLES:\n"
    "- 'Tell me everything about X' → "
    "'Write one flowing paragraph with every meaningful detail about this person: "
    "role, company, location, contact info, education, notes, and custom fields. "
    "Write only one paragraph total. No repetition. No commentary.'\n"
    "- 'Who is X' → "
    "'Write one paragraph identifying this person with their role, company, and key details. "
    "Write only one paragraph total. No repetition. No commentary.'\n"
    "- 'Describe this company' → "
    "'Write one paragraph describing this company with all available details. "
    "Write only one paragraph total. No repetition. No commentary.'\n"
    "- 'List contacts at Appscrip' → "
    "'Write one short paragraph per contact. No repetition. No commentary.'"
)


def _strip_code_fences(raw: str) -> str:
    """Remove optional markdown code fences from LLM JSON output."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


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


def _entity_key_from_hit(hit: SupermemorySearchHit) -> str | None:
    """Stable key per CRM record so fragments collapse to one richest snippet."""
    meta = hit.metadata or {}
    entity_id = str(meta.get("entity_id") or "").strip()
    entity_type = str(meta.get("entity_type") or "").strip().lower()
    if entity_id and entity_type:
        return f"{entity_type}:{entity_id}"

    match = _ENTITY_HEADER_RE.search(hit.text)
    if match:
        kind = match.group(1).strip().lower()
        name = match.group(2).strip()[:120]
        return f"{kind}:{name}"
    return None


def _hit_quality_score(hit: SupermemorySearchHit) -> int:
    """Prefer full CRM markdown snapshots over short extracted memory lines."""
    text = hit.text
    score = len(text)
    if _ENTITY_HEADER_RE.search(text):
        score += 10_000
    if "## Profile" in text:
        score += 2_000
    for section in ("## Companies", "## Phones", "## Social", "## Tags", "## Custom fields"):
        if section in text:
            score += 500
    return score


def _is_authoritative_crm_snapshot(hit: SupermemorySearchHit) -> bool:
    """True when the hit is a full sync snapshot, not a short extracted memory line."""
    return _hit_quality_score(hit) >= _AUTHORITATIVE_SNAPSHOT_MIN_SCORE


def _metadata_updated_at(hit: SupermemorySearchHit) -> int:
    """Unix ``updated_at`` from sync metadata (0 when missing)."""
    meta = hit.metadata or {}
    raw = meta.get("updated_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    return 0


def _pick_authoritative_snapshot(hits: list[SupermemorySearchHit]) -> SupermemorySearchHit | None:
    """Return the newest full CRM snapshot for one entity, if any."""
    snapshots = [hit for hit in hits if _is_authoritative_crm_snapshot(hit)]
    if not snapshots:
        return None
    return max(
        snapshots,
        key=lambda hit: (_metadata_updated_at(hit), _hit_quality_score(hit), len(hit.text)),
    )


def _metadata_filters_for_entity(
    entity_type: str,
    entity_id: str,
) -> dict[str, object]:
    """Supermemory metadata filter matching sync ``_base_metadata`` fields."""
    return {
        "AND": [
            {"key": "entity_type", "value": entity_type},
            {"key": "entity_id", "value": entity_id},
        ]
    }


def _merge_entity_snippets(hits: list[SupermemorySearchHit]) -> str:
    """Combine search fragments for one CRM record.

    When a full sync snapshot exists, use only the newest snapshot so stale extracted
    memories (e.g. removed company associations) are not merged back into summaries.
    """
    authoritative = _pick_authoritative_snapshot(hits)
    if authoritative is not None:
        return authoritative.text.strip()

    ordered = sorted(hits, key=_hit_quality_score, reverse=True)
    seen: set[str] = set()
    parts: list[str] = []
    for hit in ordered:
        text = hit.text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return "\n\n".join(parts)


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
    merged.sort(key=_hit_quality_score, reverse=True)
    return merged[:_MAX_SYNTH_ENTITY_SNIPPETS]


class OrgMemoryQueryService:
    """Intent → Supermemory hybrid search → answer synthesis."""

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
        """Return a user-facing natural-language answer."""
        user_message = user_message.strip()
        fallback_queries = [user_message]
        model = shared_settings.org_memory_llm_model
        search_filters: dict[str, object] | None = None
        if entity_id and entity_type:
            search_filters = _metadata_filters_for_entity(entity_type, entity_id.strip())

        raw_plan = await create_chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=_INTENT_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        plan = _parse_intent(raw_plan or "{}", fallback_queries=fallback_queries)

        limit = _AGGREGATION_SEARCH_LIMIT if plan.is_aggregation else _LOOKUP_SEARCH_LIMIT
        container = container_tag_for_organization(organization_id)

        search_sets = await asyncio.gather(
            *(
                self._supermemory.search_hybrid(
                    query=q,
                    container_tag=container,
                    limit=limit,
                    filters=search_filters,
                )
                for q in plan.search_queries
            )
        )
        merged: list[SupermemorySearchHit] = []
        for subset in search_sets:
            merged.extend(subset)

        cleaned = _drop_deleted_and_empty(_dedupe_hits(merged))
        usable = _collapse_hits_by_entity(cleaned)

        if usable:
            notes = "\n\n---\n\n".join(hit.text for hit in usable)
            if len(notes) > _SYNTH_CONTEXT_CHAR_LIMIT:
                notes = notes[:_SYNTH_CONTEXT_CHAR_LIMIT]
                logger.info(
                    "org_memory_synth_context_truncated chars=%s",
                    _SYNTH_CONTEXT_CHAR_LIMIT,
                )
        else:
            notes = ""

        if notes:
            scope_line = ""
            if entity_id and entity_type:
                scope_line = f"Answer only about this CRM {entity_type} (id {entity_id}).\n\n"
            synth_user = (
                f"{scope_line}"
                f"Question: {user_message}\n\n"
                f"CRM notes:\n{notes}\n\n"
                f"Instruction: {plan.synthesize_instruction}"
            )
        else:
            synth_user = (
                f"Question: {user_message}\n\n"
                "No matching CRM notes were retrieved. "
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
                reasoning_effort="low",
            )
        ).strip()
        if not answer:
            answer = (
                "No matching information is available."
                if not notes
                else "No answer could be formed from the available records."
            )

        return answer
