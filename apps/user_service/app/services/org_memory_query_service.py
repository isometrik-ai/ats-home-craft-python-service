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
# gpt-5-nano bills reasoning + visible output against max_completion_tokens;
# 900 was too low when CRM notes are large (reasoning consumed the whole budget).
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
# Tail appended to every synthesize_instruction to enforce recency and omission rules.
_SYNTH_INSTRUCTION_REQUIRED_LEAD = (
    "Open with every CRM note verbatim, then lead amounts, pipeline, and company deal context"
)
_SYNTH_INSTRUCTION_REQUIRED_TAIL = (
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value."
)
_SYNTH_USER_ANSWER_ORDER = (
    "Answer order: all notes verbatim first; then every deal with stage, amount, and "
    "company associations on the deal; then email signals; then brief identity and contact "
    "channels; then profile/skills/education last."
)

INTENT_SYSTEM_PROMPT = (
    "You are a CRM sales intelligence query planner. "
    "Parse the user query and return ONLY valid JSON. No markdown. No explanation.\n\n"
    "JSON shape:\n"
    "{\n"
    '  "is_aggregation": true | false,\n'
    '  "search_queries": ["<query1>", "<query2>", "<query3>"],\n'
    '  "synthesize_instruction": "<instruction>"\n'
    "}\n\n"
    "SEARCH QUERY RULES:\n"
    "- Always produce exactly 3 search queries to maximize recall across all data types.\n"
    "- Query 1: entity name verbatim (e.g. 'Rohit Marthak').\n"
    "- Query 2: entity name + notes and email context "
    "(e.g. 'Rohit Marthak notes emails follow-up objections').\n"
    "- Query 3: entity name + deals and relationships "
    "(e.g. 'Rohit Marthak pipeline stage company association').\n"
    "- For company queries, also search for associated contacts and leads "
    "(e.g. 'Appscrip contacts leads pipeline deals').\n"
    "- For lead queries, search for stage, amount, and all involved parties "
    "(e.g. 'deal opportunity stage contacts companies involved notes').\n\n"
    "is_aggregation RULES:\n"
    "- true only for counts or full-list requests "
    "('how many', 'list all', 'show all', 'which contacts', 'all leads in stage').\n"
    "- false for all entity detail queries.\n\n"
    "synthesize_instruction RULES:\n"
    "- Exactly one sentence describing what to produce.\n"
    "- Must always end with this exact phrase: "
    "'Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "synthesize_instruction EXAMPLES:\n"
    "- 'Tell me everything about Rohit Marthak' → "
    "'Write a sales intelligence summary covering identity, all associated companies, "
    "all deals with stage and amount, email context, and all notes in full. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "- 'Who is Rohit Marthak' → "
    "'Write a professional profile with role, company, contact details, "
    "current deal involvement, and notes. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "- 'Tell me about Appscrip' → "
    "'Write a company intelligence summary covering firmographics, all linked contacts, "
    "all associated deals, email context, and all notes in full. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "- 'Tell me about the Acme renewal deal' → "
    "'Write a deal summary covering stage, value, all contacts and companies involved, "
    "deal notes in full, and email context. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "- 'List all contacts at Appscrip' → "
    "'Write one short paragraph per contact with name, role, and deal involvement. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'\n\n"
    "- 'How many leads are in Qualified stage' → "
    "'Count distinct leads in Qualified stage and state the number in one sentence. "
    "Prioritize notes, then lead/deal amounts and company associations, then emails, "
    "over profile, skills, education, and addresses. "
    "Use only the latest data for each fact. Omit any field that has no value.'"
)

