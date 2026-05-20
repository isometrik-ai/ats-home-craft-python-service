"""Build CRM entity snapshots and upsert them into Supermemory.

Kafka lifecycle events are triggers only: each sync reloads the canonical entity
from Postgres (contacts, companies, leads with associations) and replaces the
matching Supermemory document via a stable ``customId``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg

from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
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
from apps.user_service.app.services.organization_memory_service import (
    is_organization_memory_enabled,
)
from apps.user_service.app.services.typesense_index_service import (
    _build_contact_full_name,
    _extract_company_phone_numbers_and_display,
    _extract_contact_company_linkage,
    _extract_contact_social_urls,
    _extract_contact_websites,
    _extract_phone_numbers_and_display,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.supermemory_service import (
    SupermemoryService,
    container_tag_for_organization,
)

logger = get_logger("supermemory_sync_service")

EntityType = Literal["contact", "company", "lead"]
_SCHEMA_VERSION = 2
_TAGS_METADATA_MAX_LEN = 2000
_ENTITY_CONTEXT = (
    "Legal CRM data. Contacts are people; companies are client organizations; "
    "leads are pipeline opportunities with stage, amount, owner, linked contacts "
    "and companies. Preserve association labels and custom fields."
)

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
        company = (item.get("company_name") or item.get("company") or "").strip()
        title = (item.get("title") or item.get("job_title") or item.get("position") or "").strip()
        if company and title:
            lines.append(f"{title} at {company}")
        elif company:
            lines.append(company)
        elif title:
            lines.append(title)
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


async def _resolve_contact_custom_field_bullets(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    stored_custom_fields: Any,
) -> list[str]:
    """Load field definitions and resolve stored cells to labeled display lines."""
    roots = _parse_json_list(stored_custom_fields)
    if not roots:
        return []
    user_context = UserContext(user_id="", email="", organization_id=organization_id)
    cfs = CustomFieldService(db_connection=db_connection, user_context=user_context)
    definitions, _ = await cfs.get_custom_fields_list(
        CustomFieldEntityType.CONTACT,
        organization_id=organization_id,
    )
    id_to_def = {str(defn.id): defn for defn in definitions}
    resolved = cfs.resolve_fields_for_read(roots, id_to_def)
    return _format_resolved_custom_field_bullets(resolved)


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


def _format_company_phone_bullets(details: dict[str, Any]) -> list[str]:
    """Human-readable phone lines for a company row."""
    _, phones_display = _extract_company_phone_numbers_and_display(details)
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


async def _resolve_company_custom_field_bullets(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    stored_custom_fields: Any,
) -> list[str]:
    """Like ``_resolve_contact_custom_field_bullets`` but for company entities."""
    roots = _parse_json_list(stored_custom_fields)
    if not roots:
        return []
    user_context = UserContext(user_id="", email="", organization_id=organization_id)
    cfs = CustomFieldService(db_connection=db_connection, user_context=user_context)
    definitions, _ = await cfs.get_custom_fields_list(
        CustomFieldEntityType.COMPANY,
        organization_id=organization_id,
    )
    id_to_def = {str(defn.id): defn for defn in definitions}
    resolved = cfs.resolve_fields_for_read(roots, id_to_def)
    return _format_resolved_custom_field_bullets(resolved)


def _company_linked_contact_lines(contacts: Any) -> list[str]:
    """Format linked contact rows for a company snapshot."""
    contact_lines: list[str] = []
    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            full = contact.get("full_name") or " ".join(
                p for p in (contact.get("first_name"), contact.get("last_name")) if p
            )
            title = contact.get("title") or ""
            email = contact.get("email") or ""
            primary = " (primary)" if contact.get("is_primary") else ""
            contact_lines.append(
                f"{full}{primary}"
                + (f" — {title}" if title else "")
                + (f" — {email}" if email else "")
            )
    return contact_lines


def _build_company_content(
    details: dict[str, Any],
    *,
    custom_field_lines: list[str] | None = None,
) -> str:
    """Assemble the markdown body for a company Supermemory snapshot."""
    company_id = str(details.get("id") or "")
    name = details.get("name") or company_id
    contact_lines = _company_linked_contact_lines(details.get("contacts") or [])
    address_lines = _contact_address_lines(_parse_json_list(details.get("addresses")))

    company_tags = _normalize_tags(details.get("tags"))
    description = (details.get("description") or "").strip()
    description_snippet = description[:500] if description else ""

    company_profile: list[str] = []
    if company_id.strip():
        company_profile.append(f"ID: {company_id}")
    for line in (
        _optional_kv_line("Industry", details.get("industry")),
        _optional_kv_line("Email", details.get("email")),
        _optional_kv_line("Status", details.get("status")),
        _optional_kv_line("Description", description_snippet or None),
    ):
        if line:
            company_profile.append(line)

    parts = [
        f"# Company: {name}\n",
        _section("Profile", _bullet_lines(company_profile)),
        _section("Tags", _bullet_lines(company_tags)),
        _section("Contacts", _bullet_lines(contact_lines)),
        _section("Addresses", _bullet_lines(address_lines)),
        _section("Phones", _bullet_lines(_format_company_phone_bullets(details))),
        _section("Notes", _bullet_lines(_format_note_bullets(details.get("notes")))),
        _section("Custom fields", _bullet_lines(custom_field_lines or [])),
    ]
    updated_iso = format_iso_datetime(details.get("updated_at"))
    if updated_iso:
        parts.append(f"Last updated (DB): {updated_iso}\n")
    return "".join(parts)


def _build_lead_content(row: dict[str, Any], contacts: list[dict[str, Any]]) -> str:
    """Assemble the markdown body for a lead Supermemory snapshot."""
    lead_id = str(row.get("id") or "")
    name = row.get("name") or lead_id
    companies = parse_json_field(row.get("companies")) or []
    company_lines: list[str] = []
    if isinstance(companies, list):
        for company in companies:
            if isinstance(company, dict):
                cname = (company.get("name") or "").strip() or (
                    str(company.get("company_id")).strip() if company.get("company_id") else ""
                )
                if cname:
                    company_lines.append(cname)

    contact_lines: list[str] = []
    for contact in contacts:
        label = contact.get("label") or ""
        cname = contact.get("contact_name") or contact.get("contact_id") or "Contact"
        email = contact.get("contact_email") or contact.get("email") or ""
        contact_lines.append(
            f"{cname}" + (f" ({label})" if label else "") + (f" — {email}" if email else "")
        )

    deal_lines: list[str] = []
    if lead_id.strip():
        deal_lines.append(f"ID: {lead_id}")
    stage = row.get("stage_name") or row.get("stage_id")
    for line in (
        _optional_kv_line("Stage", stage),
        _optional_kv_line("Priority", row.get("priority")),
        _optional_kv_line(
            "Amount",
            (
                f"{row.get('amount')} {row.get('currency') or ''}".strip()
                if row.get("amount") is not None
                else None
            ),
        ),
        _optional_kv_line("Owner", row.get("owner_name") or row.get("owner_id")),
        _optional_kv_line("Close date", row.get("close_date")),
        _optional_kv_line("Lead score", row.get("lead_score")),
    ):
        if line:
            deal_lines.append(line)

    parts = [
        f"# Lead: {name}\n",
        _section("Deal", _bullet_lines(deal_lines)),
        _section("Companies", _bullet_lines(company_lines)),
        _section("Contacts", _bullet_lines(contact_lines)),
        _section("Notes", _bullet_lines(_format_note_bullets(row.get("notes")))),
        _section(
            "Description",
            ((row.get("description") or "").strip()[:2000] if row.get("description") else ""),
        ),
    ]
    updated_iso = format_iso_datetime(row.get("updated_at"))
    if updated_iso:
        parts.append(f"Last updated (DB): {updated_iso}\n")
    return "".join(parts)


class SupermemorySyncService:
    """Load CRM entities from Postgres and push narrative snapshots to Supermemory."""

    def __init__(self, *, supermemory: SupermemoryService | None = None) -> None:
        self._supermemory = supermemory or SupermemoryService.from_settings()

    async def process_crm_event(
        self,
        db_connection: asyncpg.Connection,
        event: dict[str, Any],
    ) -> None:
        """Handle one CRM Kafka event envelope.

        No-op when ``organization_memory`` is disabled for the event's organization.
        """
        organization_id = str(event.get("organization_id") or "")
        event_type = str(event.get("event_type") or "")
        aggregate_id = str(event.get("aggregate_id") or "")
        event_id = str(event.get("event_id") or "")

        if not organization_id:
            logger.info(
                "supermemory_sync_noop missing organization_id event_id=%s "
                "event_type=%s aggregate_id=%s",
                event_id,
                event_type,
                aggregate_id,
            )
            return
        if not await is_organization_memory_enabled(db_connection, organization_id):
            logger.info(
                "supermemory_sync_noop organization_memory disabled "
                "organization_id=%s event_id=%s event_type=%s aggregate_id=%s",
                organization_id,
                event_id,
                event_type,
                aggregate_id,
            )
            return
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}

        targets = resolve_sync_targets(
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload_dict,
        )
        if not targets:
            logger.info(
                "supermemory_sync_noop no sync targets event_id=%s event_type=%s "
                "aggregate_id=%s organization_id=%s",
                event_id,
                event_type,
                aggregate_id,
                organization_id,
            )
            return

        targets_label = ",".join(f"{entity_type}:{entity_id}" for entity_type, entity_id in targets)
        logger.info(
            "supermemory_sync_processing event_id=%s event_type=%s organization_id=%s targets=%s",
            event_id,
            event_type,
            organization_id,
            targets_label,
        )
        for entity_type, entity_id in targets:
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if entity_type == "contact":
                await self._cascade_contact_associations(
                    db_connection,
                    organization_id=organization_id,
                    contact_id=entity_id,
                )
            elif entity_type == "company":
                await self._cascade_company_associations(
                    db_connection,
                    organization_id=organization_id,
                    company_id=entity_id,
                )
            elif entity_type == "lead":
                await self._cascade_lead_associations(
                    db_connection,
                    organization_id=organization_id,
                    lead_id=entity_id,
                )

    async def _cascade_contact_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        contact_id: str,
    ) -> None:
        """Re-sync companies linked to a contact (contact create often skips companies.created)."""
        repo = ContactsRepository(db_connection=db_connection)
        details = await repo.get_contact_details(
            contact_id=contact_id,
            organization_id=organization_id,
        )
        if not details:
            return
        company_ids, _ = _extract_contact_company_linkage(details)
        for company_id in company_ids:
            await self.sync_entity(
                db_connection,
                organization_id=organization_id,
                entity_type="company",
                entity_id=company_id,
            )

    async def _cascade_company_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        company_id: str,
    ) -> None:
        """Re-sync contacts linked to a company after company changes."""
        repo = CompaniesRepository(db_connection=db_connection)
        details = await repo.get_company_details(
            company_id=company_id,
            organization_id=organization_id,
        )
        if not details:
            return
        contacts = details.get("contacts") or []
        if not isinstance(contacts, list):
            return
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            contact_id = contact.get("id")
            if contact_id:
                await self.sync_entity(
                    db_connection,
                    organization_id=organization_id,
                    entity_type="contact",
                    entity_id=str(contact_id),
                )

    async def _cascade_lead_associations(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        lead_id: str,
    ) -> None:
        """Re-sync companies and contacts linked to a lead after lead changes."""
        lead_repo = LeadRepository(db_connection=db_connection)
        row = await lead_repo.get_lead_detail_with_contacts_by_id(
            organization_id,
            lead_id,
            owner_id=None,
        )
        if not row:
            return
        companies = parse_json_field(row.get("companies")) or []
        if isinstance(companies, list):
            for company in companies:
                if not isinstance(company, dict):
                    continue
                cid = company.get("company_id") or company.get("id")
                if cid:
                    await self.sync_entity(
                        db_connection,
                        organization_id=organization_id,
                        entity_type="company",
                        entity_id=str(cid),
                    )
        contacts = row.get("contacts") or []
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                cid = contact.get("contact_id")
                if cid:
                    await self.sync_entity(
                        db_connection,
                        organization_id=organization_id,
                        entity_type="contact",
                        entity_id=str(cid),
                    )

    async def sync_entity(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> None:
        """Load canonical entity state and upsert one Supermemory document."""
        if not await is_organization_memory_enabled(db_connection, organization_id):
            return

        snapshot = await self._load_snapshot(
            db_connection,
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if snapshot is None:
            logger.info(
                "supermemory_sync_skipped_missing_entity type=%s id=%s org=%s",
                entity_type,
                entity_id,
                organization_id,
            )
            return

        content, metadata = snapshot
        await self._supermemory.add_or_replace_document(
            content=content,
            container_tag=container_tag_for_organization(organization_id),
            custom_id=custom_id_for_entity(entity_type, entity_id),
            metadata=metadata,
            entity_context=_ENTITY_CONTEXT,
        )

    async def _load_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> tuple[str, dict[str, str | int | float | bool]] | None:
        """Return Supermemory document content and metadata for *entity_type*, or None."""
        if entity_type == "contact":
            return await self._load_contact_snapshot(
                db_connection,
                organization_id=organization_id,
                contact_id=entity_id,
            )
        if entity_type == "company":
            return await self._load_company_snapshot(
                db_connection,
                organization_id=organization_id,
                company_id=entity_id,
            )
        return await self._load_lead_snapshot(
            db_connection,
            organization_id=organization_id,
            lead_id=entity_id,
        )

    async def _load_contact_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        contact_id: str,
    ) -> tuple[str, dict[str, str | int | float | bool]] | None:
        """Load contact state and produce a snapshot tuple, or a tombstone when missing/deleted."""
        repo = ContactsRepository(db_connection=db_connection)
        details = await repo.get_contact_details(
            contact_id=contact_id,
            organization_id=organization_id,
        )
        if not details:
            return self._tombstone_snapshot(
                entity_type="contact",
                entity_id=contact_id,
                organization_id=organization_id,
                display_name="Deleted contact",
            )

        status = str(details.get("status") or "active")
        if status == "deleted":
            display_name = _build_contact_full_name(details) or contact_id
            return self._tombstone_snapshot(
                entity_type="contact",
                entity_id=contact_id,
                organization_id=organization_id,
                display_name=display_name,
            )

        full_name = _build_contact_full_name(details) or contact_id
        company_ids, _ = _extract_contact_company_linkage(details)
        contact_tags = _normalize_tags(details.get("tags"))
        prepared = _prepare_contact_row(details)
        custom_field_lines = await _resolve_contact_custom_field_bullets(
            db_connection,
            organization_id=organization_id,
            stored_custom_fields=prepared.get("custom_fields"),
        )
        content = _build_contact_content(
            prepared,
            custom_field_lines=custom_field_lines,
        )
        metadata = self._base_metadata(
            entity_type="contact",
            entity_id=contact_id,
            organization_id=organization_id,
            status=status,
            display_name=full_name,
            primary_email=str(details.get("email") or ""),
            updated_at=_unix_ts(details.get("updated_at")),
            related_company_ids=",".join(company_ids),
            tags=_tags_csv(contact_tags),
        )
        return content, metadata

    async def _load_company_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        company_id: str,
    ) -> tuple[str, dict[str, str | int | float | bool]] | None:
        """Load company state and produce a snapshot tuple, or a tombstone when missing/deleted."""
        repo = CompaniesRepository(db_connection=db_connection)
        details = await repo.get_company_details(
            company_id=company_id,
            organization_id=organization_id,
        )
        if not details:
            return self._tombstone_snapshot(
                entity_type="company",
                entity_id=company_id,
                organization_id=organization_id,
                display_name="Deleted company",
            )

        status = str(details.get("status") or "active")
        name = str(details.get("name") or company_id)
        if status == "deleted":
            return self._tombstone_snapshot(
                entity_type="company",
                entity_id=company_id,
                organization_id=organization_id,
                display_name=name,
            )

        contacts = details.get("contacts") or []
        related_contact_ids = ",".join(
            str(c.get("id")) for c in contacts if isinstance(c, dict) and c.get("id")
        )
        company_tags = _normalize_tags(details.get("tags"))
        prepared = _prepare_company_row(details)
        custom_field_lines = await _resolve_company_custom_field_bullets(
            db_connection,
            organization_id=organization_id,
            stored_custom_fields=prepared.get("custom_fields"),
        )
        content = _build_company_content(
            prepared,
            custom_field_lines=custom_field_lines,
        )
        metadata = self._base_metadata(
            entity_type="company",
            entity_id=company_id,
            organization_id=organization_id,
            status=status,
            display_name=name,
            primary_email=str(details.get("email") or ""),
            updated_at=_unix_ts(details.get("updated_at")),
            related_contact_ids=related_contact_ids,
            tags=_tags_csv(company_tags),
        )
        return content, metadata

    async def _load_lead_snapshot(
        self,
        db_connection: asyncpg.Connection,
        *,
        organization_id: str,
        lead_id: str,
    ) -> tuple[str, dict[str, str | int | float | bool]] | None:
        """Load lead state and produce a snapshot tuple, or a tombstone when missing."""
        lead_repo = LeadRepository(db_connection=db_connection)
        row = await lead_repo.get_lead_detail_with_contacts_by_id(
            organization_id,
            lead_id,
            owner_id=None,
        )
        if not row:
            return self._tombstone_snapshot(
                entity_type="lead",
                entity_id=lead_id,
                organization_id=organization_id,
                display_name="Deleted lead",
            )

        contacts_raw = row.get("contacts") or []
        contacts = contacts_raw if isinstance(contacts_raw, list) else []
        name = str(row.get("name") or lead_id)
        content = _build_lead_content(row, contacts)
        metadata = self._base_metadata(
            entity_type="lead",
            entity_id=lead_id,
            organization_id=organization_id,
            status="active",
            display_name=name,
            primary_email="",
            updated_at=_unix_ts(row.get("updated_at")),
        )
        return content, metadata

    @staticmethod
    def _tombstone_snapshot(
        *,
        entity_type: EntityType,
        entity_id: str,
        organization_id: str,
        display_name: str,
    ) -> tuple[str, dict[str, str | int | float | bool]]:
        """Minimal deleted-entity document and metadata for deleted CRM rows."""
        content = (
            f"# {entity_type.title()}: {display_name}\n\n"
            "This CRM record was deleted or is no longer available.\n"
        )
        metadata = SupermemorySyncService._base_metadata(
            entity_type=entity_type,
            entity_id=entity_id,
            organization_id=organization_id,
            status="deleted",
            display_name=display_name,
            primary_email="",
            updated_at=int(datetime.now(UTC).timestamp()),
        )
        return content, metadata

    @staticmethod
    def _base_metadata(
        *,
        entity_type: EntityType,
        entity_id: str,
        organization_id: str,
        status: str,
        display_name: str,
        primary_email: str,
        updated_at: int,
        related_company_ids: str = "",
        related_contact_ids: str = "",
        tags: str = "",
    ) -> dict[str, str | int | float | bool]:
        """Shared Supermemory metadata fields for all CRM entity snapshots."""
        meta: dict[str, str | int | float | bool] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "organization_id": organization_id,
            "status": status,
            "display_name": display_name[:200],
            "primary_email": primary_email[:320],
            "updated_at": updated_at,
            "schema_version": _SCHEMA_VERSION,
            "source": "crm",
        }
        if related_company_ids:
            meta["related_company_ids"] = related_company_ids[:2000]
        if related_contact_ids:
            meta["related_contact_ids"] = related_contact_ids[:2000]
        if tags:
            meta["tags"] = tags[:_TAGS_METADATA_MAX_LEN]
        return meta
