"""Import vendor companies from the Vendors workbook.

Companies are created or updated through CompaniesService. Contacts are linked
when they already exist and created through ContactsService when they do not.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    BillingPreferences,
    BillingPreferencesUpdate,
    NoteItem,
    Phone,
    Website,
    WebsiteInput,
    WebsitesUpdate,
)
from apps.user_service.app.schemas.companies import (
    CompanyContactAssociationAdd,
    CompanyContactUpdate,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.contacts import (
    ContactCompaniesCreate,
    ContactCompanyAssociationAdd,
    CreateContactRequest,
)
from apps.user_service.app.schemas.enums import ContactType
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import coerce_json_list, parse_json_field
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("vendor_import")
_MAX_VENDOR_WORKBOOK_BYTES = 20 * 1024 * 1024

_VENDOR_HEADERS = {
    "company name": "company_name",
    "service / industry": "service",
    "service/industry": "service",
    "company email": "company_email",
    "company phone": "company_phone",
    "website": "website",
    "contact person": "contact_person",
    "designation": "designation",
    "contact mobile": "contact_mobile",
    "contact email": "contact_email",
    "address": "address",
    "city": "city",
    "state": "state",
    "pin code": "postal_code",
    "pincode": "postal_code",
    "pin": "postal_code",
    "billing method": "billing_method",
    "payment terms": "payment_terms",
    "notes": "notes",
}
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_NAME_PREFIXES = {"mr": "Mr", "mrs": "Mrs", "ms": "Ms", "miss": "Miss", "dr": "Dr", "prof": "Prof"}
_MAX_WORKBOOK_SHEETS = 20
_MAX_VENDOR_ROWS = 5000
_MAX_VENDOR_COLUMNS = 256
_MAX_HEADER_ROWS = 30
_MAX_VENDOR_TAGS = 50


async def import_vendors(
    service: CompaniesService,
    *,
    content: bytes,
    filename: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Create or update vendor companies from an uploaded Vendors workbook.

    Service / Industry values are stored as tags. A contact is linked when the
    email or mobile already exists, and created through the contact service when
    it does not.
    """
    workbook_too_large = len(content) > _MAX_VENDOR_WORKBOOK_BYTES
    if not filename.lower().endswith(".xlsx") or not content or workbook_too_large:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    vendors = _group_vendor_sheet(content)
    if not vendors:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    created = 0
    updated = 0
    errors: list[dict[str, Any]] = []
    company_ids: list[str] = []
    contact_ids: list[str] = []
    organization_id = service.user_context.organization_id
    assert organization_id
    for vendor in vendors:
        try:
            company_id, was_created, linked_ids, has_primary = await _save_vendor_company(
                service,
                vendor=vendor,
                organization_id=organization_id,
                project_id=project_id,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Vendor import failed for %s", vendor["name"])
            message_key = getattr(exc, "message_key", None)
            message = (
                message_key
                if isinstance(message_key, str) and message_key
                else (str(exc).strip() or exc.__class__.__name__)
            )
            errors.extend(
                {"row": row_number, "company": vendor["name"], "message": message[:500]}
                for row_number in vendor["rows"]
            )
            continue
        saved_ids, contact_errors = await _save_vendor_contacts(
            service,
            company_id=company_id,
            vendor=vendor,
            linked_ids=linked_ids,
            has_primary=has_primary,
        )
        errors.extend(contact_errors)
        company_ids.append(company_id)
        contact_ids.extend(saved_ids)
        created += int(was_created)
        updated += int(not was_created)
    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "company_ids": company_ids,
        "contact_ids": contact_ids,
    }


