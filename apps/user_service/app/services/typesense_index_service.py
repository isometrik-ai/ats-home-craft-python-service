"""Typesense indexing helpers for split Contacts/Companies.

This module mirrors the v1 behavior (best-effort, background-safe, low round-trips),
but reads from the split tables:
- contacts / contact_addresses
- companies / company_addresses
- contact_companies
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import Any

import asyncpg

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.schemas import typesense as typesense_schemas
from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.app.search.client_typesense_schema import (
    build_document_from_schema,
)
from apps.user_service.app.search.company_typesense_schema import (
    COMPANIES_COLLECTION_SCHEMA,
)
from apps.user_service.app.search.contact_typesense_schema import (
    CONTACTS_COLLECTION_SCHEMA,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import UserContext, parse_json_field
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger
from libs.shared_utils.typesense_service import TypesenseService

logger = get_logger("typesense_index_service")


def _dedupe_string_list_fields(document: dict[str, Any]) -> None:
    """Deduplicate `string[]`-like fields while preserving order."""
    for key, value in document.items():
        if not isinstance(value, list):
            continue

        non_null_items = [i for i in value if i is not None]
        if any(isinstance(i, (dict, list)) for i in non_null_items):
            continue
        seen: set[str] = set()
        deduped: list[str] = []
        for item in value:
            if item is None:
                continue
            item_str = item if isinstance(item, str) else str(item)
            if item_str in seen:
                continue
            seen.add(item_str)
            deduped.append(item_str)
        document[key] = deduped


def _normalize_phone_entry(value: Any) -> dict[str, Any] | None:
    """Normalize phone items that may arrive as dict or stringified dict."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    # Some DB drivers/queries may return JSONB array items as Python-dict repr strings.
    # Example: "{'id': '...', 'phone_number': '...'}" (single quotes) which is not JSON.
    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_contact_full_name(details: dict[str, Any]) -> str:
    """Build a full name from contact details."""
    first_name = details.get("first_name") or ""
    last_name = details.get("last_name") or ""
    full_name_parts = (
        details.get("prefix"),
        first_name,
        details.get("middle_name"),
        last_name,
    )
    return " ".join(part for part in full_name_parts if part)