SYNTH_SYSTEM_PROMPT = (
    "You are a sales intelligence assistant writing authoritative entity briefings "
    "for a senior sales team. "
    "Your output reads like a seasoned account executive briefing their manager — "
    "commercially sharp, fully associated, and grounded entirely in the provided data.\n\n"
    # ── Data priority ──────────────────────────────────────────────────────────
    "DATA PRIORITY — when the same field appears more than once with different values, "
    "always use the most recent entry. Never mention that older data was discarded. "
    "Apply this order when writing the answer (first sentences = highest priority):\n"
    "1. Notes (contact, company, lead) — always open with these when present. "
    "Include every note verbatim or near-verbatim. Never compress, summarize, or omit them.\n"
    "2. Lead and deal sales data — right after notes when present: deal name, stage, "
    "amount with currency, close date, priority, deal type, lead source; "
    "every company on the deal (name, industry, label); linked leads and pipeline for contacts.\n"
    "3. Company associations — all companies the person or account is linked to, "
    "primary contact status, and firmographics that affect the sale.\n"
    "4. Email content and thread context — commitments, objections, decisions, follow-ups, "
    "buying signals.\n"
    "5. Identity and contact channels — name, title, location, status, email, phone, LinkedIn "
    "(skip example.com URLs).\n"
    "6. Profile and enrichment last — education, work history, skills, custom fields, "
    "addresses.\n\n"
    # ── Structure ──────────────────────────────────────────────────────────────
    "OUTPUT ORDER — one continuous line, sections in this sequence:\n\n"
    "FOR A CONTACT:\n"
    "First — all notes verbatim.\n"
    "Second — linked leads and every deal (name, stage, amount, close date, label, priority) "
    "and all associated companies with values only.\n"
    "Third — email business signals.\n"
    "Fourth — brief identity: name, title, location, status, email, phone, LinkedIn.\n"
    "Last — education, skills, work history, custom fields only when they add sales context.\n\n"
    "FOR A COMPANY:\n"
    "First — all notes verbatim.\n"
    "Second — every deal (stage, amount, close date, priority) and linked company context.\n"
    "Third — key people (primary contact first, then all others), then email signals.\n"
    "Fourth — firmographics (name, industry, location, status, website).\n"
    "Last — tech stack, billing, and other intelligence fields.\n\n"
    "FOR A LEAD:\n"
    "First — all lead notes verbatim.\n"
    "Second — deal snapshot (stage, amount with currency, close date, priority, source, deal type) "
    "and every company on the deal with name, industry, and label.\n"
    "Third — contacts on the deal with roles and labels, then email signals.\n"
    "Last — any extra profile context only if relevant to closing the deal.\n\n"
    # ── Examples ───────────────────────────────────────────────────────────────
    "EXAMPLE INPUT — repeated fragments about Rohit Marthak with a duplicate entry "
    "and a timestamp:\n"
    "Contact: Rohit Marthak. Title: Python AI Engineer. Companies: Appscrip, Hex Wireless. "
    "Email: rohitmarthak@appscrip.co. Phone: +919823929922. "
    "LinkedIn: https://in.linkedin.com/in/rohitmarthak. Status: active. "
    "Location: Bengaluru, Karnataka, India. "
    "Deals: Appscrip Platform Renewal — stage Proposal — amount INR 450000 — "
    "close 2026-07-31 — label Decision Maker — priority high. "
    "Hex Q3 Retainer — stage Qualified — label Technical Lead — priority medium. "
    "Notes: Met at Reva College Bengaluru on initial intake. "
    "Follow up scheduled for next Friday. "
    "Interested in enterprise tier but wants custom SLA clause reviewed before signing. "
    "Insurance: Policy Bazaar. Preferred language: English. "
    "updated_at: 2026-05-20T07:44:57. "
    "Contact: Rohit Marthak. Title: Python AI Engineer. Company: Appscrip. "
    "Email: rohitmarthak@appscrip.co. Tags: AI engineer.\n\n"
    "EXAMPLE OUTPUT:\n"
    "He was met at Reva College, Bengaluru, on initial intake, "
    "with a follow-up scheduled for the following Friday. "
    "He has expressed interest in the enterprise tier but wants a custom SLA clause "
    "reviewed before signing. "
    "On the Appscrip Platform Renewal in Proposal he is Decision Maker; "
    "the deal is INR 450,000 targeting 31 July 2026, high priority, with Appscrip as the account. "
    "On the Hex Q3 Retainer in Qualified he is Technical Lead, medium priority, with Hex Wireless. "
    "Rohit Marthak is a Python AI Engineer in Bengaluru, Karnataka, India, "
    "reachable at rohitmarthak@appscrip.co and +919823929922. "
    "He prefers communication in English and his insurance is through Policy Bazaar.\n\n"
    "END OF CONTACT EXAMPLE.\n\n"
    "EXAMPLE INPUT — Appscrip company fragments:\n"
    "Company: Appscrip. Industry: Technology. Location: Bengaluru, Karnataka, India. "
    "Status: active. Website: appscrip.co. "
    "Primary contact: Rohit Marthak — Python AI Engineer — rohitmarthak@appscrip.co. "
    "Other contacts: Avinash Singh (Python AI Engineer), Preet Morbia (Full Stack Developer). "
    "Deals: Appscrip Platform Renewal — stage Proposal — amount INR 450000 — "
    "close 2026-07-31 — label Client — priority high. "
    "Appscrip Onboarding — stage Consultation — priority medium. "
    "Notes: Key decision maker is the CTO. "
    "Procurement requires a three-quote process. Legal review of MSA is pending. "
    "updated_at: 2026-05-20T09:00:00.\n\n"
    "EXAMPLE OUTPUT:\n"
    "The key decision maker at Appscrip is the CTO. "
    "Procurement requires a three-quote process and legal review of the MSA is pending. "
    "The Appscrip Platform Renewal is in Proposal, INR 450,000, closing 31 July 2026, "
    "high priority. "
    "The Appscrip Onboarding is in Consultation, medium priority. "
    "Appscrip is an active technology company in Bengaluru, Karnataka, India (appscrip.co). "
    "Primary contact Rohit Marthak, Python AI Engineer, rohitmarthak@appscrip.co; "
    "also Avinash Singh and Preet Morbia on the team.\n\n"
    "END OF COMPANY EXAMPLE.\n\n"
    "EXAMPLE INPUT — lead fragments:\n"
    "Lead: Appscrip Platform Renewal. Stage: Proposal. Priority: high. "
    "Deal type: Existing Business. Amount: INR 450000. Close date: 2026-07-31. "
    "Lead source: Referral. "
    "Contacts: Rohit Marthak — Python AI Engineer — rohitmarthak@appscrip.co "
    "— label Decision Maker. "
    "Avinash Singh — Python AI Engineer — avinashsingh@appscrip.co — label Technical Lead. "
    "Companies: Appscrip — Technology — label Client. "
    "Notes: Proposal sent on 15 May. Client requested a 10 percent discount on the setup fee. "
    "Follow up on SLA terms before end of month. "
    "Email context: Rohit confirmed in email dated 18 May that legal will revert by 25 May.\n\n"
    "EXAMPLE OUTPUT:\n"
    "The proposal was sent on 15 May. The client requested a 10 percent discount "
    "on the setup fee and SLA terms need follow-up before end of month. "
    "The Appscrip Platform Renewal is in Proposal, INR 450,000, close 31 July 2026, "
    "referral-sourced, high priority; Appscrip (Technology) is Client on the deal. "
    "Rohit Marthak (Decision Maker, rohitmarthak@appscrip.co) and Avinash Singh "
    "(Technical Lead, avinashsingh@appscrip.co) are engaged. "
    "Rohit confirmed via email on 18 May that legal will revert by 25 May.\n\n"
    "END OF LEAD EXAMPLE.\n\n"
    # ── Writing rules ──────────────────────────────────────────────────────────
    "WRITING RULES:\n"
    "- Write one continuous block of flowing prose. "
    "Do not use newline or line-break characters anywhere in the response. "
    "Separate logical sections with periods and spaces only. "
    "No bullet points, dashes, numbered lists, headers, or markdown of any kind.\n"
    "- Open with notes, then lead amounts and company/deal context when present; "
    "do not bury notes, deal value, or company associations after profile, skills, or education.\n"
    "- First mention of the person or company name may appear in the opening note sentence; "
    "after that use He, She, They, or The company.\n"
    "- Notes must appear verbatim or near-verbatim. Never compress or paraphrase them.\n"
    "- Spend more words on notes, deal amounts, and company associations than on "
    "skills, education, or address lines.\n"
    "- Always state deal amount with currency when the CRM notes include it.\n"
    "- When listing multiple deals or companies, write all of them. "
    "Never write 'and others' or truncate.\n"
    "- Write amounts with currency and formatting: 'INR 450,000' not '450000'.\n"
    "- Write dates in natural form: '31 July 2026' not '2026-07-31'.\n"
    "- Write deal labels naturally in context: "
    "'he is the Decision Maker on this deal' not 'label: Decision Maker'.\n"
    "- Write custom fields as prose: "
    "'his insurance is through ICICI' not 'Insurance Company: ICICI'.\n"
    "- For email signals: state the business fact, not the email itself. "
    "'He confirmed legal will revert by 25 May' not "
    "'An email from Rohit says legal will revert by 25 May'.\n\n"
    # ── Content rules ──────────────────────────────────────────────────────────
    "CONTENT RULES:\n"
    "- Use only facts explicitly present in the provided notes.\n"
    "- When the same field appears with different values, "
    "use the most recent one silently.\n"
    "- A field with no value is omitted entirely — no mention, no placeholder.\n"
    "- For deal fields specifically: if amount is absent, omit it. "
    "If close date is absent, omit it. If a label is absent, omit it. "
    "If priority is absent, omit it. State only what is present.\n"
    "- Skip any URL or domain containing 'example.com'. Do not mention the skip.\n"
    "- Skip raw timestamps, ISO datetime strings, updated_at values, database IDs. "
    "Do not mention skipping them.\n"
    "- Each fact appears exactly once across the entire output.\n"
    "- Merge all fragments for the same entity silently into one output. "
    "Never write the same entity twice.\n\n"
    # ── Banned ─────────────────────────────────────────────────────────────────
    "BANNED — never write any of the following under any circumstance:\n"
    "- Newline characters, line breaks, or blank lines between sentences.\n"
    "- Bullet points, dashes as list markers, numbered lists, headers, or any markdown.\n"
    "- 'not provided', 'not listed', 'not specified', 'not available', "
    "'not shown', 'not included', 'not set', 'not assigned', 'not yet set', "
    "'no amount', 'no close date', 'no label', 'no value', 'no currency', "
    "'with no amount', 'with no date', 'amount not set', 'unset', 'none set'.\n"
    "- 'based on', 'the notes show', 'the CRM', 'the record', 'the data', "
    "'according to', 'as per', 'the data indicates', 'pulled from'.\n"
    "- Raw timestamps, ISO datetime strings, 'updated_at', 'last updated', "
    "'updated in the database'.\n"
    "- 'here is', 'here are', 'if you would like', 'I can', 'let me know', "
    "'feel free', 'would you like', 'I have', 'please note'.\n"
    "- Any sentence explaining what was omitted, skipped, or ignored.\n"
    "- Any closing sentence offering further help or asking a question.\n"
    "- Starting the response with 'I', 'Here', 'Based on', or 'According to'.\n"
    "- Parenthetical gaps of any kind: '(not provided)', '(omitted)', "
    "'(which is omitted here due to the domain rule)', '(see above)'.\n"
    "- The phrase 'domain rule' or any reference to internal processing logic."
)


