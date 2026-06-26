"""Build Graphiti CRM snapshots from Postgres using existing Supermemory formatters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.schemas.enums import EntityType as CustomFieldEntityType
from apps.user_service.app.services.supermemory_sync_service import (
    _SCHEMA_VERSION,
    _TAGS_METADATA_MAX_LEN,
    _extract_phone_numbers_and_display,
    _format_scalar_date,
    _normalize_tags,
    _parse_json_dict,
    _parse_json_list,
    _prepare_company_row,
    _prepare_contact_row,
    _resolve_entity_custom_fields_for_snapshot,
    _tags_csv,
    _unix_ts,
)
from apps.user_service.app.services.typesense_index_service import (
    _build_contact_full_name,
    _extract_company_phone_numbers_and_display,
    _extract_contact_company_linkage,
    _extract_contact_websites,
)
from apps.user_service.app.utils.common_utils import parse_json_field
from libs.shared_utils.graphiti_crm_models import (
    AddressEntry,
    CompanySnapshot,
    ContactSnapshot,
    CrmEntityType,
    CrmMetadata,
    EducationEntry,
    LeadSnapshot,
    LinkedCompanyRef,
    LinkedContactRef,
    LinkedLeadRef,
    NoteEntry,
    PhoneEntry,
    ResolvedCustomField,
    SocialPageEntry,
    WebsiteEntry,
    WorkHistoryEntry,
)


def _base_metadata(
    *,
    entity_type: CrmEntityType,
    entity_id: str,
    organization_id: str,
    status: str,
    display_name: str,
    primary_email: str,
    updated_at: int,
    related_company_ids: str = "",
    related_contact_ids: str = "",
    tags: str = "",
) -> CrmMetadata:
    """Build shared CRM metadata for snapshot episodes."""
    meta: dict[str, Any] = {
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
    return CrmMetadata(**meta)


def _phones_from_display(phones_display: list[dict[str, Any]]) -> list[PhoneEntry]:
    """Convert display phone dicts into snapshot phone entries."""
    return [
        PhoneEntry(
            phone_number=str(p.get("phone_number") or "") or None,
            phone_isd_code=str(p.get("phone_isd_code") or "") or None,
            label=str(p.get("label") or "") or None,
            is_primary=bool(p.get("is_primary")) if p.get("is_primary") is not None else None,
        )
        for p in phones_display
        if isinstance(p, dict)
    ]


def _notes_from_raw(notes: Any) -> list[NoteEntry]:
    """Parse raw CRM notes JSON into snapshot note entries."""
    items: list[NoteEntry] = []
    for item in _parse_json_list(notes):
        if not isinstance(item, dict):
            continue
        items.append(
            NoteEntry(
                title=str(item.get("title") or "").strip() or None,
                content=str(item.get("content") or "").strip() or None,
            )
        )
    return items


def _custom_fields_from_resolved(resolved: list[dict[str, Any]]) -> list[ResolvedCustomField]:
    """Convert resolved custom-field dicts into snapshot entries."""
    return [ResolvedCustomField.model_validate(node) for node in resolved if isinstance(node, dict)]


def _tombstone_snapshot(
    *,
    entity_type: CrmEntityType,
    entity_id: str,
    organization_id: str,
    display_name: str,
) -> ContactSnapshot | CompanySnapshot | LeadSnapshot:
    """Build a deleted-entity placeholder snapshot."""
    metadata = _base_metadata(
        entity_type=entity_type,
        entity_id=entity_id,
        organization_id=organization_id,
        status="deleted",
        display_name=display_name,
        primary_email="",
        updated_at=int(datetime.now(UTC).timestamp()),
    )
    if entity_type == "contact":
        return ContactSnapshot(crm_id=entity_id, display_name=display_name, metadata=metadata)
    if entity_type == "company":
        return CompanySnapshot(
            crm_id=entity_id, name=display_name, display_name=display_name, metadata=metadata
        )
    return LeadSnapshot(
        crm_id=entity_id, name=display_name, display_name=display_name, metadata=metadata
    )


def _linked_companies_from_prepared(prepared: dict[str, Any]) -> list[LinkedCompanyRef]:
    """Parse linked company refs from prepared contact row data."""
    linked_companies: list[LinkedCompanyRef] = []
    for entry in _parse_json_list(prepared.get("companies")):
        if isinstance(entry, dict):
            linked_companies.append(
                LinkedCompanyRef(
                    name=str(entry.get("name") or "") or None,
                    industry=str(entry.get("industry") or "") or None,
                    is_primary=bool(entry.get("is_primary"))
                    if entry.get("is_primary") is not None
                    else None,
                    company_id=str(entry.get("id") or entry.get("company_id") or "") or None,
                )
            )
    return linked_companies


def _linked_leads_from_prepared(prepared: dict[str, Any]) -> list[LinkedLeadRef]:
    """Parse linked lead refs from prepared contact row data."""
    linked_leads: list[LinkedLeadRef] = []
    for entry in _parse_json_list(prepared.get("leads")):
        if isinstance(entry, dict):
            linked_leads.append(
                LinkedLeadRef(
                    lead_id=str(entry.get("id") or "").strip() or None,
                    name=str(entry.get("name") or "").strip() or None,
                    stage_name=str(entry.get("stage_name") or "").strip() or None,
                    stage_id=str(entry.get("stage_id") or "").strip() or None,
                    amount=entry.get("amount"),
                )
            )
    return linked_leads


def _addresses_from_prepared(prepared: dict[str, Any]) -> list[AddressEntry]:
    """Parse address entries from prepared contact row data."""
    return [
        AddressEntry(
            address_line1=str(a.get("address_line1") or "") or None,
            address_line2=str(a.get("address_line2") or "") or None,
            city=str(a.get("city") or "") or None,
            state=str(a.get("state") or "") or None,
            country=str(a.get("country") or "") or None,
            postal_code=str(a.get("postal_code") or "") or None,
            is_primary=bool(a.get("is_primary")) if a.get("is_primary") is not None else None,
        )
        for a in _parse_json_list(prepared.get("addresses"))
        if isinstance(a, dict)
    ]


def _websites_from_additional(additional: dict[str, Any]) -> list[WebsiteEntry]:
    """Parse website entries from contact additional_data."""
    websites: list[WebsiteEntry] = []
    for item in _parse_json_list(additional.get("websites")):
        if isinstance(item, dict):
            websites.append(
                WebsiteEntry(
                    url=str(item.get("url") or "").strip() or None,
                    type=str(item.get("type") or "").strip() or None,
                    is_primary=bool(item.get("is_primary"))
                    if item.get("is_primary") is not None
                    else None,
                )
            )
    return websites


def _social_pages_from_prepared(prepared: dict[str, Any]) -> list[SocialPageEntry]:
    """Parse social page entries from prepared contact row data."""
    social_pages: list[SocialPageEntry] = []
    for item in _parse_json_list(prepared.get("social_pages")):
        if isinstance(item, dict):
            social_pages.append(
                SocialPageEntry(
                    platform=str(item.get("platform") or "").strip() or None,
                    url=str(item.get("url") or item.get("link") or "").strip() or None,
                )
            )
    return social_pages


async def build_contact_snapshot(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    contact_id: str,
) -> ContactSnapshot | None:
    """Load contact from Postgres and return a full snapshot, or tombstone when missing/deleted."""
    repo = ContactsRepository(db_connection=db_connection)
    details = await repo.get_contact_details(contact_id=contact_id, organization_id=organization_id)
    if not details:
        return _tombstone_snapshot(
            entity_type="contact",
            entity_id=contact_id,
            organization_id=organization_id,
            display_name="Deleted contact",
        )

    status = str(details.get("status") or "active")
    full_name = _build_contact_full_name(details) or contact_id
    if status == "deleted":
        return _tombstone_snapshot(
            entity_type="contact",
            entity_id=contact_id,
            organization_id=organization_id,
            display_name=full_name,
        )

    company_ids, _ = _extract_contact_company_linkage(details)  # contact_companies only
    contact_tags = _normalize_tags(details.get("tags"))
    prepared = _prepare_contact_row(details)
    resolved_cf = await _resolve_entity_custom_fields_for_snapshot(
        db_connection,
        organization_id=organization_id,
        entity_type=CustomFieldEntityType.CONTACT,
        stored_custom_fields=prepared.get("custom_fields"),
    )

    additional = _parse_json_dict(prepared.get("additional_data"))
    _, phones_display = _extract_phone_numbers_and_display(prepared)

    linked_companies = _linked_companies_from_prepared(prepared)
    linked_leads = _linked_leads_from_prepared(prepared)
    addresses = _addresses_from_prepared(prepared)
    websites = _websites_from_additional(additional)
    social_pages = _social_pages_from_prepared(prepared)

    work_history = [
        WorkHistoryEntry.model_validate(item)
        for item in _parse_json_list(prepared.get("work_history"))
        if isinstance(item, dict)
    ]
    education = [
        EducationEntry.model_validate(item)
        for item in _parse_json_list(prepared.get("educational_history"))
        if isinstance(item, dict)
    ]

    updated_raw = details.get("updated_at")
    updated_at_db = updated_raw if isinstance(updated_raw, datetime) else None

    return ContactSnapshot(
        crm_id=contact_id,
        prefix=str(details.get("prefix") or "").strip() or None,
        first_name=str(details.get("first_name") or "").strip() or None,
        middle_name=str(details.get("middle_name") or "").strip() or None,
        last_name=str(details.get("last_name") or "").strip() or None,
        display_name=full_name,
        email=str(details.get("email") or "").strip() or None,
        title=str(details.get("title") or "").strip() or None,
        date_of_birth=_format_scalar_date(details.get("date_of_birth")) or None,
        status=status,
        enrichment_done=details.get("enrichment_done")
        if isinstance(details.get("enrichment_done"), bool)
        else None,
        intake_stage=str(additional.get("intake_stage") or "").strip() or None,
        preferred_language=str(additional.get("preferred_language") or "").strip() or None,
        tags=contact_tags,
        linked_companies=linked_companies,
        linked_leads=linked_leads,
        addresses=addresses,
        phones=_phones_from_display(phones_display),
        websites=websites,
        website_urls=_extract_contact_websites(prepared),
        social_pages=social_pages,
        notes=_notes_from_raw(prepared.get("notes")),
        skills=[
            s.strip()
            for s in _parse_json_list(prepared.get("skills"))
            if isinstance(s, str) and s.strip()
        ],
        work_history=work_history,
        educational_history=education,
        custom_fields=_custom_fields_from_resolved(resolved_cf),
        metadata=_base_metadata(
            entity_type="contact",
            entity_id=contact_id,
            organization_id=organization_id,
            status=status,
            display_name=full_name,
            primary_email=str(details.get("email") or ""),
            updated_at=_unix_ts(details.get("updated_at")),
            related_company_ids=",".join(company_ids),
            tags=_tags_csv(contact_tags),
        ),
        updated_at_db=updated_at_db,
    )


async def build_company_snapshot(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    company_id: str,
) -> CompanySnapshot | None:
    """Load company from Postgres and return a full snapshot."""
    repo = CompaniesRepository(db_connection=db_connection)
    details = await repo.get_company_details(company_id=company_id, organization_id=organization_id)
    if not details:
        return _tombstone_snapshot(
            entity_type="company",
            entity_id=company_id,
            organization_id=organization_id,
            display_name="Deleted company",
        )

    status = str(details.get("status") or "active")
    name = str(details.get("name") or company_id)
    if status == "deleted":
        return _tombstone_snapshot(
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
    resolved_cf = await _resolve_entity_custom_fields_for_snapshot(
        db_connection,
        organization_id=organization_id,
        entity_type=CustomFieldEntityType.COMPANY,
        stored_custom_fields=prepared.get("custom_fields"),
    )
    _, phones_display = _extract_company_phone_numbers_and_display(prepared)

    linked_contacts: list[LinkedContactRef] = []
    for contact in _parse_json_list(prepared.get("contacts")):
        if not isinstance(contact, dict):
            continue
        full = contact.get("full_name") or " ".join(
            p for p in (contact.get("first_name"), contact.get("last_name")) if p
        )
        linked_contacts.append(
            LinkedContactRef(
                id=str(contact.get("id") or "") or None,
                full_name=str(full).strip() or None,
                first_name=str(contact.get("first_name") or "").strip() or None,
                last_name=str(contact.get("last_name") or "").strip() or None,
                title=str(contact.get("title") or "").strip() or None,
                email=str(contact.get("email") or "").strip() or None,
                is_primary=bool(contact.get("is_primary"))
                if contact.get("is_primary") is not None
                else None,
            )
        )

    updated_raw = details.get("updated_at")
    updated_at_db = updated_raw if isinstance(updated_raw, datetime) else None

    return CompanySnapshot(
        crm_id=company_id,
        name=name,
        display_name=name,
        industry=str(details.get("industry") or "").strip() or None,
        email=str(details.get("email") or "").strip() or None,
        status=status,
        description=str(details.get("description") or "").strip() or None,
        tags=company_tags,
        linked_contacts=linked_contacts,
        addresses=[
            AddressEntry(
                address_line1=str(a.get("address_line1") or "") or None,
                address_line2=str(a.get("address_line2") or "") or None,
                city=str(a.get("city") or "") or None,
                state=str(a.get("state") or "") or None,
                country=str(a.get("country") or "") or None,
                postal_code=str(a.get("postal_code") or "") or None,
                is_primary=bool(a.get("is_primary")) if a.get("is_primary") is not None else None,
            )
            for a in _parse_json_list(prepared.get("addresses"))
            if isinstance(a, dict)
        ],
        phones=_phones_from_display(phones_display),
        notes=_notes_from_raw(prepared.get("notes")),
        custom_fields=_custom_fields_from_resolved(resolved_cf),
        metadata=_base_metadata(
            entity_type="company",
            entity_id=company_id,
            organization_id=organization_id,
            status=status,
            display_name=name,
            primary_email=str(details.get("email") or ""),
            updated_at=_unix_ts(details.get("updated_at")),
            related_contact_ids=related_contact_ids,
            tags=_tags_csv(company_tags),
        ),
        updated_at_db=updated_at_db,
    )


async def build_lead_snapshot(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    lead_id: str,
) -> LeadSnapshot | None:
    """Load lead from Postgres and return a full snapshot."""
    lead_repo = LeadRepository(db_connection=db_connection)
    row = await lead_repo.get_lead_detail_with_contacts_by_id(
        organization_id, lead_id, owner_id=None
    )
    if not row:
        return _tombstone_snapshot(
            entity_type="lead",
            entity_id=lead_id,
            organization_id=organization_id,
            display_name="Deleted lead",
        )

    contacts_raw = row.get("contacts") or []
    contacts = contacts_raw if isinstance(contacts_raw, list) else []
    name = str(row.get("name") or lead_id)

    companies = parse_json_field(row.get("companies")) or []
    linked_companies: list[LinkedCompanyRef] = []
    if isinstance(companies, list):
        for company in companies:
            if isinstance(company, dict):
                cname = (company.get("name") or "").strip() or (
                    str(company.get("company_id")).strip() if company.get("company_id") else ""
                )
                linked_companies.append(
                    LinkedCompanyRef(
                        name=cname or None,
                        company_id=str(company.get("company_id") or company.get("id") or "")
                        or None,
                    )
                )

    linked_contacts: list[LinkedContactRef] = []
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        linked_contacts.append(
            LinkedContactRef(
                contact_id=str(contact.get("contact_id") or "") or None,
                contact_name=str(contact.get("contact_name") or contact.get("contact_id") or "")
                or None,
                contact_email=str(contact.get("contact_email") or contact.get("email") or "")
                or None,
                email=str(contact.get("email") or "").strip() or None,
                label=str(contact.get("label") or "").strip() or None,
            )
        )

    updated_raw = row.get("updated_at")
    updated_at_db = updated_raw if isinstance(updated_raw, datetime) else None

    return LeadSnapshot(
        crm_id=lead_id,
        name=name,
        display_name=name,
        stage_name=str(row.get("stage_name") or "").strip() or None,
        stage_id=str(row.get("stage_id") or "").strip() or None,
        priority=str(row.get("priority") or "").strip() or None,
        amount=row.get("amount"),
        currency=str(row.get("currency") or "").strip() or None,
        owner_name=str(row.get("owner_name") or "").strip() or None,
        owner_id=str(row.get("owner_id") or "").strip() or None,
        close_date=row.get("close_date"),
        lead_score=row.get("lead_score"),
        linked_companies=linked_companies,
        linked_contacts=linked_contacts,
        notes=_notes_from_raw(row.get("notes")),
        description=str(row.get("description") or "").strip() or None,
        metadata=_base_metadata(
            entity_type="lead",
            entity_id=lead_id,
            organization_id=organization_id,
            status="active",
            display_name=name,
            primary_email="",
            updated_at=_unix_ts(row.get("updated_at")),
        ),
        updated_at_db=updated_at_db,
    )
