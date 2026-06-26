"""Pydantic models for Graphiti CRM snapshots and graph extraction."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

_FALKORDB_PRIMITIVE_TYPES = (str, int, float, bool)

CrmEntityType = Literal["contact", "company", "lead"]
_SCHEMA_VERSION = 2


class CrmMetadata(BaseModel):
    """Maps 1:1 to Supermemory ``_base_metadata()``."""

    entity_type: CrmEntityType
    entity_id: str
    organization_id: str
    status: str
    display_name: str
    primary_email: str = ""
    updated_at: int
    schema_version: int = _SCHEMA_VERSION
    source: Literal["crm"] = "crm"
    related_company_ids: str = ""
    related_contact_ids: str = ""
    tags: str = ""


class PhoneEntry(BaseModel):
    """A phone number attached to a CRM entity."""

    phone_number: str | None = None
    phone_isd_code: str | None = None
    label: str | None = None
    is_primary: bool | None = None


class AddressEntry(BaseModel):
    """A postal address attached to a CRM entity."""

    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    is_primary: bool | None = None


class NoteEntry(BaseModel):
    """A free-form note on a CRM entity."""

    title: str | None = None
    content: str | None = None


class WebsiteEntry(BaseModel):
    """A website URL attached to a CRM entity."""

    url: str | None = None
    type: str | None = None
    is_primary: bool | None = None


class SocialPageEntry(BaseModel):
    """A social profile link attached to a CRM entity."""

    platform: str | None = None
    url: str | None = None


class WorkHistoryEntry(BaseModel):
    """Employment history — company name is informational; no CRM company entity required."""

    company_name: str | None = None
    company: str | None = None
    title: str | None = None
    job_title: str | None = None
    position: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    current: bool | None = None


class EducationEntry(BaseModel):
    """An education history row on a contact."""

    institution: str | None = None
    school: str | None = None
    university: str | None = None
    college: str | None = None
    degree: str | None = None
    qualification: str | None = None
    field_of_study: str | None = None


class ResolvedCustomField(BaseModel):
    """Output of ``CustomFieldService.resolve_fields_for_read``."""

    label: str | None = None
    field_key: str | None = None
    value: Any = None
    sub_fields: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    stale: bool = Field(False, alias="_stale")
    old_value: Any = None

    model_config = {"populate_by_name": True}


class LinkedCompanyRef(BaseModel):
    """CRM ``contact_companies`` link — always references an existing company record."""

    name: str | None = None
    industry: str | None = None
    is_primary: bool | None = None
    company_id: str | None = None


class LinkedLeadRef(BaseModel):
    """A lead linked to a contact."""

    lead_id: str | None = None
    name: str | None = None
    stage_name: str | None = None
    stage_id: str | None = None
    amount: Any = None


class LinkedContactRef(BaseModel):
    """A contact linked to a company or lead."""

    id: str | None = None
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    email: str | None = None
    is_primary: bool | None = None
    contact_id: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    label: str | None = None


class InboundEmailEntry(BaseModel):
    """Normalized inbound email payload for contact memory."""

    message_id: str
    subject: str | None = None
    from_header: str | None = None
    from_email: str
    to: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    received_at: str | None = None
    body: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ContactSnapshot(BaseModel):
    """Canonical JSON snapshot of a CRM contact."""

    crm_id: str
    prefix: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    display_name: str = ""
    email: str | None = None
    title: str | None = None
    date_of_birth: str | None = None
    status: str | None = None
    enrichment_done: bool | None = None
    intake_stage: str | None = None
    preferred_language: str | None = None
    tags: list[str] = Field(default_factory=list)
    linked_companies: list[LinkedCompanyRef] = Field(default_factory=list)
    linked_leads: list[LinkedLeadRef] = Field(default_factory=list)
    addresses: list[AddressEntry] = Field(default_factory=list)
    phones: list[PhoneEntry] = Field(default_factory=list)
    websites: list[WebsiteEntry] = Field(default_factory=list)
    website_urls: list[str] = Field(default_factory=list)
    social_pages: list[SocialPageEntry] = Field(default_factory=list)
    notes: list[NoteEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    work_history: list[WorkHistoryEntry] = Field(default_factory=list)
    educational_history: list[EducationEntry] = Field(default_factory=list)
    custom_fields: list[ResolvedCustomField] = Field(default_factory=list)
    metadata: CrmMetadata
    updated_at_db: datetime | None = None


class CompanySnapshot(BaseModel):
    """Canonical JSON snapshot of a CRM company."""

    crm_id: str
    name: str = ""
    display_name: str = ""
    industry: str | None = None
    email: str | None = None
    status: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    linked_contacts: list[LinkedContactRef] = Field(default_factory=list)
    addresses: list[AddressEntry] = Field(default_factory=list)
    phones: list[PhoneEntry] = Field(default_factory=list)
    notes: list[NoteEntry] = Field(default_factory=list)
    custom_fields: list[ResolvedCustomField] = Field(default_factory=list)
    metadata: CrmMetadata
    updated_at_db: datetime | None = None


class LeadSnapshot(BaseModel):
    """Canonical JSON snapshot of a CRM lead."""

    crm_id: str
    name: str = ""
    display_name: str = ""
    stage_name: str | None = None
    stage_id: str | None = None
    priority: str | None = None
    amount: Any = None
    currency: str | None = None
    owner_name: str | None = None
    owner_id: str | None = None
    close_date: Any = None
    lead_score: Any = None
    linked_companies: list[LinkedCompanyRef] = Field(default_factory=list)
    linked_contacts: list[LinkedContactRef] = Field(default_factory=list)
    notes: list[NoteEntry] = Field(default_factory=list)
    description: str | None = None
    metadata: CrmMetadata
    updated_at_db: datetime | None = None


CrmSnapshot = ContactSnapshot | CompanySnapshot | LeadSnapshot


# ---------------------------------------------------------------------------
# Slim graph entity types (LLM extraction + traversal)
# ---------------------------------------------------------------------------


class Contact(BaseModel):
    """A person who is a CRM contact."""

    crm_id: str = Field(..., description="Postgres contact UUID")
    display_name: str | None = None
    email: str | None = None
    title: str | None = None
    status: str | None = None


class Company(BaseModel):
    """An organization tracked in the CRM."""

    crm_id: str = Field(...)
    display_name: str | None = None
    industry: str | None = None
    email: str | None = None
    status: str | None = None


class Lead(BaseModel):
    """A sales opportunity."""

    crm_id: str = Field(...)
    display_name: str | None = None
    stage: str | None = None
    amount: float | None = None
    priority: str | None = None
    status: str | None = None


class LinkedToCrmCompany(BaseModel):
    """Explicit CRM association via ``contact_companies`` (not work history)."""

    is_primary: bool | None = None


class OwnsLead(BaseModel):
    """Lead association to contact or company."""

    relationship_type: str | None = None


class AssociatedWith(BaseModel):
    """Generic CRM association fallback."""

    label: str | None = None


ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "Contact": Contact,
    "Company": Company,
    "Lead": Lead,
}
EDGE_TYPES: dict[str, type[BaseModel]] = {
    "LinkedToCrmCompany": LinkedToCrmCompany,
    "OwnsLead": OwnsLead,
    "AssociatedWith": AssociatedWith,
}
EDGE_TYPE_MAP: dict[tuple[str, str], list[str]] = {
    ("Contact", "Company"): ["LinkedToCrmCompany"],
    ("Lead", "Contact"): ["OwnsLead"],
    ("Lead", "Company"): ["OwnsLead"],
    ("Entity", "Entity"): ["AssociatedWith"],
}


def entity_label_for_crm_type(crm_type: CrmEntityType) -> str:
    """Map CRM entity type to Graphiti entity label."""
    return {"contact": "Contact", "company": "Company", "lead": "Lead"}[crm_type]


def deterministic_entity_uuid(crm_type: CrmEntityType, crm_id: str) -> str:
    """Stable UUID for idempotent entity upserts."""
    return str(uuid5(NAMESPACE_URL, f"crm:{crm_type}:{crm_id}"))


def snapshot_episode_name(crm_type: CrmEntityType, crm_id: str) -> str:
    """Stable episodic node name for JSON CRM snapshots."""
    return f"crm_snapshot:{crm_type}:{crm_id}"


def custom_id_for_entity(entity_type: CrmEntityType, entity_id: str) -> str:
    """Backward-compatible stable id (Supermemory customId format)."""
    return f"crm:{entity_type}:{entity_id}"


def deterministic_episode_uuid(episode_name: str) -> str:
    """Stable UUID for episodic snapshot upserts."""
    return str(uuid5(NAMESPACE_URL, f"episode:{episode_name}"))


def deterministic_association_edge_uuid(
    source_uuid: str,
    target_uuid: str,
    edge_name: str,
) -> str:
    """Stable UUID for idempotent CRM association edge upserts."""
    return str(uuid5(NAMESPACE_URL, f"crm_edge:{source_uuid}:{target_uuid}:{edge_name}"))


def work_history_entry_display_line(entry: WorkHistoryEntry) -> str:
    """Format one work-history row using *works at* (current) vs *worked at* (past)."""
    company = (entry.company_name or entry.company or "").strip()
    title = (entry.title or entry.job_title or entry.position or "").strip()
    verb = "works at" if entry.current is True else "worked at"

    date_bits: list[str] = []
    if entry.start_date:
        date_bits.append(str(entry.start_date).strip())
    if entry.end_date:
        date_bits.append(str(entry.end_date).strip())
    dates = " – ".join(date_bits)

    if title and company:
        line = f"{title} {verb} {company}"
    elif company:
        line = f"{verb} {company}"
    elif title:
        line = title
    else:
        return ""

    if dates:
        line = f"{line} ({dates})"
    return line


def work_history_display_lines(entries: list[WorkHistoryEntry]) -> list[str]:
    """Non-empty display lines for contact work history."""
    lines: list[str] = []
    for entry in entries:
        line = work_history_entry_display_line(entry)
        if line:
            lines.append(line)
    return lines


def _falkordb_assign(attrs: dict[str, Any], key: str, value: Any) -> None:
    """Assign *value* to *attrs* using FalkorDB-safe property shapes."""
    if value is None:
        return
    if isinstance(value, _FALKORDB_PRIMITIVE_TYPES):
        attrs[key] = value
        return
    if isinstance(value, list):
        if not value:
            return
        if all(isinstance(item, _FALKORDB_PRIMITIVE_TYPES) or item is None for item in value):
            primitive_items = [item for item in value if item is not None]
            if primitive_items:
                attrs[key] = primitive_items
            return
        attrs[f"{key}_json"] = json.dumps(value, default=str)
        return
    if isinstance(value, dict):
        attrs[f"{key}_json"] = json.dumps(value, default=str)
        return
    attrs[key] = str(value)


def falkordb_entity_attributes(snapshot: CrmSnapshot) -> dict[str, Any]:
    """Build FalkorDB-safe entity node properties without losing snapshot data.

    Nested CRM structures are stored as JSON strings. The full canonical snapshot is
    also stored in ``snapshot_json`` for lossless round-trip.
    """
    raw = snapshot.model_dump(mode="json")
    attrs: dict[str, Any] = {
        "snapshot_json": snapshot.model_dump_json(),
        "crm_entity_type": snapshot.metadata.entity_type,
        "crm_id": snapshot.crm_id,
    }

    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        for meta_key, meta_value in metadata.items():
            _falkordb_assign(attrs, f"meta_{meta_key}", meta_value)

    for field_name, field_value in raw.items():
        if field_name in {"metadata", "crm_id"}:
            continue
        if field_name == "updated_at_db" and field_value is not None:
            attrs["updated_at_db"] = str(field_value)
            continue
        _falkordb_assign(attrs, field_name, field_value)

    return attrs