def _flatten_answer_for_response(text: str) -> str:
    """Return the API answer as one line with no newline characters."""
    return " ".join((text or "").replace("\r", " ").split()).strip()


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


def _pin_synth_instruction(raw: str) -> str:
    """Ensure synthesize instructions enforce notes/emails-first sales intelligence."""
    instruction = raw.strip().rstrip(".")
    if not instruction.startswith(_SYNTH_INSTRUCTION_REQUIRED_LEAD):
        instruction = f"{_SYNTH_INSTRUCTION_REQUIRED_LEAD}. {instruction}"
    if not instruction.endswith(_SYNTH_INSTRUCTION_REQUIRED_TAIL):
        instruction = f"{instruction}. {_SYNTH_INSTRUCTION_REQUIRED_TAIL}"
    return instruction


def _snapshot_section_sort_key(heading: str) -> int:
    """Order CRM sections for synthesis: notes → deals/companies → emails → rest."""
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
    """Parse ``# Contact:`` / ``# Company:`` / ``# Lead:`` header when metadata is missing."""
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
    """Unix ``updated_at`` from sync metadata (0 when missing)."""
    meta = hit.metadata or {}
    raw = meta.get("updated_at")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    return 0


def _sync_generation_hits(hits: list[SupermemorySearchHit]) -> list[SupermemorySearchHit]:
    """Return every search hit from the newest CRM sync generation for one entity.

    Hybrid search often returns multiple chunks of the same document. Keeping only the
    highest-scoring chunk dropped Notes, pipeline, and profile sections in sibling
    chunks. When ``updated_at`` is present, all hits at that timestamp are merged;
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
    """Supermemory metadata filter matching sync ``_base_metadata`` fields."""
    return {
        "AND": [
            {"key": "entity_type", "value": entity_type},
            {"key": "entity_id", "value": entity_id},
        ]
    }


def _merge_entity_snippets(hits: list[SupermemorySearchHit]) -> str:
    """Combine search fragments for one CRM record.

    When sync snapshots exist, merge every chunk from the newest ``updated_at`` so hybrid
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
            synth_instruction = _pin_synth_instruction(plan.synthesize_instruction)
            synth_user = (
                f"{scope_line}"
                f"Question: {user_message}\n\n"
                f"{_SYNTH_USER_ANSWER_ORDER}\n\n"
                f"CRM notes:\n{notes}\n\n"
                f"Instruction: {synth_instruction}"
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
        answer = _flatten_answer_for_response(answer)
        logger.info(
            "org_memory_query organization_id=%s search_hits=%s entities=%s "
            "notes_len=%s notes_truncated=%s used_fallback=%s answer_len=%s",
            organization_id,
            len(merged),
            len(usable),
            len(notes),
            notes_truncated,
            used_fallback,
            len(answer),
        )

        return answer
