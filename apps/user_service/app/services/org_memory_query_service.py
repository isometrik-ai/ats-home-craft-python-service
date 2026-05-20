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
_AGGREGATION_SEARCH_LIMIT = 50
_MAX_SYNTH_ENTITY_SNIPPETS = 5
_ENTITY_HEADER_RE = re.compile(r"^#\s*(Contact|Company|Lead):\s*(.+)", re.MULTILINE | re.IGNORECASE)

INTENT_SYSTEM_PROMPT = (
    "You are a CRM query planner. Parse the user's natural language query "
    "and return a JSON object.\n\n"
    "Return ONLY valid JSON, no markdown, no explanation.\n\n"
    "JSON shape:\n"
    "{\n"
    '  "is_aggregation": true | false,\n'
    '  "search_queries": ["<query1>", "<query2>"],\n'
    '  "synthesize_instruction": "<what to do with the retrieved memories>"\n'
    "}\n\n"
    "Rules:\n"
    '- For counts or full lists ("how many companies"), set is_aggregation: true\n'
    "- Produce 1–3 search_queries with different phrasings for better recall"
)

SYNTH_SYSTEM_PROMPT = (
    "You answer questions about CRM contacts, companies, and leads for a business user.\n"
    "Write one or two flowing paragraphs in plain conversational English.\n"
    "Include every relevant fact from the CRM notes (role, company, email, phone, "
    "addresses, tags, custom fields, social links) — do not omit fields that are present.\n\n"
    "Strict rules:\n"
    "- Use ONLY facts that appear in the CRM notes; quote labels and values faithfully.\n"
    "- Do NOT infer or rephrase (Status: active ≠ 'active user'; "
    "Preferred language: English ≠ 'English-speaking').\n"
    "- State full URLs, emails, and phone numbers from the notes; never say "
    "'the link you provided' or refer to the question.\n"
    "- Do NOT offer to draft messages, reach out, or do tasks.\n"
    "- Do NOT use bullet lists unless the user asked for a list.\n"
    "- You may skip stating obvious placeholder domains (example.com) but still "
    "mention other real fields.\n"
    "- If notes conflict, prefer sections under '# Contact:' or '# Company:'.\n"
    "- If a fact is missing from the notes, do not mention it."
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
    """Combine all search fragments for one CRM record (richest snapshot first)."""
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
                f"The user asked: {user_message}\n\n"
                f"CRM notes:\n{notes}\n\n"
                f"{plan.synthesize_instruction}"
            )
        else:
            synth_user = (
                f"The user asked: {user_message}\n\n"
                "No CRM notes were found for this question. "
                "Tell the user politely that you do not have that information."
            )

        answer = (
            await create_chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": SYNTH_SYSTEM_PROMPT},
                    {"role": "user", "content": synth_user},
                ],
                max_completion_tokens=_SYNTH_MAX_TOKENS,
                reasoning_effort="minimal",
            )
        ).strip()
        if not answer:
            answer = (
                "I don't have enough information in your CRM to answer that yet."
                if not notes
                else "I wasn't able to put together an answer from your CRM data."
            )

        return answer