def _extract_phone_numbers_and_display(
    details: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract phone numbers and display from contact details."""
    phones = parse_json_field(details.get("phones")) or []
    phone_numbers: list[str] = []
    phones_display: list[dict[str, Any]] = []
    if not isinstance(phones, list):
        return phone_numbers, phones_display

    for phone_entry in phones:
        normalized = _normalize_phone_entry(phone_entry)
        if normalized is None:
            continue
        phones_display.append(normalized)
        number = (normalized.get("phone_number") or "").strip()
        isd_code = (normalized.get("phone_isd_code") or "").strip()
        if not number:
            continue
        phone_numbers.append(f"{isd_code}{number}" if isd_code else number)
    return phone_numbers, phones_display


def _extract_company_phone_numbers_and_display(
    details: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract phone numbers and display from company details."""
    phones = parse_json_field(details.get("phones")) or []
    phone_numbers: list[str] = []
    phones_display: list[dict[str, Any]] = []
    if not isinstance(phones, list):
        return phone_numbers, phones_display

    for phone_entry in phones:
        normalized = _normalize_phone_entry(phone_entry)
        if normalized is None:
            continue
        phones_display.append(normalized)
        number = (normalized.get("phone_number") or "").strip()
        isd_code = (normalized.get("phone_isd_code") or "").strip()
        if not number:
            continue
        phone_numbers.append(f"{isd_code}{number}" if isd_code else number)
    return phone_numbers, phones_display


def _extract_contact_company_linkage(
    details: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract company linkage from contact details."""
    companies = details.get("companies") or []
    company_ids: list[str] = []
    company_names: list[str] = []
    if not isinstance(companies, list) or not companies:
        return company_ids, company_names

    for company_entry in companies:
        if not isinstance(company_entry, dict):
            continue
        linked_company_id = (company_entry.get("company_id") or "").strip()
        linked_company_name = (company_entry.get("name") or "").strip()
        if linked_company_id:
            company_ids.append(linked_company_id)
        if linked_company_name:
            company_names.append(linked_company_name)
    return company_ids, company_names


async def _extract_contact_custom_field_facets(
    *,
    conn: asyncpg.Connection,
    organization_id: str,
    details: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract custom field facets from contact details."""
    custom_field_keys: list[str] = []
    custom_field_values: list[str] = []
    root_cells = details.get("custom_fields") or []
    if not isinstance(root_cells, list) or not root_cells:
        return custom_field_keys, custom_field_values

    # Use a minimal user context so custom-field reads are org-scoped.
    user_context = UserContext(user_id="", email="", organization_id=organization_id)
    custom_field_service = CustomFieldService(db_connection=conn, user_context=user_context)
    definitions, _ = await custom_field_service.get_custom_fields_list(
        EntityType.CONTACT,
        organization_id=organization_id,
    )
    id_to_def = {str(d.id): d for d in definitions}
    return CustomFieldService.field_cells_typesense_facets(
        root_cells,
        id_to_def,
    )


def _extract_created_updated(details: dict[str, Any]) -> tuple[int, int]:
    """Extract created and updated timestamps from contact details."""
    created_at_dt = details.get("created_at_dt") or details.get("created_at")
    updated_at_dt = details.get("updated_at_dt") or details.get("updated_at")
    created_at = int(created_at_dt.timestamp()) if hasattr(created_at_dt, "timestamp") else 0
    updated_at = int(updated_at_dt.timestamp()) if hasattr(updated_at_dt, "timestamp") else 0
    return created_at, updated_at


def _ensure_list(value: Any) -> list[Any]:
    """Ensure a value is a list, parsing JSON-like strings when needed."""
    parsed = parse_json_field(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


def _ensure_dict(value: Any) -> dict[str, Any]:
    """Ensure a value is a dict, parsing JSON-like strings when needed."""
    parsed = parse_json_field(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _extract_contact_skills(details: dict[str, Any]) -> list[str]:
    """Extract skills from contact details."""
    skills_raw = _ensure_list(details.get("skills"))
    skills: list[str] = []
    for item in skills_raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value:
            skills.append(value)
    return skills


def _extract_contact_work_history_facets(
    details: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract work history from contact details."""
    work_history_raw = _ensure_list(details.get("work_history"))
    companies: list[str] = []
    titles: list[str] = []
    for item in work_history_raw:
        if not isinstance(item, dict):
            continue
        company = (item.get("company_name") or item.get("company") or "").strip()
        title = (item.get("title") or item.get("job_title") or item.get("position") or "").strip()
        if company:
            companies.append(company)
        if title:
            titles.append(title)
    return companies, titles


def _extract_contact_education_facets(
    details: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract education from contact details."""
    education_raw = _ensure_list(details.get("educational_history"))
    institutions: list[str] = []
    degrees: list[str] = []
    for item in education_raw:
        if not isinstance(item, dict):
            continue
        institution = (
            item.get("institution")
            or item.get("school")
            or item.get("university")
            or item.get("college")
            or ""
        )
        degree = item.get("degree") or item.get("qualification") or item.get("field_of_study") or ""
        institution_s = institution.strip() if isinstance(institution, str) else ""
        degree_s = degree.strip() if isinstance(degree, str) else ""
        if institution_s:
            institutions.append(institution_s)
        if degree_s:
            degrees.append(degree_s)
    return institutions, degrees


def _extract_contact_social_urls(details: dict[str, Any]) -> list[str]:
    """Extract social URLs from contact details."""
    social_raw = _ensure_list(details.get("social_pages"))
    urls: list[str] = []
    for item in social_raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link") or item.get("profile_url") or ""
        url_s = url.strip() if isinstance(url, str) else ""
        if url_s:
            urls.append(url_s)
    return urls


def _extract_contact_websites(details: dict[str, Any]) -> list[str]:
    """Extract websites from contact details."""
    additional = _ensure_dict(details.get("additional_data"))
    websites_raw = additional.get("websites")
    websites_list = _ensure_list(websites_raw)
    urls: list[str] = []
    for item in websites_list:
        if isinstance(item, str):
            url_s = item.strip()
        elif isinstance(item, dict):
            url_value = item.get("url") or item.get("website") or item.get("link") or ""
            url_s = url_value.strip() if isinstance(url_value, str) else ""
        else:
            continue
        if not url_s:
            continue
        urls.append(url_s)
    return urls


def _extract_contact_address_facets(
    details: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Extract address facets from contact details."""
    addresses_raw = _ensure_list(details.get("addresses"))
    cities: list[str] = []
    states: list[str] = []
    countries: list[str] = []
    postal_codes: list[str] = []
    for item in addresses_raw:
        if not isinstance(item, dict):
            continue
        city = (item.get("city") or "").strip()
        state = (item.get("state") or item.get("region") or "").strip()
        country = (item.get("country") or "").strip()
        postal = (item.get("postal_code") or item.get("zip_code") or item.get("zip") or "").strip()
        if city:
            cities.append(city)
        if state:
            states.append(state)
        if country:
            countries.append(country)
        if postal:
            postal_codes.append(postal)
    return cities, states, countries, postal_codes


def _extract_company_address_facets(
    details: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Extract address facets from company details (from addresses[])."""
    addresses_raw = _ensure_list(details.get("addresses"))
    cities: list[str] = []
    states: list[str] = []
    countries: list[str] = []
    postal_codes: list[str] = []
    for item in addresses_raw:
        if not isinstance(item, dict):
            continue
        city = (item.get("city") or "").strip()
        state = (item.get("state") or item.get("region") or "").strip()
        country = (item.get("country") or "").strip()
        postal = (item.get("postal_code") or item.get("zip_code") or item.get("zip") or "").strip()
        if city:
            cities.append(city)
        if state:
            states.append(state)
        if country:
            countries.append(country)
        if postal:
            postal_codes.append(postal)
    return cities, states, countries, postal_codes


def _extract_company_product_names(details: dict[str, Any]) -> list[str]:
    """Extract product names from company JSONB products[] list."""
    products_raw = _ensure_list(details.get("products"))
    names: list[str] = []
    for item in products_raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _extract_company_key_people_names(details: dict[str, Any]) -> list[str]:
    """Extract key people names from company JSONB key_people[] list."""
    people_raw = _ensure_list(details.get("key_people"))
    names: list[str] = []
    for item in people_raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _extract_company_contacts_fields(
    details: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str], list[str]]:
    """Extract company contacts fields from company details."""
    contacts_raw = details.get("contacts") or []
    contacts: list[dict[str, Any]] = []
    contact_full_names: list[str] = []
    contact_titles: list[str] = []
    contact_emails: list[str] = []
    contact_phone_numbers: list[str] = []

    if not isinstance(contacts_raw, list) or not contacts_raw:
        return (
            contacts,
            contact_full_names,
            contact_titles,
            contact_emails,
            contact_phone_numbers,
        )

    for contact in contacts_raw:
        if not isinstance(contact, dict):
            continue
        first_name = (contact.get("first_name") or "").strip()
        last_name = (contact.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        title = (contact.get("title") or "").strip()
        email = (contact.get("email") or "").strip().lower()
        is_primary = bool(contact.get("is_primary"))

        phone_numbers, phones_display = _extract_phone_numbers_and_display(contact)

        contact_doc = {
            "id": (contact.get("id") or "").strip(),
            "first_name": first_name or None,
            "last_name": last_name or None,
            "full_name": full_name or "",
            "title": title or None,
            "email": email or None,
            "is_primary": is_primary,
            "phones_display": phones_display or None,
            "phone_numbers": phone_numbers or None,
        }
        contacts.append({k: v for k, v in contact_doc.items() if v is not None and v != ""})

        if full_name:
            contact_full_names.append(full_name)
        if title:
            contact_titles.append(title)
        if email:
            contact_emails.append(email)
        if phone_numbers:
            contact_phone_numbers.extend(phone_numbers)

    return (
        contacts,
        contact_full_names,
        contact_titles,
        contact_emails,
        contact_phone_numbers,
    )


async def _extract_company_custom_field_facets(
    *,
    conn: asyncpg.Connection,
    organization_id: str,
    details: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract custom field facets from company details."""
    custom_field_keys: list[str] = []
    custom_field_values: list[str] = []
    root_cells = details.get("custom_fields") or []
    if not isinstance(root_cells, list) or not root_cells:
        return custom_field_keys, custom_field_values

    # Use a minimal user context so custom-field reads are org-scoped.
    user_context = UserContext(user_id="", email="", organization_id=organization_id)
    custom_field_service = CustomFieldService(db_connection=conn, user_context=user_context)
    definitions, _ = await custom_field_service.get_custom_fields_list(
        EntityType.COMPANY,
        organization_id=organization_id,
    )
    id_to_def = {str(d.id): d for d in definitions}
    return CustomFieldService.field_cells_typesense_facets(
        root_cells,
        id_to_def,
    )


async def _build_contact_document(
    *,
    conn: asyncpg.Connection,
    contact_id: str,
    organization_id: str,
) -> dict[str, Any] | None:
    """Build a Typesense document for a contact."""
    contacts_repo = ContactsRepository(conn)
    details = await contacts_repo.get_contact_details(
        contact_id=contact_id,
        organization_id=organization_id,
    )
    if not details:
        return None

    full_name = _build_contact_full_name(details)
    phone_numbers, phones_display = _extract_phone_numbers_and_display(details)
    company_ids, company_names = _extract_contact_company_linkage(details)
    custom_field_keys, custom_field_values = await _extract_contact_custom_field_facets(
        conn=conn,
        organization_id=organization_id,
        details=details,
    )
    skills = _extract_contact_skills(details)
    work_history_companies, work_history_titles = _extract_contact_work_history_facets(details)
    educational_institutions, educational_degrees = _extract_contact_education_facets(details)
    social_urls = _extract_contact_social_urls(details)
    websites = _extract_contact_websites(details)
    address_cities, address_states, address_countries, address_postal_codes = (
        _extract_contact_address_facets(details)
    )
    created_at, updated_at = _extract_created_updated(details)

    document: dict[str, Any] = {
        "id": str(details["id"]),
        "organization_id": str(details["organization_id"]),
        "status": details.get("status"),
        "first_name": (details.get("first_name") or "") or None,
        "last_name": (details.get("last_name") or "") or None,
        "full_name": full_name
        or " ".join(
            part
            for part in ((details.get("first_name") or ""), (details.get("last_name") or ""))
            if part
        )
        or "",
        "title": details.get("title") or None,
        "email": (details.get("email") or "").lower() or None,
        "phone_numbers": phone_numbers or None,
        "phones_display": phones_display or None,
        "tags": details.get("tags") or [],
        "skills": skills or None,
        "work_history_companies": work_history_companies or None,
        "work_history_titles": work_history_titles or None,
        "educational_institutions": educational_institutions or None,
        "educational_degrees": educational_degrees or None,
        "social_urls": social_urls or None,
        "websites": websites or None,
        "address_cities": address_cities or None,
        "address_states": address_states or None,
        "address_countries": address_countries or None,
        "address_postal_codes": address_postal_codes or None,
        "company_ids": company_ids or None,
        "company_names": company_names or None,
        "custom_field_keys": custom_field_keys or None,
        "custom_field_values": custom_field_values or None,
        "enrichment_done": bool(details.get("enrichment_done")),
        "created_at": created_at,
        "updated_at": updated_at,
        "profile_photo_url": details.get("profile_photo_url") or "",
    }
    _dedupe_string_list_fields(document)
    validated_document = typesense_schemas.TypesenseContactDocument.model_validate(
        document
    ).model_dump(exclude_none=True)
    return build_document_from_schema(
        schema=CONTACTS_COLLECTION_SCHEMA,
        raw_document=validated_document,
    )


async def _build_company_document(
    *,
    conn: asyncpg.Connection,
    company_id: str,
    organization_id: str,
) -> dict[str, Any] | None:
    """Build a Typesense document for a company."""
    companies_repo = CompaniesRepository(conn)
    details = await companies_repo.get_company_details(
        company_id=company_id,
        organization_id=organization_id,
    )
    if not details:
        return None

    (
        contacts,
        contact_full_names,
        contact_titles,
        contact_emails,
        contact_phone_numbers,
    ) = _extract_company_contacts_fields(details)

    phone_numbers, phones_display = _extract_company_phone_numbers_and_display(details)

    custom_field_keys, custom_field_values = await _extract_company_custom_field_facets(
        conn=conn,
        organization_id=organization_id,
        details=details,
    )

    address_cities, address_states, address_countries, address_postal_codes = (
        _extract_company_address_facets(details)
    )
    key_people_names = _extract_company_key_people_names(details)
    product_names = _extract_company_product_names(details)

    created_at, updated_at = _extract_created_updated(details)

    document: dict[str, Any] = {
        "id": str(details["id"]),
        "organization_id": str(details["organization_id"]),
        "status": details.get("status"),
        "name": details.get("name") or "",
        "industry": details.get("industry") or None,
        "email": (details.get("email") or "").strip().lower() or None,
        "phone_numbers": phone_numbers or None,
        "phones_display": phones_display or None,
        "contacts": contacts or None,
        "contact_full_names": contact_full_names or None,
        "contact_titles": contact_titles or None,
        "contact_emails": contact_emails or None,
        "contact_phone_numbers": contact_phone_numbers or None,
        "tags": details.get("tags") or [],
        "description": details.get("description") or "",
        "target_market_segments": details.get("target_market_segments") or [],
        "current_tech_stack": details.get("current_tech_stack") or [],
        "preferred_communication_channels": details.get("preferred_communication_channels") or [],
        "industry_specific_terminologies": details.get("industry_specific_terminologies") or [],
        "address_cities": address_cities or None,
        "address_states": address_states or None,
        "address_countries": address_countries or None,
        "address_postal_codes": address_postal_codes or None,
        "key_people_names": key_people_names or None,
        "product_names": product_names or None,
        "custom_field_keys": custom_field_keys or None,
        "custom_field_values": custom_field_values or None,
        "enrichment_done": bool(details.get("enrichment_done")),
        "created_at": created_at,
        "updated_at": updated_at,
        "profile_photo_url": details.get("profile_photo_url") or "",
    }
    _dedupe_string_list_fields(document)
    validated_document = typesense_schemas.TypesenseCompanyDocument.model_validate(
        document
    ).model_dump(exclude_none=True)
    return build_document_from_schema(
        schema=COMPANIES_COLLECTION_SCHEMA,
        raw_document=validated_document,
    )


async def index_contacts_background(client_refs: Iterable[tuple[str, str]]) -> None:
    """Best-effort indexing of contact documents into the contacts collection."""
    client_ref_list = list(client_refs)
    if not client_ref_list:
        return
    pool = await get_pool()
    async with AcquireConnection(pool) as conn:
        typesense = TypesenseService.from_settings(
            collection_name=app_settings.shared_settings.typesense.contacts_collection_name,
        )
        documents: list[dict[str, Any]] = []
        for client_id, org_id in client_ref_list:
            try:
                document = await _build_contact_document(
                    conn=conn,
                    contact_id=client_id,
                    organization_id=org_id,
                )
                if document:
                    documents.append(document)
            except Exception:
                logger.exception(
                    "typesense_v2_build_contact_doc_failed",
                    extra={"contact_id": client_id},
                )
        if documents:
            await typesense.upsert_documents_bulk(documents)


async def index_companies_background(client_refs: Iterable[tuple[str, str]]) -> None:
    """Best-effort indexing of company documents into the companies collection."""
    client_ref_list = list(client_refs)
    if not client_ref_list:
        return
    pool = await get_pool()
    async with AcquireConnection(pool) as conn:
        typesense = TypesenseService.from_settings(
            collection_name=app_settings.shared_settings.typesense.companies_collection_name,
        )
        documents: list[dict[str, Any]] = []
        for client_id, org_id in client_ref_list:
            try:
                document = await _build_company_document(
                    conn=conn,
                    company_id=client_id,
                    organization_id=org_id,
                )
                if document:
                    documents.append(document)
            except Exception:
                logger.exception(
                    "typesense_v2_build_company_doc_failed",
                    extra={"company_id": client_id},
                )
        if documents:
            await typesense.upsert_documents_bulk(documents)


async def delete_contact_background(contact_id: str) -> None:
    """Best-effort deletion of a contact document from the contacts collection."""
    typesense = TypesenseService.from_settings(
        collection_name=app_settings.shared_settings.typesense.contacts_collection_name,
    )
    try:
        await typesense.delete_document(str(contact_id))
    except Exception:
        logger.exception(
            "typesense_v2_delete_contact_failed",
            extra={"contact_id": contact_id},
        )


async def delete_company_background(company_id: str) -> None:
    """Best-effort deletion of a company document from the companies collection."""
    typesense = TypesenseService.from_settings(
        collection_name=app_settings.shared_settings.typesense.companies_collection_name,
    )
    try:
        await typesense.delete_document(str(company_id))
    except Exception:
        logger.exception(
            "typesense_v2_delete_company_failed",
            extra={"company_id": company_id},
        )