async def _save_vendor_company(
    service: CompaniesService,
    *,
    vendor: dict[str, Any],
    organization_id: str,
    project_id: str | None,
) -> tuple[str, bool, set[str], bool]:
    """Create the company or merge service tags onto the company with the same name."""
    found = await service.companies_repo.get_company_ids_by_names(
        organization_id=organization_id,
        names=[vendor["name"]],
    )
    company_id = found.get(vendor["name"].strip().lower())
    tags = _vendor_tags(vendor["tags"])
    if company_id is None:
        created = await service.create_company(
            _vendor_company_request(vendor, tags),
            project_id=project_id,
        )
        return str(created["company_id"]), True, set(), False

    current = await service.companies_repo.get_company_for_update(
        company_id=company_id,
        organization_id=organization_id,
    )
    current = dict(current or {})
    stored_tags = current.get("tags") if isinstance(current.get("tags"), list) else []
    stored_tags = [str(item).strip() for item in stored_tags if str(item).strip()]
    merged = _vendor_tags([*stored_tags, *vendor["tags"]])
    update_body = _vendor_company_update_request(vendor, current, merged)
    if update_body is not None:
        await service.update_company(company_id=company_id, body=update_body)
    contacts = coerce_json_list(current.get("contacts"))
    linked_ids = {
        str(item.get("id")) for item in contacts if isinstance(item, dict) and item.get("id")
    }
    has_primary = any(bool(item.get("is_primary")) for item in contacts if isinstance(item, dict))
    await service._link_company_to_project_if_requested(
        company_id=company_id,
        project_id=project_id,
    )
    return company_id, False, linked_ids, has_primary


