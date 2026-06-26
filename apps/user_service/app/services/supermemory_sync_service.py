"""CRM snapshot formatting helpers shared by Graphiti sync and legacy tests.

Kafka lifecycle events are handled by ``GraphitiSyncService``; this module keeps
the markdown/formatting helpers used when building canonical CRM snapshots.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

import asyncpg

from apps.user_service.app.schemas.enums import (
    ClientEventType,
    CompanyEventType,
    ContactEventType,
)
from apps.user_service.app.schemas.enums import EntityType as CustomFieldEntityType
from apps.user_service.app.schemas.enums import (
    LeadEventType,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.services.typesense_index_service import (
    _build_contact_full_name,
    _extract_contact_social_urls,
    _extract_contact_websites,
    _extract_phone_numbers_and_display,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.graphiti_crm_models import (
    WorkHistoryEntry,
    work_history_entry_display_line,
)

EntityType = Literal["contact", "company", "lead"]
_SCHEMA_VERSION = 2
_TAGS_METADATA_MAX_LEN = 2000

_ENRICHMENT_REQUESTED_EVENTS = frozenset(
    {
        ContactEventType.ENRICHMENT_REQUESTED,
        CompanyEventType.ENRICHMENT_REQUESTED,
        ClientEventType.ENRICHMENT_REQUESTED,
    }
)

# CRM lifecycle events that trigger a Supermemory upsert (excludes enrichment_requested).
_EVENT_TYPE_TO_ENTITY: dict[str, EntityType] = {
    **{
        event.value: "contact"
        for event in ContactEventType
        if event not in _ENRICHMENT_REQUESTED_EVENTS
    },
    **{
        event.value: "company"
        for event in CompanyEventType
        if event not in _ENRICHMENT_REQUESTED_EVENTS
    },
    **{event.value: "lead" for event in LeadEventType},
}


def custom_id_for_entity(entity_type: EntityType, entity_id: str) -> str:
    """Stable document id for idempotent upserts."""
    return f"crm:{entity_type}:{entity_id}"


def resolve_sync_targets(
    *,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, Any] | None,
) -> list[tuple[EntityType, str]]:
    """Map a CRM lifecycle event to entity sync targets (primary aggregate only)."""
    entity_id = str(aggregate_id)

    mapped_entity = _EVENT_TYPE_TO_ENTITY.get(event_type)
    if mapped_entity is not None:
        return [(mapped_entity, entity_id)]

    if event_type == ClientEventType.CREATED.value:
        client_type = str((payload or {}).get("client_type") or "").strip().lower()
        if client_type == "companies":
            return [("company", entity_id)]
        if client_type == "contacts":
            return [("contact", entity_id)]

    return []


def _unix_ts(value: Any) -> int:
    """Coerce a DB timestamp or ISO string to Unix epoch seconds."""
    if value is None:
        return 0
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(normalized).timestamp())
        except ValueError:
            return 0
    return 0


def _section(title: str, body: str) -> str:
    """Return a markdown ``##`` section, or empty string when *body* is blank."""
    body_stripped = body.strip()
    if not body_stripped:
        return ""
    return f"## {title}\n{body_stripped}\n\n"


def _bullet_lines(items: Iterable[str]) -> str:
    """Join non-empty *items* into a single markdown bullet block."""
    lines = [f"- {item}" for item in items if item and str(item).strip()]
    return "\n".join(lines)


def _optional_kv_line(label: str, value: Any, *, yes_no: bool = False) -> str | None:
    """Return ``Label: value`` only when *value* is present (no empty or N/A placeholders)."""
    if value is None:
        return None
    if yes_no and isinstance(value, bool):
        return f"{label}: {'Yes' if value else 'No'}"
    if isinstance(value, bool):
        return f"{label}: {value}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{label}: {value}"
    text = str(value).strip()
    if not text:
        return None
    return f"{label}: {text}"


def _normalize_tags(value: Any) -> list[str]:
    """Return deduplicated tag strings from a DB ``tags`` column (list or JSON string)."""
    raw = parse_json_field(value) if isinstance(value, str) else value
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen_lower: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        tags.append(tag)
    return tags


def _tags_csv(tags: list[str]) -> str:
    """Comma-separated tags for Supermemory metadata (flat scalar filter)."""
    if not tags:
        return ""
    return ",".join(tags)[:_TAGS_METADATA_MAX_LEN]


