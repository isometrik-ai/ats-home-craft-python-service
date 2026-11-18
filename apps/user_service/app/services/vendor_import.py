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
    AddressInput,
    BillingPreferences,
    NoteItem,
    Phone,
    Website,
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
from apps.user_service.app.utils.common_utils import coerce_json_list
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
    _validate_vendor_upload(filename, content)
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
        saved = await _import_one_vendor(
            service,
            vendor=vendor,
            organization_id=organization_id,
            project_id=project_id,
            errors=errors,
        )
        if saved is None:
            continue
        company_id, was_created, saved_contact_ids = saved
        company_ids.append(company_id)
        contact_ids.extend(saved_contact_ids)
        created += int(was_created)
        updated += int(not was_created)
    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "company_ids": company_ids,
        "contact_ids": contact_ids,
    }


async def _import_one_vendor(
    service: CompaniesService,
    *,
    vendor: dict[str, Any],
    organization_id: str,
    project_id: str | None,
    errors: list[dict[str, Any]],
) -> tuple[str, bool, list[str]] | None:
    """Save one grouped vendor. Company failures are recorded and skipped."""
    try:
        company_id, was_created, linked_ids, has_primary = await _save_vendor_company(
            service,
            vendor=vendor,
            organization_id=organization_id,
            project_id=project_id,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Vendor import failed for %s", vendor["name"])
        errors.extend(_vendor_row_errors(vendor, exc))
        return None
    saved_ids, contact_errors = await _save_vendor_contacts(
        service,
        company_id=company_id,
        vendor=vendor,
        linked_ids=linked_ids,
        has_primary=has_primary,
    )
    errors.extend(contact_errors)
    return company_id, was_created, saved_ids


def _validate_vendor_upload(filename: str, content: bytes) -> None:
    """Reject uploads that are not an .xlsx workbook within the size limit."""
    if not filename.lower().endswith(".xlsx") or not content:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    if len(content) > _MAX_VENDOR_WORKBOOK_BYTES:
        raise ValidationException(
            message_key="companies.errors.import_workbook_invalid",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )


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
    company_id = found.get(vendor["name"].strip().casefold())
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
    merged = _vendor_tags([*_string_list(current.get("tags")), *vendor["tags"]])
    if merged != _string_list(current.get("tags")):
        await service.update_company(
            company_id=company_id,
            body=UpdateCompanyRequest(tags=merged),
        )
    linked_ids, has_primary = _linked_contact_ids(current.get("contacts"))
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
        contact_id, has_primary, error = await _save_one_vendor_contact(
            service,
            contacts_service=contacts_service,
            company_id=company_id,
            vendor=vendor,
            contact=contact,
            linked_ids=linked_ids,
            has_primary=has_primary,
        )
        if error is not None:
            errors.append(error)
        if contact_id:
            saved.append(contact_id)
    return saved, errors


async def _save_one_vendor_contact(
    service: CompaniesService,
    *,
    contacts_service: ContactsService,
    company_id: str,
    vendor: dict[str, Any],
    contact: dict[str, Any],
    linked_ids: set[str],
    has_primary: bool,
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Attach one contact and turn a failure into a row error."""
    try:
        contact_id, has_primary = await _attach_vendor_contact(
            service,
            contacts_service=contacts_service,
            company_id=company_id,
            contact=contact,
            linked_ids=linked_ids,
            has_primary=has_primary,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Vendor contact import failed for %s", vendor["name"])
        return None, has_primary, _contact_row_error(vendor, contact, _import_error_message(exc))
    if contact_id is None:
        return (
            None,
            has_primary,
            _contact_row_error(vendor, contact, "Contact has no usable email or mobile number."),
        )
    return contact_id, has_primary, None


async def _attach_vendor_contact(
    service: CompaniesService,
    *,
    contacts_service: ContactsService,
    company_id: str,
    contact: dict[str, Any],
    linked_ids: set[str],
    has_primary: bool,
) -> tuple[str | None, bool]:
    """Link a contact that already exists, or create it on this company."""
    existing_id = await _find_vendor_contact_id(service, contact)
    if existing_id is None and not contact["emails"] and not contact["phones"]:
        return None, has_primary
    make_primary = not has_primary
    if existing_id and existing_id in linked_ids:
        return existing_id, has_primary
    if existing_id:
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
        return existing_id, has_primary or make_primary

    created = await contacts_service.create_contact(
        _vendor_contact_request(
            contact=contact,
            company_id=company_id,
            is_primary=make_primary,
        )
    )
    contact_id = str(created.get("contact_id") or "")
    if not contact_id:
        return None, has_primary
    linked_ids.add(contact_id)
    return contact_id, True


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


def _group_vendor_sheet(payload: bytes) -> list[dict[str, Any]]:
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
        mapped = _map_vendor_row(header, values)
        name = " ".join(mapped["company_name"].split())[:200]
        if not name:
            continue
        vendor = companies.get(name.casefold())
        if vendor is None:
            vendor = _empty_vendor(name)
            companies[name.casefold()] = vendor
        _merge_vendor_row(vendor, mapped, first_data_row + offset)
    return list(companies.values())


def _map_vendor_row(header: dict[int, str], values: tuple[Any, ...]) -> dict[str, str]:
    """Read one worksheet row into the known vendor fields."""
    mapped = {name: "" for name in set(_VENDOR_HEADERS.values())}
    for index, field_name in header.items():
        if index < len(values):
            mapped[field_name] = _cell_text(values[index])
    return mapped


def _empty_vendor(name: str) -> dict[str, Any]:
    """Return a grouped vendor with empty collected fields."""
    return {
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


def _vendor_sheet_rows(workbook: Any) -> tuple[dict[int, str], list[Any], int] | None:
    """Return the header map and data rows from the Vendors sheet."""
    sheets = list(workbook.worksheets)
    preferred = [sheet for sheet in sheets if sheet.title.strip().casefold() == "vendors"]
    for sheet in preferred or sheets:
        rows = list(sheet.iter_rows(values_only=True))
        for index, row in enumerate(rows):
            header = _vendor_header(row)
            fields = set(header.values())
            if "company_name" in fields and "service" in fields:
                return header, rows[index + 1 :], index + 2
    return None


def _vendor_header(row: Any) -> dict[int, str]:
    """Map worksheet columns whose titles match the vendor template."""
    header: dict[int, str] = {}
    for column, cell in enumerate(row or ()):
        key = re.sub(r"\s+", " ", _cell_text(cell).casefold().replace("*", "")).strip()
        field_name = _VENDOR_HEADERS.get(key)
        if field_name and field_name not in header.values():
            header[column] = field_name
    return header


def _merge_vendor_row(vendor: dict[str, Any], mapped: dict[str, str], row_number: int) -> None:
    """Add one worksheet row onto a grouped vendor."""
    vendor["rows"].append(row_number)
    _extend_text(vendor["emails"], _emails(mapped.get("company_email")))
    _extend_phones(vendor["phones"], _phones(mapped.get("company_phone")))
    service_tag = _service_tag(mapped.get("service"))
    if service_tag:
        _extend_text(vendor["tags"], [service_tag])
    _merge_vendor_fields(vendor, mapped)
    person = mapped.get("contact_person") or ""
    emails = _emails(mapped.get("contact_email"))
    phones = _phones(mapped.get("contact_mobile"))
    if not person and not emails and not phones:
        return
    _merge_vendor_contact(vendor, mapped, row_number, service_tag, person, emails, phones)


def _merge_vendor_fields(vendor: dict[str, Any], mapped: dict[str, str]) -> None:
    """Keep the first non-empty company field from the grouped rows."""
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


def _merge_vendor_contact(
    vendor: dict[str, Any],
    mapped: dict[str, str],
    row_number: int,
    service_tag: str | None,
    person: str,
    emails: list[str],
    phones: list[tuple[str, str]],
) -> None:
    """Fold the row's person into the company's contact list."""
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
    if emails or phones or contact["emails"] or contact["phones"]:
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
    return list(seen.values())


def _string_list(value: Any) -> list[str]:
    """Return stripped strings from a list, or an empty list for other values."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _linked_contact_ids(raw_contacts: Any) -> tuple[set[str], bool]:
    """Return contact ids already on the company and whether one is primary."""
    contacts = coerce_json_list(raw_contacts)
    linked = {str(item.get("id")) for item in contacts if isinstance(item, dict) and item.get("id")}
    has_primary = any(bool(item.get("is_primary")) for item in contacts if isinstance(item, dict))
    return linked, has_primary


def _vendor_row_errors(vendor: dict[str, Any], exc: Exception) -> list[dict[str, Any]]:
    """Build one error entry for every worksheet row of a failed company."""
    message = _import_error_message(exc)
    return [
        {"row": row_number, "company": vendor["name"], "message": message}
        for row_number in vendor["rows"]
    ]


def _contact_row_error(vendor: dict[str, Any], contact: dict[str, Any], message: str) -> dict:
    """Build the row error returned when one contact cannot be saved."""
    return {
        "row": contact["rows"][0] if contact["rows"] else None,
        "company": vendor["name"],
        "message": message,
    }


def _import_error_message(exc: Exception) -> str:
    """Prefer a message key, then the exception text, for an import row error."""
    message_key = getattr(exc, "message_key", None)
    if isinstance(message_key, str) and message_key:
        return message_key
    return (str(exc).strip() or exc.__class__.__name__)[:500]


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
        parsed = _parse_phone_digits(run)
        if parsed is None or parsed[1] in seen:
            continue
        seen.add(parsed[1])
        phones.append(parsed)
    return phones


def _parse_phone_digits(run: str) -> tuple[str, str] | None:
    """Split a digit run into an ISD code and a 10-digit national number."""
    digits = run[1:] if len(run) == 11 and run.startswith("0") else run
    if len(digits) == 10:
        return "+91", digits
    if len(digits) > 10 and 1 <= len(digits[:-10]) <= 3:
        return f"+{digits[:-10]}", digits[-10:]
    return None


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