async def _save_vendor_contacts(
    service: CompaniesService,
    *,
    company_id: str,
    vendor: dict[str, Any],
    linked_ids: set[str],
    has_primary: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Link an existing contact or create one with ContactsService."""
    saved: list[str] = []
    errors: list[dict[str, Any]] = []
    contacts_service = ContactsService(
        db_connection=service.db_connection,
        user_context=service.user_context,
        supabase_client=service.supabase_client,
    )
    for contact in vendor["contacts"]:
        row_error = {
            "row": contact["rows"][0] if contact["rows"] else None,
            "company": vendor["name"],
        }
        try:
            existing_id = await _find_vendor_contact_id(service, contact)
            if existing_id is None and not contact["emails"] and not contact["phones"]:
                errors.append(
                    {**row_error, "message": "Contact has no usable email or mobile number."}
                )
                continue
            make_primary = not has_primary
            if existing_id and existing_id not in linked_ids:
                await service.update_company(
                    company_id=company_id,
                    body=UpdateCompanyRequest(
                        contact_association=CompanyContactUpdate(
                            add_associations=[
                                CompanyContactAssociationAdd(
                                    contact_id=existing_id,
                                    is_primary=make_primary,
                                )
                            ]
                        )
                    ),
                )
                linked_ids.add(existing_id)
                has_primary = has_primary or make_primary
            elif existing_id is None:
                created = await contacts_service.create_contact(
                    _vendor_contact_request(
                        contact=contact,
                        company_id=company_id,
                        is_primary=make_primary,
                    )
                )
                existing_id = str(created.get("contact_id") or "")
                if not existing_id:
                    errors.append(
                        {**row_error, "message": "Contact has no usable email or mobile number."}
                    )
                    continue
                linked_ids.add(existing_id)
                has_primary = True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Vendor contact import failed for %s", vendor["name"])
            message_key = getattr(exc, "message_key", None)
            message = (
                message_key
                if isinstance(message_key, str) and message_key
                else (str(exc).strip() or exc.__class__.__name__)
            )
            errors.append({**row_error, "message": message[:500]})
            continue
        saved.append(existing_id)
    return saved, errors


async def _find_vendor_contact_id(
    service: CompaniesService,
    contact: dict[str, Any],
) -> str | None:
    """Resolve a contact by email, then by phone."""
    organization_id = service.user_context.organization_id
    assert organization_id
    for email in contact["emails"]:
        found = await service.contacts_repo.get_contact_id_by_email(
            organization_id=organization_id,
            email=email,
        )
        if found:
            return str(found)
    for isd, number in contact["phones"]:
        for candidate in (number, f"{isd}{number}"):
            details = await service.contacts_repo.get_contact_details_by_phone(
                organization_id=organization_id,
                phone_number=candidate,
            )
            if details and details.get("id"):
                return str(details["id"])
    return None


def _vendor_company_request(vendor: dict[str, Any], tags: list[str]) -> CreateCompanyRequest:
    """Map one grouped vendor onto the existing company create request."""
    phones = [
        Phone(
            phone_number=number,
            phone_isd_code=isd,
            label="work" if index == 0 else "other",
            is_primary=index == 0,
        )
        for index, (isd, number) in enumerate(vendor["phones"][:3])
    ]
    addresses = []
    if any(vendor[key] for key in ("address_line1", "city", "state", "postal_code")):
        addresses.append(
            AddressInput(
                address_line1=vendor["address_line1"],
                city=vendor["city"],
                state=vendor["state"],
                postal_code=vendor["postal_code"],
                country="India",
                is_primary=True,
            )
        )
    return CreateCompanyRequest(
        name=vendor["name"],
        email=vendor["emails"][0] if vendor["emails"] else None,
        phones=phones,
        tags=tags,
        websites=[Website(url=vendor["website"], type="website")] if vendor["website"] else [],
        billing_preferences=BillingPreferences(
            method=vendor["billing_method"],
            terms=vendor["payment_terms"],
        ),
        notes=[NoteItem(title="Vendor import", content=note) for note in vendor["notes"]],
        addresses=addresses,
    )


def _vendor_company_update_request(  # pylint: disable=too-many-branches,too-complex
    vendor: dict[str, Any],
    current: dict[str, Any],
    tags: list[str],
) -> UpdateCompanyRequest | None:
    """Build a patch for imported fields the stored company does not already have."""
    payload: dict[str, Any] = {}
    email = vendor["emails"][0] if vendor["emails"] else None
    current_email = str(current.get("email") or "").strip()
    if email and email.casefold() != current_email.casefold():
        payload["email"] = email
    stored_tags = current.get("tags") if isinstance(current.get("tags"), list) else []
    stored_tags = [str(item).strip() for item in stored_tags if str(item).strip()]
    if tags != stored_tags:
        payload["tags"] = tags

    phones: list[Phone] = []
    seen_numbers: set[str] = set()
    phone_has_primary = False
    for item in coerce_json_list(current.get("phones")):
        if not isinstance(item, dict):
            continue
        number = str(item.get("phone_number") or "").strip()
        isd = str(item.get("phone_isd_code") or "").strip()
        if not number or not isd or number in seen_numbers:
            continue
        seen_numbers.add(number)
        label = str(item.get("label") or "").strip()
        is_primary = bool(item.get("is_primary")) and not phone_has_primary
        phone_has_primary = phone_has_primary or is_primary
        phones.append(
            Phone(
                phone_number=number[:15],
                phone_isd_code=isd[:5],
                label=label[:20] or None,
                is_primary=is_primary,
            )
        )
    added_phone = False
    for isd, number in vendor["phones"]:
        if number in seen_numbers or len(phones) >= 20:
            continue
        seen_numbers.add(number)
        phones.append(
            Phone(
                phone_number=number,
                phone_isd_code=isd,
                label="other" if phones else "work",
                is_primary=not phone_has_primary,
            )
        )
        phone_has_primary = True
        added_phone = True
    if added_phone:
        payload["phones"] = phones

    url = vendor.get("website")
    stored_sites = [
        item for item in coerce_json_list(current.get("websites")) if isinstance(item, dict)
    ]
    known_urls = {str(item.get("url") or "").strip().casefold() for item in stored_sites}
    if url and url.casefold() not in known_urls:
        site_has_primary = any(bool(item.get("is_primary")) for item in stored_sites)
        payload["websites"] = WebsitesUpdate(
            add=[WebsiteInput(url=url, type="website", is_primary=not site_has_primary)]
        )

    address_fields = ("address_line1", "city", "state", "postal_code")
    if any(vendor[key] for key in address_fields):
        incoming = tuple(str(vendor.get(key) or "").strip().casefold() for key in address_fields)
        stored_addresses = [
            item for item in coerce_json_list(current.get("addresses")) if isinstance(item, dict)
        ]
        known_addresses = {
            tuple(str(item.get(key) or "").strip().casefold() for key in address_fields)
            for item in stored_addresses
        }
        if incoming not in known_addresses:
            address_has_primary = any(bool(item.get("is_primary")) for item in stored_addresses)
            payload["addresses"] = AddressesUpdate(
                add=[
                    AddressInput(
                        address_line1=vendor["address_line1"],
                        city=vendor["city"],
                        state=vendor["state"],
                        postal_code=vendor["postal_code"],
                        country="India",
                        is_primary=not address_has_primary,
                    )
                ]
            )

    billing = current.get("billing_preferences")
    if isinstance(billing, str):
        try:
            billing = parse_json_field(billing)
        except (TypeError, ValueError):
            billing = {}
    billing = billing if isinstance(billing, dict) else {}
    billing_payload: dict[str, str] = {}
    method = vendor["billing_method"]
    terms = vendor["payment_terms"]
    if method and str(billing.get("method") or "").strip() != method:
        billing_payload["method"] = method
    if terms and str(billing.get("terms") or "").strip() != terms:
        billing_payload["terms"] = terms
    if billing_payload:
        payload["billing_preferences"] = BillingPreferencesUpdate(**billing_payload)

    if vendor["notes"]:
        notes: list[NoteItem] = []
        seen_notes: set[str] = set()
        for raw in coerce_json_list(current.get("notes")):
            if not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or "").strip()
            title = str(raw.get("title") or "Note").strip() or "Note"
            if not content or content.casefold() in seen_notes:
                continue
            seen_notes.add(content.casefold())
            notes.append(NoteItem(title=title[:500], content=content[:50000]))
        added_note = False
        for content in vendor["notes"]:
            if content.casefold() in seen_notes:
                continue
            seen_notes.add(content.casefold())
            notes.append(NoteItem(title="Vendor import", content=content))
            added_note = True
        if added_note:
            payload["notes"] = notes
    if not payload:
        return None
    return UpdateCompanyRequest(**payload)


def _vendor_contact_request(
    *,
    contact: dict[str, Any],
    company_id: str,
    is_primary: bool,
) -> CreateContactRequest:
    """Map one vendor contact onto the existing contact create request."""
    phones = [
        Phone(
            phone_number=number,
            phone_isd_code=isd,
            label="mobile" if index == 0 else "other",
            is_primary=index == 0,
        )
        for index, (isd, number) in enumerate(contact["phones"][:3])
    ]
    return CreateContactRequest(
        contact_type=ContactType.VENDOR,
        email=contact["emails"][0] if contact["emails"] else None,
        prefix=contact["prefix"],
        first_name=contact["first_name"],
        last_name=contact["last_name"],
        title=contact["title"],
        phones=phones,
        tags=_vendor_tags(contact["tags"]),
        portal_access=False,
        company_association=ContactCompaniesCreate(
            add_association=ContactCompanyAssociationAdd(
                company_id=company_id,
                is_primary=is_primary,
            )
        ),
    )


def _group_vendor_sheet(payload: bytes) -> list[dict[str, Any]]:  # pylint: disable=too-complex
    """Fold the Vendors sheet into one record per company name."""
    try:
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException) as exc:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        ) from exc
    try:
        selected = _vendor_sheet_rows(workbook)
    finally:
        workbook.close()
    if selected is None:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    header, data_rows, first_data_row = selected
    companies: dict[str, dict[str, Any]] = {}
    blank_run = 0
    for offset, row in enumerate(data_rows):
        values = tuple(row or ())
        if not any(_cell_text(cell) for cell in values):
            blank_run += 1
            if blank_run >= 20:
                break
            continue
        blank_run = 0
        mapped = {name: "" for name in set(_VENDOR_HEADERS.values())}
        for index, field_name in header.items():
            if index < len(values):
                mapped[field_name] = _cell_text(values[index])
        name = " ".join(mapped["company_name"].split())[:200]
        if not name:
            continue
        name_key = name.strip().lower()
        vendor = companies.get(name_key)
        if vendor is None:
            vendor = {
                "name": name,
                "emails": [],
                "phones": [],
                "website": None,
                "tags": [],
                "address_line1": None,
                "city": None,
                "state": None,
                "postal_code": None,
                "billing_method": None,
                "payment_terms": None,
                "notes": [],
                "contacts": [],
                "rows": [],
            }
            companies[name_key] = vendor
        _merge_vendor_row(vendor, mapped, first_data_row + offset)
    return list(companies.values())


def _vendor_sheet_rows(workbook: Any) -> tuple[dict[int, str], Any, int] | None:
    """Stream data rows from the Vendors sheet within workbook limits."""
    sheets = list(workbook.worksheets)
    if len(sheets) > _MAX_WORKBOOK_SHEETS:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    for sheet in sheets:
        max_row = getattr(sheet, "max_row", None)
        max_column = getattr(sheet, "max_column", None)
        row_too_large = isinstance(max_row, int) and max_row > _MAX_VENDOR_ROWS
        column_too_large = isinstance(max_column, int) and max_column > _MAX_VENDOR_COLUMNS
        if row_too_large or column_too_large:
            raise ValidationException(
                message_key="companies.errors.import_workbook_invalid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
    preferred = next(
        (sheet for sheet in sheets if str(sheet.title).strip().casefold() == "vendors"),
        None,
    )
    for sheet in [preferred] if preferred is not None else sheets:
        row_iter = sheet.iter_rows(
            max_row=_MAX_VENDOR_ROWS,
            max_col=_MAX_VENDOR_COLUMNS,
            values_only=True,
        )
        for index, row in enumerate(row_iter, start=1):
            if index > _MAX_HEADER_ROWS:
                break
            header: dict[int, str] = {}
            for column, cell in enumerate(row or ()):
                key = re.sub(r"\s+", " ", _cell_text(cell).casefold().replace("*", "")).strip()
                field_name = _VENDOR_HEADERS.get(key)
                if field_name and field_name not in header.values():
                    header[column] = field_name
            fields = set(header.values())
            if "company_name" in fields and "service" in fields:
                return header, row_iter, index + 1
    return None


def _merge_vendor_row(vendor: dict[str, Any], mapped: dict[str, str], row_number: int) -> None:
    """Add one worksheet row onto a grouped vendor."""
    vendor["rows"].append(row_number)
    _extend_text(vendor["emails"], _emails(mapped.get("company_email")))
    _extend_phones(vendor["phones"], _phones(mapped.get("company_phone")))
    service_tag = _service_tag(mapped.get("service"))
    if service_tag:
        _extend_text(vendor["tags"], [service_tag])
    vendor["website"] = vendor["website"] or _website(mapped.get("website"))
    vendor["address_line1"] = vendor["address_line1"] or _clip(mapped.get("address"), 1000)
    vendor["city"] = vendor["city"] or _clip(mapped.get("city"), 100)
    vendor["state"] = vendor["state"] or _clip(mapped.get("state"), 100)
    vendor["postal_code"] = vendor["postal_code"] or _clip(mapped.get("postal_code"), 20)
    vendor["billing_method"] = vendor["billing_method"] or _clip(mapped.get("billing_method"), 50)
    vendor["payment_terms"] = vendor["payment_terms"] or _clip(mapped.get("payment_terms"), 50)
    note = _clip(mapped.get("notes"), 5000)
    if note:
        _extend_text(vendor["notes"], [note])
    person = mapped.get("contact_person") or ""
    emails = _emails(mapped.get("contact_email"))
    phones = _phones(mapped.get("contact_mobile"))
    if not person and not emails and not phones:
        return
    prefix, first_name, last_name = _person_name(person)
    contact = next(
        (
            item
            for item in vendor["contacts"]
            if _same_vendor_contact(item, emails, phones, prefix, first_name, last_name)
        ),
        None,
    )
    if contact is None:
        contact = {
            "prefix": prefix,
            "first_name": first_name,
            "last_name": last_name,
            "title": _clip(mapped.get("designation"), 100),
            "emails": [],
            "phones": [],
            "tags": [],
            "rows": [],
        }
        vendor["contacts"].append(contact)
    if contact["first_name"] is None and first_name:
        contact["prefix"] = prefix
        contact["first_name"] = first_name
        contact["last_name"] = last_name
    if contact["title"] is None:
        contact["title"] = _clip(mapped.get("designation"), 100)
    _extend_text(contact["emails"], emails)
    _extend_phones(contact["phones"], phones)
    if service_tag:
        _extend_text(contact["tags"], [service_tag])
    if row_number not in contact["rows"]:
        contact["rows"].append(row_number)


def _same_vendor_contact(
    contact: dict[str, Any],
    emails: list[str],
    phones: list[tuple[str, str]],
    prefix: str | None,
    first_name: str | None,
    last_name: str | None,
) -> bool:
    """Match a later row to a contact already gathered for the company."""
    known_emails = {email.casefold() for email in contact["emails"]}
    if emails and any(email.casefold() in known_emails for email in emails):
        return True
    known_phones = {number for _, number in contact["phones"]}
    if phones and any(number in known_phones for _, number in phones):
        return True
    if contact["emails"] or contact["phones"]:
        return False
    return (contact["prefix"], contact["first_name"], contact["last_name"]) == (
        prefix,
        first_name,
        last_name,
    )


def _vendor_tags(*groups: list[str]) -> list[str]:
    """Union tag text and keep the Vendor tag."""
    seen: dict[str, str] = {}
    for group in groups:
        for raw in group:
            cleaned = " ".join(str(raw).split())
            if cleaned:
                seen.setdefault(cleaned.casefold(), cleaned[:100])
    if "vendor" not in seen:
        seen["vendor"] = "Vendor"
    values = list(seen.values())
    if len(values) <= _MAX_VENDOR_TAGS:
        return values
    without_vendor = [item for item in values if item.casefold() != "vendor"]
    return [*without_vendor[: _MAX_VENDOR_TAGS - 1], "Vendor"]


def _cell_text(value: Any) -> str:
    """Render a worksheet cell as stripped text."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).replace("\xa0", " ").strip()


def _emails(value: str | None) -> list[str]:
    """Extract unique email addresses from a cell."""
    found: list[str] = []
    seen: set[str] = set()
    for email in _EMAIL_RE.findall(value or ""):
        if email.casefold() not in seen:
            seen.add(email.casefold())
            found.append(email[:320])
    return found


def _phones(value: str | None) -> list[tuple[str, str]]:
    """Parse digit runs into ISD code and national number pairs."""
    phones: list[tuple[str, str]] = []
    seen: set[str] = set()
    for run in re.findall(r"\d{10,15}", value or ""):
        digits = run[1:] if len(run) == 11 and run.startswith("0") else run
        if len(digits) == 10:
            parsed = ("+91", digits)
        elif len(digits) > 10 and 1 <= len(digits[:-10]) <= 3:
            parsed = (f"+{digits[:-10]}", digits[-10:])
        else:
            continue
        if parsed[1] in seen:
            continue
        seen.add(parsed[1])
        phones.append(parsed)
    return phones


def _person_name(value: str) -> tuple[str | None, str | None, str | None]:
    """Split a contact person cell into prefix, first name, and last name."""
    text = " ".join(value.split())
    if not text:
        return None, None, None
    match = re.match(r"^(mr|mrs|ms|miss|dr|prof)\.?\s*(.*)$", text, flags=re.IGNORECASE)
    prefix = None
    rest = text
    if match:
        prefix = _NAME_PREFIXES[match.group(1).casefold()]
        rest = (match.group(2) or "").strip()
    parts = rest.split()
    if not parts:
        return prefix, None, None
    if len(parts) == 1:
        return prefix, parts[0][:100], None
    return prefix, parts[0][:100], " ".join(parts[1:])[:100]


def _service_tag(value: str | None) -> str | None:
    """Return a service tag, skipping blank cells and example text."""
    text = " ".join((value or "").split())
    if not text or text.casefold().startswith("e.g"):
        return None
    return text[:100]


def _website(value: str | None) -> str | None:
    """Return a website URL, adding https when the cell has no scheme."""
    text = (value or "").split()[0].strip(".,;") if value else ""
    if not text or "@" in text or "." not in text:
        return None
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = f"https://{text}"
    return text[:500]


def _clip(value: str | None, limit: int) -> str | None:
    """Collapse whitespace and keep text within the field limit."""
    text = " ".join((value or "").split())
    return text[:limit] if text else None


def _extend_text(target: list[str], values: list[str]) -> None:
    """Append values that are not already in the list, ignoring case."""
    seen = {item.casefold() for item in target}
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            target.append(value)


def _extend_phones(target: list[tuple[str, str]], values: list[tuple[str, str]]) -> None:
    """Append phone pairs whose national number is not already present."""
    seen = {number for _, number in target}
    for isd, number in values:
        if number not in seen:
            seen.add(number)
            target.append((isd, number))