def _parse_json_list(value: Any) -> list[Any]:
    """Coerce a DB JSONB list column (list or JSON string) to a Python list."""
    if value is None:
        return []
    parsed = parse_json_field(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


def _parse_json_dict(value: Any) -> dict[str, Any]:
    """Coerce a DB JSONB object column (dict or JSON string) to a Python dict."""
    if value is None:
        return {}
    parsed = parse_json_field(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _format_scalar_date(value: Any) -> str:
    """Format date/datetime/ISO string for markdown."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10] if hasattr(value, "day") else value.isoformat()
    return str(value).strip()


def _prepare_contact_row(details: dict[str, Any]) -> dict[str, Any]:
    """Normalize a repository contact row for markdown snapshot building."""
    row = dict(details)
    for field_name in (
        "phones",
        "notes",
        "custom_fields",
        "social_pages",
        "work_history",
        "educational_history",
        "skills",
        "companies",
        "leads",
        "addresses",
    ):
        row[field_name] = _parse_json_list(row.get(field_name))
    row["additional_data"] = _parse_json_dict(row.get("additional_data"))
    return row


def _format_phone_bullets(details: dict[str, Any]) -> list[str]:
    """Human-readable phone lines (handles JSONB items stored as stringified dicts)."""
    _, phones_display = _extract_phone_numbers_and_display(details)
    lines: list[str] = []
    for phone in phones_display:
        number = (phone.get("phone_number") or "").strip()
        if not number:
            continue
        isd = (phone.get("phone_isd_code") or "").strip()
        label = (phone.get("label") or "").strip()
        primary = " (primary)" if phone.get("is_primary") else ""
        formatted = f"{isd}{number}" if isd else number
        suffix = f" — {label}" if label else ""
        lines.append(f"{formatted}{primary}{suffix}")
    return lines


def _format_note_bullets(notes: Any) -> list[str]:
    """Structured notes as title + content bullets."""
    lines: list[str] = []
    for item in _parse_json_list(notes):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if not title and not content:
            continue
        if title and content:
            lines.append(f"{title}: {content}")
        else:
            lines.append(title or content)
    return lines


def _format_website_bullets(details: dict[str, Any]) -> list[str]:
    """Websites from additional_data.websites (contact create stores them there)."""
    lines: list[str] = []
    for url in _extract_contact_websites(details):
        lines.append(url)
    additional = _parse_json_dict(details.get("additional_data"))
    for item in _parse_json_list(additional.get("websites")):
        if isinstance(item, dict):
            url = (item.get("url") or "").strip()
            site_type = (item.get("type") or "").strip()
            primary = " (primary)" if item.get("is_primary") else ""
            if url:
                suffix = f" ({site_type})" if site_type else ""
                line = f"{url}{suffix}{primary}"
                if line not in lines:
                    lines.append(line)
    return lines


def _format_social_bullets(details: dict[str, Any]) -> list[str]:
    """Social profile links."""
    lines: list[str] = []
    for url in _extract_contact_social_urls(details):
        lines.append(url)
    for item in _parse_json_list(details.get("social_pages")):
        if not isinstance(item, dict):
            continue
        platform = (item.get("platform") or "").strip()
        url = (item.get("url") or item.get("link") or "").strip()
        if url:
            lines.append(f"{platform}: {url}" if platform else url)
    return lines


def _format_work_history_bullets(work_history: Any) -> list[str]:
    """Summarize work history entries as compact bullet strings."""
    lines: list[str] = []
    for item in _parse_json_list(work_history):
        if not isinstance(item, dict):
            continue
        entry = WorkHistoryEntry.model_validate(item)
        line = work_history_entry_display_line(entry)
        if line:
            lines.append(line)
    return lines


def _format_education_bullets(education: Any) -> list[str]:
    """Summarize education entries as compact bullet strings."""
    lines: list[str] = []
    for item in _parse_json_list(education):
        if not isinstance(item, dict):
            continue
        institution = (
            item.get("institution")
            or item.get("school")
            or item.get("university")
            or item.get("college")
            or ""
        )
        institution = institution.strip() if isinstance(institution, str) else ""
        degree = item.get("degree") or item.get("qualification") or item.get("field_of_study") or ""
        degree = degree.strip() if isinstance(degree, str) else ""
        if institution and degree:
            lines.append(f"{degree}, {institution}")
        elif institution:
            lines.append(institution)
        elif degree:
            lines.append(degree)
    return lines


def _custom_field_scalar_display(raw: Any) -> str:
    """Format a leaf custom-field *value* for inline display."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, str):
        return raw.strip()
    return str(raw)


def _custom_field_stale_display(node: dict[str, Any]) -> str:
    """Describe a stale/outdated custom-field cell."""
    old_value = node.get("old_value")
    return f"(outdated value) {old_value}" if old_value is not None else "(outdated)"


def _join_sub_field_displays(sub_fields: list[Any]) -> str:
    """Join composite custom-field *sub_fields* into one display string."""
    parts: list[str] = []
    for sub in sub_fields:
        if not isinstance(sub, dict):
            continue
        label = (sub.get("label") or sub.get("field_key") or "Field").strip()
        value = _custom_field_value_display(sub)
        if value:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def _join_item_displays(items: list[Any]) -> str:
    """Join multi-value custom-field *items* into one display string."""
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = _custom_field_value_display(item)
            if value:
                parts.append(value)
    return "; ".join(parts)


def _custom_field_value_display(node: dict[str, Any]) -> str:
    """Render one resolved custom-field node for Supermemory markdown."""
    if node.get("_stale"):
        return _custom_field_stale_display(node)

    sub_fields = node.get("sub_fields")
    if isinstance(sub_fields, list) and sub_fields:
        return _join_sub_field_displays(sub_fields)

    items = node.get("items")
    if isinstance(items, list) and items:
        return _join_item_displays(items)

    return _custom_field_scalar_display(node.get("value"))


def _format_resolved_custom_field_bullets(resolved: list[dict[str, Any]]) -> list[str]:
    """Custom fields with human labels (from field definitions), not raw field_id JSON."""
    lines: list[str] = []
    for node in resolved:
        if not isinstance(node, dict):
            continue
        label = (node.get("label") or node.get("field_key") or "Custom field").strip()
        value = _custom_field_value_display(node)
        if value:
            lines.append(f"{label}: {value}")
    return lines


async def _resolve_entity_custom_fields_for_snapshot(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    entity_type: CustomFieldEntityType,
    stored_custom_fields: Any,
) -> list[dict[str, Any]]:
    """Resolve stored custom-field cells to read-model dicts for Graphiti snapshots."""
    roots = _parse_json_list(stored_custom_fields)
    if not roots:
        return []
    user_context = UserContext(user_id="", email="", organization_id=organization_id)
    cfs = CustomFieldService(db_connection=db_connection, user_context=user_context)
    definitions, _ = await cfs.get_custom_fields_list(
        entity_type,
        organization_id=organization_id,
    )
    id_to_def = {str(defn.id): defn for defn in definitions}
    resolved = cfs.resolve_fields_for_read(roots, id_to_def)
    return [node for node in resolved if isinstance(node, dict)]


def _linked_company_lines(companies: Any) -> list[str]:
    """Format linked company rows as display lines for contact snapshots."""
    company_lines: list[str] = []
    if isinstance(companies, list):
        for entry in companies:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or ""
            industry = entry.get("industry") or ""
            primary = " (primary)" if entry.get("is_primary") else ""
            if name or industry:
                company_lines.append(f"{name}{primary}" + (f" — {industry}" if industry else ""))
    return company_lines


def _linked_lead_lines(leads: Any) -> list[str]:
    """Format linked lead rows as display lines for contact snapshots."""
    lead_lines: list[str] = []
    if isinstance(leads, list):
        for lead in leads:
            if not isinstance(lead, dict):
                continue
            lname = (lead.get("name") or "").strip() or None
            stage = (lead.get("stage_name") or lead.get("stage_id") or "").strip() or None
            amount = lead.get("amount")
            amount_s = "" if amount is None else str(amount).strip()
            bits: list[str] = []
            if lname:
                bits.append(lname)
            if stage:
                bits.append(f"stage: {stage}")
            if amount_s:
                bits.append(f"amount: {amount_s}")
            if bits:
                lead_lines.append(" — ".join(bits))
    return lead_lines


def _contact_address_lines(addresses: Any) -> list[str]:
    """Format address rows as display lines for contact snapshots."""
    address_lines: list[str] = []
    if isinstance(addresses, list):
        for addr in addresses:
            if not isinstance(addr, dict):
                continue
            parts = [
                addr.get("address_line1"),
                addr.get("address_line2"),
                addr.get("city"),
                addr.get("state"),
                addr.get("country"),
                addr.get("postal_code"),
            ]
            line = ", ".join(str(p) for p in parts if p)
            if line:
                primary = " (primary)" if addr.get("is_primary") else ""
                address_lines.append(f"{line}{primary}")
    return address_lines


def _contact_profile_lines(
    details: dict[str, Any],
    *,
    contact_id: str,
    intake_stage: str,
    preferred_language: str,
) -> list[str]:
    """Build the bullet lines for the contact profile section."""
    dob = _format_scalar_date(details.get("date_of_birth"))
    profile_lines: list[str] = []
    if contact_id.strip():
        profile_lines.append(f"ID: {contact_id}")
    for line in (
        _optional_kv_line("Email", details.get("email")),
        _optional_kv_line("Title", details.get("title")),
        _optional_kv_line("Date of birth", dob or None),
        _optional_kv_line("Status", details.get("status")),
    ):
        if line:
            profile_lines.append(line)
    if details.get("enrichment_done") is not None:
        enrichment_done_line = _optional_kv_line(
            "Enrichment done", details.get("enrichment_done"), yes_no=True
        )
        if enrichment_done_line:
            profile_lines.append(enrichment_done_line)
    if intake_stage:
        profile_lines.append(f"Intake stage: {intake_stage}")
    if preferred_language:
        profile_lines.append(f"Preferred language: {preferred_language}")
    return profile_lines


def _build_contact_content(
    details: dict[str, Any],
    *,
    custom_field_lines: list[str] | None = None,
) -> str:
    """Assemble the markdown body for a contact Supermemory snapshot."""
    contact_id = str(details.get("id") or "")
    full_name = _build_contact_full_name(details) or (
        f"{details.get('first_name') or ''} {details.get('last_name') or ''}".strip()
    )

    company_lines = _linked_company_lines(details.get("companies") or [])
    lead_lines = _linked_lead_lines(details.get("leads") or [])
    address_lines = _contact_address_lines(details.get("addresses") or [])

    contact_tags = _normalize_tags(details.get("tags"))
    additional = _parse_json_dict(details.get("additional_data"))
    intake_stage = (additional.get("intake_stage") or "").strip() if additional else ""
    preferred_language = (additional.get("preferred_language") or "").strip() if additional else ""

    profile_lines = _contact_profile_lines(
        details,
        contact_id=contact_id,
        intake_stage=intake_stage,
        preferred_language=preferred_language,
    )

    skill_lines = [
        s.strip()
        for s in _parse_json_list(details.get("skills"))
        if isinstance(s, str) and s.strip()
    ]

    parts = [
        f"# Contact: {full_name or contact_id}\n",
        _section("Profile", _bullet_lines(profile_lines)),
        _section("Tags", _bullet_lines(contact_tags)),
        _section("Companies", _bullet_lines(company_lines)),
        _section("Linked leads", _bullet_lines(lead_lines)),
        _section("Addresses", _bullet_lines(address_lines)),
        _section("Phones", _bullet_lines(_format_phone_bullets(details))),
        _section("Websites", _bullet_lines(_format_website_bullets(details))),
        _section("Social", _bullet_lines(_format_social_bullets(details))),
        _section("Notes", _bullet_lines(_format_note_bullets(details.get("notes")))),
        _section("Skills", _bullet_lines(skill_lines)),
        _section(
            "Work history", _bullet_lines(_format_work_history_bullets(details.get("work_history")))
        ),
        _section(
            "Education",
            _bullet_lines(_format_education_bullets(details.get("educational_history"))),
        ),
        _section("Custom fields", _bullet_lines(custom_field_lines or [])),
    ]
    updated_iso = format_iso_datetime(details.get("updated_at"))
    if updated_iso:
        parts.append(f"Last updated (DB): {updated_iso}\n")
    return "".join(parts)


def _prepare_company_row(details: dict[str, Any]) -> dict[str, Any]:
    """Normalize a repository company row for markdown snapshot building."""
    row = dict(details)
    for field_name in (
        "phones",
        "notes",
        "custom_fields",
        "social_pages",
        "websites",
        "addresses",
        "contacts",
        "products",
        "key_people",
    ):
        row[field_name] = _parse_json_list(row.get(field_name))
    row["additional_data"] = _parse_json_dict(row.get("additional_data"))
    return row
