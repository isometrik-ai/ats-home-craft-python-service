"""Unit tests for ContactsService public methods."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg import UniqueViolationError
from fastapi import BackgroundTasks

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    AddressUpdateItem,
    Email,
    Phone,
    SocialPageInput,
    SocialPagesUpdate,
    SocialPageUpdateItem,
    Website,
    WorkHistoryInput,
    WorkHistoryUpdate,
)
from apps.user_service.app.schemas.companies import CreateCompanyRequest
from apps.user_service.app.schemas.contacts import (
    CommunicationPreferences,
    ContactCompaniesCreate,
    ContactCompanyAssociationAdd,
    ContactCompanyAssociationCreate,
    ContactCompanyAssociationCreateInline,
    ContactCompanyAssociationUpdate,
    ContactCompanyUpdate,
    ContactLeadAssociation,
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    CompanyEventType,
    ContactBloodGroup,
    ContactEventType,
    ContactGender,
    ContactType,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
COMPANY_ID = "880e8400-e29b-41d4-a716-446655440003"
STAGE_ID = "990e8400-e29b-41d4-a716-446655440004"
USER_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    """Build user context for contacts tests."""
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


class _FakeContactsRepo:
    """Configurable fake ContactsRepository."""

    def __init__(
        self,
        *,
        contact_details: dict[str, Any] | None = None,
        contacts: list[dict[str, Any]] | None = None,
        total: int | None = None,
        overview: dict[str, int] | None = None,
        by_ids: list[dict[str, Any]] | None = None,
        contact_for_update: dict[str, Any] | None = None,
        soft_deleted: dict[str, Any] | None = None,
        contact_id_by_email: str | None = None,
        create_result: dict[str, Any] | None = None,
        updated_row: dict[str, Any] | None = None,
        contact_phones: list[dict[str, Any]] | None = None,
        address_rows_created: list[dict[str, Any]] | None = None,
        address_update_results: dict[str, dict[str, Any]] | None = None,
        create_raises: Exception | None = None,
        echo_update: bool = False,
    ) -> None:
        self.contact_details = contact_details
        self.contacts = contacts or []
        self.total = total if total is not None else len(self.contacts)
        self.overview = overview or {"total": 0, "owners": 0, "tenants": 0, "vendors": 0}
        self.by_ids = by_ids or []
        self.contact_for_update = contact_for_update
        self.soft_deleted = soft_deleted
        self.contact_id_by_email = contact_id_by_email
        self.create_result = create_result or {
            "contact_id": CONTACT_ID,
            "company_id": None,
            "contact": {"id": CONTACT_ID, "email": "new@example.com"},
        }
        self.updated_row = updated_row
        self.last_list_kwargs: dict[str, Any] | None = None
        self.last_overview_kwargs: dict[str, Any] | None = None
        self.last_by_ids_kwargs: dict[str, Any] | None = None
        self.last_update_kwargs: dict[str, Any] | None = None
        self.last_create_kwargs: dict[str, Any] | None = None
        self.contact_phones = contact_phones
        self.address_rows_created = address_rows_created
        self.address_update_results = address_update_results or {}
        self.create_raises = create_raises
        self.echo_update = echo_update
        self.deleted_address_ids: list[str] = []
        self.last_address_create_rows: list[dict[str, Any]] | None = None

    async def get_contact_details(self, *, contact_id: str, organization_id: str):
        """Return configured contact details."""
        del contact_id, organization_id
        return self.contact_details

    async def get_contact_details_by_phone(self, *, organization_id: str, phone_number: str):
        """Return configured contact details by phone."""
        del organization_id, phone_number
        return self.contact_details

    async def get_contact_id_by_email(self, *, organization_id: str, email: str):
        """Return configured contact id for email lookup."""
        del organization_id, email
        return self.contact_id_by_email

    async def list_contacts(self, **kwargs):
        """Return paginated contacts."""
        self.last_list_kwargs = kwargs
        return self.contacts, self.total

    async def get_contact_overview(self, **kwargs):
        """Return overview counts."""
        self.last_overview_kwargs = kwargs
        return self.overview

    async def get_contacts_by_ids(self, **kwargs):
        """Return minimal contact rows."""
        self.last_by_ids_kwargs = kwargs
        return self.by_ids

    async def get_contact_for_update(self, *, contact_id: str, organization_id: str):
        """Return contact row for update."""
        del contact_id, organization_id
        return self.contact_for_update

    async def soft_delete_contact(self, *, contact_id: str, organization_id: str):
        """Return soft-deleted row."""
        del contact_id, organization_id
        return self.soft_deleted

    async def create_contact_with_optional_company_link(self, **kwargs):
        """Return configured create result."""
        self.last_create_kwargs = kwargs
        if self.create_raises is not None:
            raise self.create_raises
        return dict(self.create_result)

    async def get_contact_phones_for_update(self, *, contact_id: str, organization_id: str):
        """Return configured phones for add_phones flows."""
        del contact_id, organization_id
        return self.contact_phones

    async def delete_contact_addresses(self, *, contact_id: str, address_ids: list[str]):
        """Record deleted address ids."""
        del contact_id
        self.deleted_address_ids = list(address_ids)

    async def update_contact_address(
        self,
        *,
        contact_id: str,
        address_id: str,
        update_data: dict[str, Any],
    ):
        """Return configured address update row."""
        del contact_id, update_data
        return self.address_update_results.get(str(address_id))

    async def create_contact_addresses(self, rows: list[dict[str, Any]]):
        """Return configured inserted address rows."""
        self.last_address_create_rows = rows
        if self.address_rows_created is not None:
            return self.address_rows_created
        return [{"id": f"addr-{index}", **row} for index, row in enumerate(rows)]

    async def update_contact(self, *, contact_id: str, organization_id: str, update_data: dict):
        """Return configured updated row."""
        del contact_id, organization_id
        self.last_update_kwargs = {"update_data": update_data}
        if self.echo_update:
            return dict(update_data)
        return self.updated_row

    async def insert_contact(self, contact_data: dict[str, Any]):
        """Return inserted property-management contact row."""
        self.last_insert_contact = contact_data
        return dict(contact_data)


class _FakeCompaniesRepo:
    """Configurable fake CompaniesRepository."""

    def __init__(self, *, by_name: dict[str, str] | None = None) -> None:
        self.by_name = by_name or {}

    async def get_company_ids_by_names(self, *, organization_id: str, names: list[str]):
        """Return company ids keyed by normalized name."""
        del organization_id, names
        return dict(self.by_name)


class _FakeOrgRepo:
    """Configurable fake OrganizationRepository."""

    _DEFAULT = {"id": ORG_ID, "name": "Test Org"}

    def __init__(self, *, organization: dict[str, Any] | None | object = ...) -> None:
        if organization is ...:
            self.organization: dict[str, Any] | None = dict(self._DEFAULT)
        else:
            self.organization = organization  # type: ignore[assignment]

    async def get_organization_by_id(self, org_id: str):
        """Return configured organization."""
        del org_id
        return self.organization


class _FakeContactCompaniesRepo:
    """Configurable fake ContactCompaniesRepository."""

    def __init__(
        self,
        *,
        delta_result: dict[str, Any] | None = None,
        companies_snapshot: list[dict[str, Any]] | None = None,
    ) -> None:
        self.delta_result = delta_result or {
            "created_company_id": None,
            "companies": [],
        }
        self.companies_snapshot = companies_snapshot or []
        self.last_delta_kwargs: dict[str, Any] | None = None

    async def apply_companies_update_delta(self, **kwargs):
        """Return configured delta result."""
        self.last_delta_kwargs = kwargs
        return dict(self.delta_result)

    async def get_contact_companies_snapshot(self, *, organization_id: str, contact_id: str):
        """Return post-update companies snapshot."""
        del organization_id, contact_id
        return list(self.companies_snapshot)


def _contact_detail(**overrides) -> dict[str, Any]:
    """Build a contact detail row."""
    row = {
        "id": CONTACT_ID,
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phones": [{"phone_number": "1234567890", "phone_isd_code": "+1", "is_primary": True}],
        "tags": ["vip"],
        "companies": [],
        "addresses": [],
        "notes": [{"title": "Note", "content": "Hello"}],
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _contact_list_row(**overrides) -> dict[str, Any]:
    """Build a contact list row."""
    row = {
        "id": CONTACT_ID,
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phones": "[]",
        "company_names": '["Acme"]',
        "tags": ["vip"],
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _service(
    *,
    contacts_repo: _FakeContactsRepo | None = None,
    companies_repo: _FakeCompaniesRepo | None = None,
    cc_repo: _FakeContactCompaniesRepo | None = None,
    org_repo: _FakeOrgRepo | None = None,
) -> ContactsService:
    """Build ContactsService with fake repos."""
    svc = ContactsService(db_connection=MagicMock(), user_context=_ctx())
    svc.contacts_repo = contacts_repo or _FakeContactsRepo()
    svc.companies_repo = companies_repo or _FakeCompaniesRepo()
    svc.cc_repo = cc_repo or _FakeContactCompaniesRepo()
    svc.org_repo = org_repo or _FakeOrgRepo()
    return svc


def _patch_create_identity(
    svc: ContactsService,
    *,
    validated_custom_fields: list[dict[str, Any]] | None = None,
) -> None:
    """Stub identity provisioning for create_contact tests."""

    async def _fake_provision_identity(**_kwargs: Any) -> tuple[str, str | None, str | None]:
        return (USER_ID, "iso-new", "temp-pass")

    async def _fake_validate_custom_fields(_payload: Any) -> list[dict[str, Any]]:
        return validated_custom_fields if validated_custom_fields is not None else []

    svc._provision_identity = _fake_provision_identity  # type: ignore[method-assign]
    svc._validate_custom_fields_for_create = _fake_validate_custom_fields  # type: ignore[method-assign]


def _unique_violation(constraint_name: str) -> UniqueViolationError:
    """Build a UniqueViolationError with a constraint_name attribute."""
    exc = UniqueViolationError("duplicate key value")
    exc.constraint_name = constraint_name  # type: ignore[attr-defined]
    return exc


def _patch_custom_fields_merge(
    monkeypatch: pytest.MonkeyPatch, *, merged: list[dict[str, Any]]
) -> None:
    """Patch CustomFieldService.merge_for_update to return merged payload."""

    class _FakeCFS:
        """CustomFieldService fake with configurable merge output."""

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def validate_dropdown_filters_for_entity(self, entity_type, parsed_filters):
            del entity_type, parsed_filters

        async def merge_for_update(self, patch, merged_existing, entity_type):
            del patch, merged_existing, entity_type
            return merged

        async def validate_for_create(self, payload, entity_type):
            del entity_type
            return payload or []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        _FakeCFS,
    )


def _patch_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch CustomFieldService for list/search/update flows."""

    class _FakeCFS:
        """Minimal CustomFieldService fake."""

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def validate_dropdown_filters_for_entity(self, entity_type, parsed_filters):
            """No-op validation."""
            del entity_type, parsed_filters

        async def merge_for_update(self, patch, merged_existing, entity_type):
            """Return existing custom fields unchanged."""
            del patch, entity_type
            return merged_existing if isinstance(merged_existing, list) else []

        async def validate_for_create(self, payload, entity_type):
            """Return payload unchanged for create validation."""
            del entity_type
            return payload or []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        _FakeCFS,
    )


@pytest.mark.asyncio
async def test_list_contacts_returns_items(monkeypatch):
    """List contacts forwards org filters and normalizes rows."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeContactsRepo(contacts=[_contact_list_row()], total=1)
    svc = _service(contacts_repo=repo)

    result = await svc.list_contacts(
        search="jane",
        status=ClientStatus.ACTIVE.value,
        contact_type=None,
        dropdown_filters=None,
        page=1,
        page_size=20,
    )

    assert result["total"] == 1
    assert result["items"][0]["first_name"] == "Jane"
    assert repo.last_list_kwargs["organization_id"] == ORG_ID
    assert repo.last_list_kwargs["search"] == "jane"


@pytest.mark.asyncio
async def test_get_contact_details_found():
    """Get contact details normalizes UUIDs and timestamps."""
    repo = _FakeContactsRepo(contact_details=_contact_detail())
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details(contact_id=CONTACT_ID)

    assert result["id"] == CONTACT_ID
    assert result["organization_id"] == ORG_ID
    assert result["created_at"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_get_contact_details_not_found():
    """Missing contact raises NotFoundException."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_details=None))
    with pytest.raises(NotFoundException):
        await svc.get_contact_details(contact_id=CONTACT_ID)


@pytest.mark.asyncio
async def test_get_contact_details_by_phone_found():
    """Phone lookup returns normalized contact details."""
    repo = _FakeContactsRepo(contact_details=_contact_detail())
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details_by_phone(phone_number="1234567890")

    assert result["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_get_contact_details_by_phone_missing():
    """Phone lookup raises when no contact matches."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_details=None))
    with pytest.raises(NotFoundException):
        await svc.get_contact_details_by_phone(phone_number="9999999999")


@pytest.mark.asyncio
async def test_get_contact_details_by_email_found():
    """Email lookup resolves id then loads details."""
    repo = _FakeContactsRepo(
        contact_id_by_email=CONTACT_ID,
        contact_details=_contact_detail(),
    )
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details_by_email(email="jane@example.com")

    assert result["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_get_contact_details_by_email_missing():
    """Email lookup raises when contact id is absent."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_id_by_email=None))
    with pytest.raises(NotFoundException):
        await svc.get_contact_details_by_email(email="missing@example.com")


@pytest.mark.asyncio
async def test_get_contacts_by_ids_maps_names():
    """Batch lookup returns display names and emails."""
    repo = _FakeContactsRepo(
        by_ids=[
            {
                "id": CONTACT_ID,
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "external_contact_id": "ext-1",
            }
        ]
    )
    svc = _service(contacts_repo=repo)

    result = await svc.get_contacts_by_ids(contact_ids=[CONTACT_ID, CONTACT_ID])

    assert len(result) == 1
    assert result[0]["name"] == "Jane Doe"
    assert result[0]["external_contact_id"] == "ext-1"
    assert repo.last_by_ids_kwargs["contact_ids"] == [CONTACT_ID]


@pytest.mark.asyncio
async def test_get_contacts_by_ids_empty_input():
    """Empty id list short-circuits without repo call."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)

    result = await svc.get_contacts_by_ids(contact_ids=["", "  "])

    assert result == []
    assert repo.last_by_ids_kwargs is None


@pytest.mark.asyncio
async def test_soft_delete_contact_success():
    """Soft delete returns old and new snapshots."""
    current = {"id": CONTACT_ID, "status": ClientStatus.ACTIVE.value}
    updated = {"id": CONTACT_ID, "status": ClientStatus.DELETED.value}
    repo = _FakeContactsRepo(contact_for_update=current, soft_deleted=updated)
    svc = _service(contacts_repo=repo)

    result = await svc.soft_delete_contact(contact_id=CONTACT_ID)

    assert result["old_data"]["status"] == ClientStatus.ACTIVE.value
    assert result["new_data"]["status"] == ClientStatus.DELETED.value


@pytest.mark.asyncio
async def test_soft_delete_contact_not_found():
    """Soft delete raises when contact is missing."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_for_update=None))
    with pytest.raises(NotFoundException):
        await svc.soft_delete_contact(contact_id=CONTACT_ID)


@pytest.mark.asyncio
async def test_get_contact_overview_forwards_status():
    """Overview forwards organization_id and status filter."""
    repo = _FakeContactsRepo(overview={"total": 5, "owners": 3, "tenants": 1, "vendors": 1})
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_overview(status=ClientStatus.ACTIVE.value)

    assert result["total"] == 5
    assert repo.last_overview_kwargs == {
        "organization_id": ORG_ID,
        "status": ClientStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_search_contacts_uses_typesense():
    """Search contacts maps Typesense hits to summaries."""
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=None)
    fake_typesense.search = AsyncMock(
        return_value={
            "hits": [
                {
                    "document": {
                        "id": CONTACT_ID,
                        "organization_id": ORG_ID,
                        "status": ClientStatus.ACTIVE.value,
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "email": "jane@example.com",
                        "phones_display": [{"phone_number": "1234567890", "phone_isd_code": "+1"}],
                        "company_names": ["Acme"],
                        "tags": ["vip"],
                        "created_at": 1735689600,
                        "updated_at": 1735776000,
                    }
                }
            ],
            "found": 1,
        }
    )
    svc._typesense = fake_typesense

    result = await svc.search_contacts(
        query="jane@example.com",
        page=1,
        page_size=10,
        status=ClientStatus.ACTIVE.value,
    )

    assert result["total"] == 1
    assert result["items"][0]["email"] == "jane@example.com"
    fake_typesense.search.assert_awaited_once()


def test_typesense_hits_to_summaries():
    """Static mapper converts Typesense documents."""
    hits = [
        {
            "document": {
                "id": CONTACT_ID,
                "organization_id": ORG_ID,
                "status": ClientStatus.ACTIVE.value,
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phones_display": [],
                "company_names": [],
                "tags": [],
                "created_at": 1735689600,
                "updated_at": 1735776000,
            }
        }
    ]
    items = ContactsService.typesense_hits_to_contact_summaries(hits)
    assert items[0]["first_name"] == "Jane"


def test_format_contact_display_name():
    """Display name joins first and last name."""
    name = ContactsService._format_contact_display_name(first_name="Jane", last_name="Doe")
    assert name == "Jane Doe"


def test_normalize_external_contact_id():
    """External id strips whitespace and blanks become None."""
    assert ContactsService._normalize_external_contact_id("  ext-1  ") == "ext-1"
    assert ContactsService._normalize_external_contact_id("   ") is None


@pytest.mark.asyncio
async def test_create_contact_minimal_body():
    """Create contact persists minimal identity fields."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    result = await svc.create_contact(
        CreateContactRequest(
            email="new@example.com",
            first_name="New",
            last_name="User",
        )
    )

    assert result["contact_id"] == CONTACT_ID
    assert result["new_data"]["email"] == "new@example.com"
    assert repo.last_create_kwargs["organization_id"] == ORG_ID
    contact_data = repo.last_create_kwargs["contact_data"]
    assert contact_data["first_name"] == "New"
    assert contact_data["email"] == "new@example.com"


@pytest.mark.asyncio
async def test_create_contact_duplicate_email():
    """Create contact rejects duplicate org email."""
    repo = _FakeContactsRepo(contact_id_by_email=CONTACT_ID)
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    with pytest.raises(ConflictException):
        await svc.create_contact(CreateContactRequest(email="dup@example.com"))


@pytest.mark.asyncio
async def test_create_contact_org_not_found():
    """Create contact raises when organization is missing."""
    svc = _service(org_repo=_FakeOrgRepo(organization=None))
    _patch_create_identity(svc)

    with pytest.raises(NotFoundException):
        await svc.create_contact(CreateContactRequest(email="new@example.com"))


@pytest.mark.asyncio
async def test_update_contact_scalar_fields(monkeypatch):
    """Update contact patches scalar fields and returns audit."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(first_name="Jane", last_name="Doe")
    updated = {"first_name": "Janet", "updated_at": datetime(2026, 2, 1, tzinfo=timezone.utc)}
    repo = _FakeContactsRepo(contact_for_update=current, updated_row=updated)
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(first_name="Janet"),
    )

    assert result["ok"] is True
    assert result["new_data"]["first_name"] == "Janet"
    assert repo.last_update_kwargs["update_data"]["first_name"] == "Janet"


@pytest.mark.asyncio
async def test_update_contact_not_found():
    """Update contact raises when contact is missing."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_for_update=None))

    with pytest.raises(NotFoundException):
        await svc.update_contact(
            contact_id=CONTACT_ID,
            body=UpdateContactRequest(first_name="X"),
        )


@pytest.mark.asyncio
async def test_update_contact_multi_primary_phone():
    """Update contact rejects multiple primary phones."""
    current = _contact_detail(
        user_id="770e8400-e29b-41d4-a716-446655440002",
        phones=[{"phone_number": "111", "phone_isd_code": "+1", "is_primary": True}],
    )
    repo = _FakeContactsRepo(contact_for_update=current)
    svc = _service(contacts_repo=repo)
    body = UpdateContactRequest(
        phones=[
            Phone(phone_number="111", phone_isd_code="+1", is_primary=True),
            Phone(phone_number="222", phone_isd_code="+1", is_primary=True),
        ]
    )

    with pytest.raises(ValidationException):
        await svc.update_contact(contact_id=CONTACT_ID, body=body)


@pytest.mark.asyncio
async def test_update_contact_with_company_delta(monkeypatch):
    """Update contact applies company association delta."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail()
    updated = {"first_name": "Janet"}
    companies = [{"company_id": "co-1", "name": "Acme", "is_primary": True}]
    repo = _FakeContactsRepo(contact_for_update=current, updated_row=updated)
    cc_repo = _FakeContactCompaniesRepo(
        delta_result={"created_company_id": None, "companies": companies},
        companies_snapshot=companies,
    )
    svc = _service(contacts_repo=repo, cc_repo=cc_repo)
    delta = ContactCompanyUpdate(
        add_associations=[ContactCompanyAssociationAdd(company_id="co-1", is_primary=True)]
    )

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(first_name="Janet", company_association=delta),
    )

    assert result["created_company_id"] is None
    assert result["companies_delta"]["affected_company_ids"] == ["co-1"]
    assert cc_repo.last_delta_kwargs["add_company_ids"] == ["co-1"]


@pytest.mark.asyncio
async def test_apply_companies_delta_add_assoc():
    """Apply companies delta links existing company."""
    current = {"id": CONTACT_ID}
    companies = [{"company_id": "co-2", "name": "Beta"}]
    repo = _FakeContactsRepo(contact_for_update=current)
    cc_repo = _FakeContactCompaniesRepo(companies_snapshot=companies)
    svc = _service(contacts_repo=repo, cc_repo=cc_repo)
    delta = ContactCompanyUpdate(add_associations=[ContactCompanyAssociationAdd(company_id="co-2")])

    result = await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert result["ok"] is True
    assert result["affected_company_ids"] == ["co-2"]
    assert result["companies"] == companies
    assert cc_repo.last_delta_kwargs["add_company_ids"] == ["co-2"]


@pytest.mark.asyncio
async def test_apply_companies_delta_create():
    """Apply companies delta creates and links company."""
    current = {"id": CONTACT_ID}
    companies = [{"company_id": "co-new", "name": "NewCo"}]
    cc_repo = _FakeContactCompaniesRepo(
        delta_result={"created_company_id": "co-new"},
        companies_snapshot=companies,
    )
    svc = _service(
        contacts_repo=_FakeContactsRepo(contact_for_update=current),
        cc_repo=cc_repo,
    )
    delta = ContactCompanyUpdate(
        create_and_associate=ContactCompanyAssociationCreate(name="NewCo", is_primary=True)
    )

    result = await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert result["created_company_id"] == "co-new"
    assert cc_repo.last_delta_kwargs["create_company_name"] == "NewCo"
    assert cc_repo.last_delta_kwargs["create_is_primary"] is True


@pytest.mark.asyncio
async def test_apply_companies_delta_not_found():
    """Apply companies delta raises when contact missing."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_for_update=None))
    delta = ContactCompanyUpdate(add_associations=[ContactCompanyAssociationAdd(company_id="co-1")])

    with pytest.raises(NotFoundException):
        await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)


@pytest.mark.asyncio
async def test_trigger_enrichment_calls_service(monkeypatch):
    """Trigger enrichment builds payload and calls service."""
    repo = _FakeContactsRepo(contact_details=_contact_detail())
    svc = _service(contacts_repo=repo)
    mock_run = AsyncMock()
    mock_service = MagicMock(run_client_enrichment=mock_run)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: mock_service,
    )

    await svc.trigger_enrichment(contact_id=CONTACT_ID, organization_id=ORG_ID)

    mock_run.assert_awaited_once()
    call_kwargs = mock_run.await_args.kwargs
    assert call_kwargs["client_id"] == CONTACT_ID
    assert call_kwargs["client_type"] == "person"
    assert call_kwargs["payload_data"]["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_trigger_enrichment_wrong_org():
    """Trigger enrichment rejects mismatched organization."""
    repo = _FakeContactsRepo(contact_details=_contact_detail())
    svc = _service(contacts_repo=repo)

    with pytest.raises(NotFoundException):
        await svc.trigger_enrichment(contact_id=CONTACT_ID, organization_id="other-org")


@pytest.mark.asyncio
async def test_list_contacts_forwards_filters(monkeypatch):
    """List contacts forwards contact_type and dropdown filters."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeContactsRepo(contacts=[_contact_list_row()], total=1)
    svc = _service(contacts_repo=repo)

    await svc.list_contacts(
        search=None,
        status=ClientStatus.ACTIVE.value,
        contact_type="owner",
        dropdown_filters={"550e8400-e29b-41d4-a716-446655440099": ["gold"]},
        page=2,
        page_size=10,
    )

    assert repo.last_list_kwargs["contact_type"] == "owner"
    assert repo.last_list_kwargs["dropdown_filters"] == {
        "550e8400-e29b-41d4-a716-446655440099": ["gold"]
    }
    assert repo.last_list_kwargs["page"] == 2


@pytest.mark.asyncio
async def test_search_contacts_email_params():
    """Search contacts uses email params when query has @."""
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=None)
    fake_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = fake_typesense

    await svc.search_contacts(
        query="jane@example.com",
        page=1,
        page_size=10,
        status=ClientStatus.ACTIVE.value,
    )

    params = fake_typesense.search.await_args.args[0]
    assert "query_by" in params
    assert params["q"] == "jane@example.com"


@pytest.mark.asyncio
async def test_search_contacts_phone_params():
    """Search contacts uses phone params for digit-heavy query."""
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=None)
    fake_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = fake_typesense

    await svc.search_contacts(
        query="1234567890",
        page=1,
        page_size=10,
        status=None,
    )

    params = fake_typesense.search.await_args.args[0]
    assert "query_by" in params
    assert "1234567890" in params["q"]


@pytest.mark.asyncio
async def test_search_contacts_with_embedding():
    """Search contacts adds vector query when embedding exists."""
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=[0.1, 0.2])
    fake_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = fake_typesense

    await svc.search_contacts(query="Jane Doe", page=1, page_size=5, status=None)

    params = fake_typesense.search.await_args.args[0]
    assert "vector_query" in params
    assert "0.1,0.2" in params["vector_query"]


def test_schedule_update_bg_tasks_events(monkeypatch):
    """Update background tasks schedule event and index tasks."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.EventService.publish_event_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_contacts_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_companies_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.trigger_enrichment_background",
        AsyncMock(),
    )

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=bg,
        contact_id=CONTACT_ID,
        organization_id=ORG_ID,
        body=UpdateContactRequest(first_name="Janet"),
        update_result={"created_company_id": None},
        update_event={"type": "updated"},
        event_key=CONTACT_ID,
        event_topics=ContactsService.CLIENT_KAFKA_TOPICS,
    )

    assert add_task.call_count >= 2


def test_schedule_enrichment_adds_tasks(monkeypatch):
    """Schedule enrichment registers background enrichment tasks."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.client_enrichment_enabled",
        lambda: True,
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: mock_service,
    )

    ContactsService.schedule_enrichment(
        background_tasks=bg,
        enrichment_targets=[
            {
                "client_id": CONTACT_ID,
                "organization_id": ORG_ID,
                "client_type": "person",
                "payload_data": {"first_name": "Jane"},
                "entity_table": "contacts",
            }
        ],
    )

    add_task.assert_called_once()
    assert add_task.call_args.args[0] == mock_service.run_client_enrichment


def test_schedule_typesense_indexing_contacts(monkeypatch):
    """Schedule Typesense indexing for created contact entity."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_contacts_background",
        MagicMock(),
    )

    ContactsService.schedule_typesense_indexing_for_created_entities(
        background_tasks=bg,
        created_entities=[
            {"entity_id": CONTACT_ID, "entity_table": "contacts", "action": "create"}
        ],
        organization_id=ORG_ID,
    )

    add_task.assert_called_once()


@pytest.mark.asyncio
async def test_create_lifecycle_events_for_entities():
    """Lifecycle events are created for each created entity."""
    event_service = MagicMock()
    event_service.create_lifecycle_event = AsyncMock(
        side_effect=lambda **kwargs: {"event_type": kwargs["event_type"]}
    )

    events = await ContactsService.create_lifecycle_events_for_created_entities(
        event_service=event_service,
        created_entities=[
            {"entity_id": CONTACT_ID, "entity_table": "contacts", "action": "create"},
            {
                "entity_id": "co-1",
                "entity_table": "companies",
                "action": "create_company",
            },
        ],
        organization_id=ORG_ID,
        actor_user_id="admin-1",
    )

    assert len(events) == 2
    assert event_service.create_lifecycle_event.await_count == 2


# ---------------------------------------------------------------------------
# Helper / static method coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("user@appscrip.co", "appscrip.co"),
        ("Name <bob@acme.com>", "acme.com"),
        ("", None),
        ("not-an-email", None),
        ("user@gmail.com", "gmail.com"),
    ],
)
def test_extract_email_domain(email, expected):
    """Extract email domain handles plain and display-name formats."""
    assert ContactsService._extract_email_domain(email) == expected


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("user@appscrip.co", "appscrip"),
        ("user@gmail.com", None),
        ("user@mail.yahoo.co.in", None),
        ("user@sub.gmail.com", None),
        ("", None),
    ],
)
def test_infer_company_name_from_email(email, expected):
    """Infer company name skips consumer providers and subdomains."""
    assert ContactsService._infer_company_name_from_email(email) == expected


def test_normalize_full_phone():
    """Normalize full phone strips non-digit characters."""
    assert ContactsService._normalize_full_phone("+1", "(555) 123-4567") == "15551234567"
    assert ContactsService._normalize_full_phone("", "") == ""


def test_phone_match_key():
    """Phone match key compares digits only."""
    assert (
        ContactsService._phone_match_key(phone_number="5551234", phone_isd_code="+1") == "15551234"
    )


def test_ensure_list_item_ids_generates_missing_ids():
    """Ensure list item ids assigns UUIDs when missing."""
    items = [{"platform": "linkedin", "url": "https://linkedin.com/in/jane"}]
    result = ContactsService._ensure_list_item_ids(items)
    assert result[0]["platform"] == "linkedin"
    assert result[0]["id"]


def test_build_person_payload_with_primary_phone():
    """Build person payload includes primary phone fields."""
    body = CreateContactRequest(
        email="jane@example.com",
        first_name="Jane",
        last_name="Doe",
        phones=[Phone(phone_number="1234567890", phone_isd_code="+1", is_primary=True)],
        addresses=[AddressInput(country="US")],
    )
    payload = _service()._build_person_payload(body=body, email="jane@example.com")
    assert payload["first_name"] == "Jane"
    assert payload["phone_number"] == "1234567890"
    assert payload["addresses"] == [{"country": "US"}]


def test_build_enrichment_targets_includes_company():
    """Build enrichment targets adds company when newly created."""
    targets = ContactsService._build_enrichment_targets(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        person_payload={"first_name": "Jane"},
        created_new_company=True,
        company_id=COMPANY_ID,
        company_name="Acme",
    )
    assert len(targets) == 2
    assert targets[1]["client_type"] == "company"
    assert targets[1]["skip_company_logo"] is False


def test_build_created_entities_includes_company():
    """Build created entities records contact and company rows."""
    entities = ContactsService._build_created_entities(
        contact_id=CONTACT_ID,
        created_new_company=True,
        company_id=COMPANY_ID,
    )
    assert len(entities) == 2
    assert entities[1]["action"] == "create_company"


def test_created_entity_lifecycle_type_and_module():
    """Lifecycle mapper distinguishes contacts vs companies."""
    contact_type, contact_module = ContactsService._created_entity_lifecycle_type_and_module(
        {"entity_table": "contacts"}
    )
    company_type, company_module = ContactsService._created_entity_lifecycle_type_and_module(
        {"entity_table": "companies"}
    )
    assert contact_type == ContactEventType.CREATED.value
    assert contact_module == "contacts"
    assert company_type == CompanyEventType.CREATED.value
    assert company_module == "companies"


def test_normalize_notes_for_detail_filters_invalid():
    """Normalize notes drops entries missing title or content."""
    notes = ContactsService._normalize_notes_for_detail(
        [
            {"title": "Valid", "content": "Body"},
            {"title": "", "content": "No title"},
            {"title": "No body", "content": ""},
            "not-a-dict",
        ]
    )
    assert notes == [{"title": "Valid", "content": "Body"}]


def test_typesense_hits_skips_invalid_documents():
    """Typesense mapper skips hits without valid document payloads."""
    items = ContactsService.typesense_hits_to_contact_summaries(
        [
            {"document": None},
            {"not_document": True},
            {
                "document": {
                    "id": CONTACT_ID,
                    "organization_id": ORG_ID,
                    "status": ClientStatus.ACTIVE.value,
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "email": "jane@example.com",
                    "phones_display": [],
                    "company_names": [],
                    "tags": [],
                    "created_at": 1735689600,
                    "updated_at": 1735776000,
                }
            },
        ]
    )
    assert len(items) == 1


# ---------------------------------------------------------------------------
# create_contact coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_contact_infers_new_company_from_email():
    """Create contact infers and creates company from business email domain."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo, companies_repo=_FakeCompaniesRepo(by_name={}))
    _patch_create_identity(svc)

    await svc.create_contact(CreateContactRequest(email="user@appscrip.co", first_name="User"))

    assert repo.last_create_kwargs["company_data"]["name"] == "appscrip"
    assert repo.last_create_kwargs["make_primary"] is False


@pytest.mark.asyncio
async def test_create_contact_infers_existing_company_from_email():
    """Create contact links inferred company when name already exists."""
    repo = _FakeContactsRepo()
    svc = _service(
        contacts_repo=repo,
        companies_repo=_FakeCompaniesRepo(by_name={"appscrip": COMPANY_ID}),
    )
    _patch_create_identity(svc)

    await svc.create_contact(CreateContactRequest(email="user@appscrip.co"))

    assert repo.last_create_kwargs["company_id"] == COMPANY_ID
    assert repo.last_create_kwargs["company_data"] is None


@pytest.mark.asyncio
async def test_create_contact_skips_inference_for_gmail():
    """Create contact does not infer company for consumer email domains."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    await svc.create_contact(CreateContactRequest(email="user@gmail.com"))

    assert repo.last_create_kwargs["company_id"] is None
    assert repo.last_create_kwargs["company_data"] is None


@pytest.mark.asyncio
async def test_create_contact_with_add_association():
    """Create contact links existing company from association payload."""
    repo = _FakeContactsRepo(
        create_result={
            "contact_id": CONTACT_ID,
            "company_id": COMPANY_ID,
            "contact": {"id": CONTACT_ID, "email": "new@example.com"},
        }
    )
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)
    body = CreateContactRequest(
        email="new@example.com",
        company_association=ContactCompaniesCreate(
            add_association=ContactCompanyAssociationAdd(
                company_id=COMPANY_ID,
                is_primary=True,
            )
        ),
    )

    result = await svc.create_contact(body)

    assert result["company_id"] == COMPANY_ID
    assert repo.last_create_kwargs["company_id"] == COMPANY_ID
    assert repo.last_create_kwargs["make_primary"] is True


@pytest.mark.asyncio
async def test_create_contact_with_create_and_associate_company(monkeypatch):
    """Create contact creates inline company via association payload."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeContactsRepo(
        create_result={
            "contact_id": CONTACT_ID,
            "company_id": COMPANY_ID,
            "contact": {"id": CONTACT_ID, "email": "new@example.com"},
        }
    )
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)
    body = CreateContactRequest(
        email="new@example.com",
        company_association=ContactCompaniesCreate(
            create_and_associate=ContactCompanyAssociationCreateInline(
                company=CreateCompanyRequest(name="Inline Co"),
                is_primary=True,
            )
        ),
    )

    await svc.create_contact(body)

    assert repo.last_create_kwargs["company_data"]["name"] == "Inline Co"
    assert repo.last_create_kwargs["make_primary"] is True


@pytest.mark.asyncio
async def test_create_contact_with_addresses():
    """Create contact persists optional address rows."""
    repo = _FakeContactsRepo(
        address_rows_created=[{"id": "addr-1", "country": "US", "contact_id": CONTACT_ID}]
    )
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    await svc.create_contact(
        CreateContactRequest(
            email="new@example.com",
            addresses=[AddressInput(country="US", city="Austin")],
        )
    )

    assert repo.last_address_create_rows is not None
    assert repo.last_address_create_rows[0]["country"] == "US"


@pytest.mark.asyncio
async def test_create_contact_with_custom_fields():
    """Create contact validates and persists custom fields."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    _patch_create_identity(
        svc,
        validated_custom_fields=[{"field_id": "cf-1", "value": "gold"}],
    )

    await svc.create_contact(
        CreateContactRequest(
            email="new@example.com",
            custom_fields=[{"field_id": "cf-1", "value": "gold"}],
        )
    )

    assert repo.last_create_kwargs["contact_data"]["custom_fields"] is not None


@pytest.mark.asyncio
async def test_create_contact_with_lead(monkeypatch):
    """Create contact optionally creates and links a lead."""
    repo = _FakeContactsRepo(
        create_result={
            "contact_id": CONTACT_ID,
            "company_id": COMPANY_ID,
            "contact": {"id": CONTACT_ID, "email": "new@example.com"},
        }
    )
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)
    mock_create_lead = AsyncMock(return_value={"id": "lead-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.LeadService.create_lead",
        mock_create_lead,
    )

    result = await svc.create_contact(
        CreateContactRequest(
            email="new@example.com",
            first_name="New",
            last_name="Lead",
            lead=ContactLeadAssociation(stage_id=STAGE_ID, intake_stage="web"),
        )
    )

    assert result["created_lead_id"] == "lead-1"
    mock_create_lead.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_contact_company_not_found():
    """Create contact raises when requested company link is missing."""
    repo = _FakeContactsRepo(
        create_result={
            "contact_id": CONTACT_ID,
            "company_id": None,
            "contact": {"id": CONTACT_ID, "email": "new@example.com"},
        }
    )
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    with pytest.raises(NotFoundException):
        await svc.create_contact(
            CreateContactRequest(
                email="new@example.com",
                company_association=ContactCompaniesCreate(
                    add_association=ContactCompanyAssociationAdd(company_id=COMPANY_ID)
                ),
            )
        )


@pytest.mark.asyncio
async def test_create_contact_unique_violation_user():
    """Create contact maps user/org unique violation to conflict."""
    repo = _FakeContactsRepo(create_raises=_unique_violation("uq_contacts_user_org"))
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    with pytest.raises(ConflictException):
        await svc.create_contact(CreateContactRequest(email="new@example.com"))


@pytest.mark.asyncio
async def test_create_contact_unique_violation_external_id():
    """Create contact maps external id unique violation to conflict."""
    repo = _FakeContactsRepo(create_raises=_unique_violation("uq_contacts_org_external_contact_id"))
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    with pytest.raises(ConflictException):
        await svc.create_contact(
            CreateContactRequest(email="new@example.com", external_contact_id="ext-dup")
        )


@pytest.mark.asyncio
async def test_create_contact_sends_portal_email(monkeypatch):
    """Create contact sends welcome email when portal access is enabled."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)
    mock_send = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.send_client_creation_email",
        mock_send,
    )

    await svc.create_contact(CreateContactRequest(email="portal@example.com", portal_access=True))

    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_create_contact_with_websites_and_notes():
    """Create contact stores websites in additional_data and notes jsonb."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    _patch_create_identity(svc)

    await svc.create_contact(
        CreateContactRequest(
            email="new@example.com",
            websites=[Website(url="https://example.com", type="home")],
            notes=[{"title": "Intro", "content": "Met at conference"}],
            tags=["prospect"],
        )
    )

    contact_data = repo.last_create_kwargs["contact_data"]
    assert contact_data["tags"] == ["prospect"]
    assert contact_data["notes"] is not None


# ---------------------------------------------------------------------------
# update_contact coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_contact_custom_fields_merge(monkeypatch):
    """Update contact merges custom fields when merge output changes."""
    _patch_custom_fields_merge(
        monkeypatch,
        merged=[{"field_id": "cf-1", "value": "platinum"}],
    )
    current = _contact_detail(custom_fields=[{"field_id": "cf-1", "value": "gold"}])
    updated = {"custom_fields": [{"field_id": "cf-1", "value": "platinum"}]}
    repo = _FakeContactsRepo(contact_for_update=current, updated_row=updated)
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(custom_fields=[{"field_id": "cf-1", "value": "platinum"}]),
    )

    assert result["new_data"]["custom_fields"][0]["value"] == "platinum"
    assert repo.last_update_kwargs["update_data"]["custom_fields"][0]["value"] == "platinum"


@pytest.mark.asyncio
async def test_update_contact_addresses_add(monkeypatch):
    """Update contact adds addresses via delta operations."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(addresses=[])
    repo = _FakeContactsRepo(
        contact_for_update=current,
        echo_update=True,
        address_rows_created=[
            {
                "id": "addr-new",
                "contact_id": CONTACT_ID,
                "country": "CA",
                "city": "Toronto",
                "is_primary": True,
                "created_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
            }
        ],
    )
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            addresses=AddressesUpdate(
                add=[AddressInput(country="CA", city="Toronto", is_primary=True)]
            )
        ),
    )

    assert result["new_data"]["addresses"][0]["country"] == "CA"
    assert repo.last_address_create_rows is not None


@pytest.mark.asyncio
async def test_update_contact_addresses_remove(monkeypatch):
    """Update contact removes addresses via delta operations."""
    _patch_custom_fields(monkeypatch)
    addr_id = "addr-old"
    current = _contact_detail(
        addresses=[
            {
                "id": addr_id,
                "country": "US",
                "is_primary": True,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]
    )
    repo = _FakeContactsRepo(contact_for_update=current, echo_update=True)
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(addresses=AddressesUpdate(remove=[addr_id])),
    )

    assert repo.deleted_address_ids == [addr_id]
    assert result["new_data"]["addresses"] == []


@pytest.mark.asyncio
async def test_update_contact_addresses_update(monkeypatch):
    """Update contact patches existing address rows."""
    _patch_custom_fields(monkeypatch)
    addr_id = "addr-1"
    current = _contact_detail(
        addresses=[
            {
                "id": addr_id,
                "country": "US",
                "city": "Austin",
                "is_primary": True,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]
    )
    repo = _FakeContactsRepo(
        contact_for_update=current,
        echo_update=True,
        address_update_results={
            addr_id: {
                "id": addr_id,
                "country": "US",
                "city": "Dallas",
                "is_primary": True,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        },
    )
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            addresses=AddressesUpdate(update=[AddressUpdateItem(id=addr_id, city="Dallas")])
        ),
    )

    assert result["new_data"]["addresses"][0]["city"] == "Dallas"


@pytest.mark.asyncio
async def test_update_contact_social_pages_add(monkeypatch):
    """Update contact adds social pages through jsonb list delta."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(social_pages=[])
    repo = _FakeContactsRepo(contact_for_update=current, echo_update=True)
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            social_pages=SocialPagesUpdate(
                add=[SocialPageInput(platform="linkedin", url="https://linkedin.com/in/jane")]
            )
        ),
    )

    pages = result["new_data"]["social_pages"]
    assert len(pages) == 1
    assert pages[0]["platform"] == "linkedin"


@pytest.mark.asyncio
async def test_update_contact_social_pages_update_not_found(monkeypatch):
    """Update contact raises when social page id is missing."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(
        social_pages=[{"id": "sp-1", "platform": "x", "url": "https://x.com"}]
    )
    repo = _FakeContactsRepo(contact_for_update=current)
    svc = _service(contacts_repo=repo)

    with pytest.raises(NotFoundException):
        await svc.update_contact(
            contact_id=CONTACT_ID,
            body=UpdateContactRequest(
                social_pages=SocialPagesUpdate(
                    update=[SocialPageUpdateItem(id="missing", platform="linkedin")]
                )
            ),
        )


@pytest.mark.asyncio
async def test_update_contact_work_history_add(monkeypatch):
    """Update contact adds work history items."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(work_history=[])
    repo = _FakeContactsRepo(contact_for_update=current, echo_update=True)
    svc = _service(contacts_repo=repo)

    result = await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            work_history=WorkHistoryUpdate(
                add=[WorkHistoryInput(job_title="Engineer", company="Acme")]
            )
        ),
    )

    assert len(result["new_data"]["work_history"]) == 1
    assert result["new_data"]["work_history"][0]["job_title"] == "Engineer"


@pytest.mark.asyncio
async def test_update_contact_scalar_profile_fields(monkeypatch):
    """Update contact patches gender, blood group, and communication preferences."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail()
    repo = _FakeContactsRepo(
        contact_for_update=current,
        updated_row={
            "gender": ContactGender.FEMALE.value,
            "blood_group": ContactBloodGroup.A_POSITIVE.value,
        },
    )
    svc = _service(contacts_repo=repo)

    await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            gender=ContactGender.FEMALE,
            blood_group=ContactBloodGroup.A_POSITIVE,
            communication_preferences=CommunicationPreferences(email=False, sms=True),
            skills=["python"],
            description="Senior contact",
        ),
    )

    update_data = repo.last_update_kwargs["update_data"]
    assert update_data["gender"] == ContactGender.FEMALE.value
    assert update_data["blood_group"] == ContactBloodGroup.A_POSITIVE.value
    assert update_data["communication_preferences"]["email"] is False
    assert update_data["skills"] == ["python"]


@pytest.mark.asyncio
async def test_update_contact_sync_auth_phone(monkeypatch):
    """Update contact syncs auth phone when primary phone changes."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(
        user_id=USER_ID,
        phones=[{"phone_number": "111", "phone_isd_code": "+1", "is_primary": True}],
    )
    repo = _FakeContactsRepo(
        contact_for_update=current,
        updated_row={
            "phones": [{"phone_number": "222", "phone_isd_code": "+1", "is_primary": True}]
        },
    )
    svc = _service(contacts_repo=repo)
    mock_sync = AsyncMock()
    svc._sync_contact_auth_phone = mock_sync  # type: ignore[method-assign]

    await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(
            phones=[Phone(phone_number="222", phone_isd_code="+1", is_primary=True)]
        ),
    )

    mock_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_contact_emails(monkeypatch):
    """Update contact replaces emails jsonb list."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail()
    repo = _FakeContactsRepo(contact_for_update=current, echo_update=True)
    svc = _service(contacts_repo=repo)

    await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(emails=[Email(email="alt@example.com", is_primary=True)]),
    )

    emails = repo.last_update_kwargs["update_data"]["emails"]
    assert emails[0]["email"] == "alt@example.com"


@pytest.mark.asyncio
async def test_update_contact_notes_replacement(monkeypatch):
    """Update contact replaces notes when field is explicitly set."""
    _patch_custom_fields(monkeypatch)
    current = _contact_detail(notes=[{"title": "Old", "content": "Note"}])
    repo = _FakeContactsRepo(contact_for_update=current, echo_update=True)
    svc = _service(contacts_repo=repo)

    await svc.update_contact(
        contact_id=CONTACT_ID,
        body=UpdateContactRequest(notes=[]),
    )

    assert repo.last_update_kwargs["update_data"]["notes"] == []


def test_normalize_contact_audit_snapshot():
    """Normalize contact audit snapshot stringifies ids and json fields."""
    row = {
        "id": CONTACT_ID,
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "phones": '[{"phone_number": "111"}]',
        "tags": '["vip"]',
        "additional_data": '{"tier": "gold"}',
        "communication_preferences": '{"email": true}',
        "sales_intelligence": '{"score": 10}',
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "addresses": [{"id": "addr-1", "contact_id": CONTACT_ID, "country": "US"}],
    }
    normalized = ContactsService._normalize_contact_audit_snapshot(row)
    assert normalized is not None
    assert isinstance(normalized["phones"], list)
    assert normalized["additional_data"]["tier"] == "gold"
    assert normalized["id"] == CONTACT_ID


# ---------------------------------------------------------------------------
# company association delta coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_companies_delta_remove():
    """Apply companies delta removes associations."""
    current = {"id": CONTACT_ID}
    companies = [{"company_id": "co-2", "name": "Beta"}]
    cc_repo = _FakeContactCompaniesRepo(companies_snapshot=companies)
    svc = _service(
        contacts_repo=_FakeContactsRepo(contact_for_update=current),
        cc_repo=cc_repo,
    )
    delta = ContactCompanyUpdate(remove_associations=["co-1"])

    result = await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert cc_repo.last_delta_kwargs["remove_company_ids"] == ["co-1"]
    assert result["affected_company_ids"] == ["co-1"]
    assert result["companies"] == companies


@pytest.mark.asyncio
async def test_apply_companies_delta_update_unset_primary():
    """Apply companies delta forwards unset-primary company ids."""
    current = {"id": CONTACT_ID}
    cc_repo = _FakeContactCompaniesRepo()
    svc = _service(
        contacts_repo=_FakeContactsRepo(contact_for_update=current),
        cc_repo=cc_repo,
    )
    delta = ContactCompanyUpdate(
        update_associations=[
            ContactCompanyAssociationUpdate(company_id=COMPANY_ID, is_primary=False)
        ]
    )

    await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert cc_repo.last_delta_kwargs["unset_primary_company_ids"] == [COMPANY_ID]


@pytest.mark.asyncio
async def test_apply_companies_delta_set_primary_adds_membership():
    """Apply companies delta ensures primary companies are also added."""
    current = {"id": CONTACT_ID}
    cc_repo = _FakeContactCompaniesRepo()
    svc = _service(
        contacts_repo=_FakeContactsRepo(contact_for_update=current),
        cc_repo=cc_repo,
    )
    delta = ContactCompanyUpdate(
        update_associations=[
            ContactCompanyAssociationUpdate(company_id=COMPANY_ID, is_primary=True)
        ]
    )

    await svc.apply_companies_update_delta(contact_id=CONTACT_ID, delta=delta)

    assert COMPANY_ID in cc_repo.last_delta_kwargs["add_company_ids"]
    assert COMPANY_ID in cc_repo.last_delta_kwargs["set_primary_company_ids"]


@pytest.mark.asyncio
async def test_apply_companies_delta_fetches_snapshot_after_remove():
    """Apply companies delta fetches authoritative snapshot when changes occur."""
    current = {"id": CONTACT_ID}
    companies = [{"company_id": "co-2", "name": "Beta"}]
    cc_repo = _FakeContactCompaniesRepo(companies_snapshot=companies)
    svc = _service(
        contacts_repo=_FakeContactsRepo(contact_for_update=current),
        cc_repo=cc_repo,
    )

    result = await svc.apply_companies_update_delta(
        contact_id=CONTACT_ID,
        delta=ContactCompanyUpdate(remove_associations=["co-1"]),
    )

    assert result["companies"] == companies
    assert cc_repo.last_delta_kwargs["remove_company_ids"] == ["co-1"]


# ---------------------------------------------------------------------------
# add_phones_to_contact_if_missing coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_phones_empty_returns_false():
    """Add phones short-circuits when payload is empty."""
    repo = _FakeContactsRepo(contact_phones=[])
    svc = _service(contacts_repo=repo)

    result = await svc.add_phones_to_contact_if_missing(contact_id=CONTACT_ID, phones=[])

    assert result is False


@pytest.mark.asyncio
async def test_add_phones_appends_missing_numbers():
    """Add phones appends only numbers not already on the contact."""
    repo = _FakeContactsRepo(
        contact_phones=[{"phone_number": "111", "phone_isd_code": "+1", "is_primary": True}],
        echo_update=True,
    )
    svc = _service(contacts_repo=repo)

    result = await svc.add_phones_to_contact_if_missing(
        contact_id=CONTACT_ID,
        phones=[
            Phone(phone_number="111", phone_isd_code="+1"),
            Phone(phone_number="222", phone_isd_code="+1"),
        ],
    )

    assert result is True
    phones = repo.last_update_kwargs["update_data"]["phones"]
    assert "222" in str(phones)


@pytest.mark.asyncio
async def test_add_phones_contact_not_found():
    """Add phones raises when contact is missing."""
    repo = _FakeContactsRepo(contact_phones=None)
    svc = _service(contacts_repo=repo)

    with pytest.raises(NotFoundException):
        await svc.add_phones_to_contact_if_missing(
            contact_id=CONTACT_ID,
            phones=[Phone(phone_number="222", phone_isd_code="+1")],
        )


@pytest.mark.asyncio
async def test_add_phones_rejects_multiple_primary():
    """Add phones rejects merge when existing data has multiple primaries."""
    repo = _FakeContactsRepo(
        contact_phones=[
            {"phone_number": "111", "phone_isd_code": "+1", "is_primary": True},
            {"phone_number": "999", "phone_isd_code": "+1", "is_primary": True},
        ],
    )
    svc = _service(contacts_repo=repo)

    with pytest.raises(ValidationException):
        await svc.add_phones_to_contact_if_missing(
            contact_id=CONTACT_ID,
            phones=[Phone(phone_number="222", phone_isd_code="+1")],
        )


# ---------------------------------------------------------------------------
# search / lifecycle / scheduling coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_contacts_default_name_params():
    """Search contacts uses default name params for text queries."""
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=None)
    fake_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = fake_typesense

    await svc.search_contacts(query="Jane Doe", page=1, page_size=10, status=None)

    params = fake_typesense.search.await_args.args[0]
    assert params["q"] == "Jane Doe"
    assert "query_by" in params


@pytest.mark.asyncio
async def test_search_contacts_with_distance_threshold(monkeypatch):
    """Search contacts includes distance threshold in vector query when configured."""
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.shared_settings.typesense.vector_distance_threshold",
        0.35,
    )
    svc = _service()
    fake_typesense = MagicMock()
    fake_typesense.embed_query_text = AsyncMock(return_value=[0.5, 0.6])
    fake_typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = fake_typesense

    await svc.search_contacts(query="Jane", page=1, page_size=5, status=ClientStatus.ACTIVE.value)

    params = fake_typesense.search.await_args.args[0]
    assert "distance_threshold:0.35" in params["vector_query"]
    assert "status:=active" in params["filter_by"]


@pytest.mark.asyncio
async def test_get_contact_details_normalizes_json_strings():
    """Get contact details parses stringified json columns."""
    repo = _FakeContactsRepo(
        contact_details=_contact_detail(
            phones='[{"phone_number": "123", "phone_isd_code": "+1"}]',
            tags='["vip"]',
            additional_data='{"tier": "gold"}',
            communication_preferences='{"email": false}',
            sales_intelligence='{"score": 5}',
            companies=[{"company_id": COMPANY_ID, "name": "Acme"}],
        )
    )
    svc = _service(contacts_repo=repo)

    result = await svc.get_contact_details(contact_id=CONTACT_ID)

    assert isinstance(result["phones"], list)
    assert result["additional_data"]["tier"] == "gold"
    assert result["companies"][0]["name"] == "Acme"


def test_normalize_contact_list_row_parses_json():
    """Normalize list row parses string json for company names and phones."""
    row = _contact_list_row(company_names='["Acme"]', phones='[{"phone_number": "111"}]')
    ContactsService._normalize_contact_list_row(row)
    assert row["company_names"] == ["Acme"]
    phones = row["phones"]
    assert isinstance(phones, list)
    assert phones[0]["phone_number"] == "111"


def test_schedule_lifecycle_event_publishes(monkeypatch):
    """Schedule lifecycle publishes registers one background task per event."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.EventService.publish_event_background",
        MagicMock(),
    )

    ContactsService.schedule_lifecycle_event_publishes(
        background_tasks=bg,
        created_events=[
            ({"type": "created"}, CONTACT_ID),
            ({"type": "company_created"}, COMPANY_ID),
        ],
    )

    assert add_task.call_count == 2


def test_schedule_typesense_indexing_companies(monkeypatch):
    """Schedule Typesense indexing for created company entity."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_companies_background",
        MagicMock(),
    )

    ContactsService.schedule_typesense_indexing_for_created_entities(
        background_tasks=bg,
        created_entities=[
            {"entity_id": COMPANY_ID, "entity_table": "companies", "action": "create_company"}
        ],
        organization_id=ORG_ID,
    )

    add_task.assert_called_once()


@pytest.mark.asyncio
async def test_create_lifecycle_events_skips_empty_entity_id():
    """Lifecycle event creation skips rows without entity_id."""
    event_service = MagicMock()
    event_service.create_lifecycle_event = AsyncMock(return_value={"event_type": "created"})

    events = await ContactsService.create_lifecycle_events_for_created_entities(
        event_service=event_service,
        created_entities=[{"entity_table": "contacts", "action": "create"}],
        organization_id=ORG_ID,
        actor_user_id="admin-1",
    )

    assert events == []
    event_service.create_lifecycle_event.assert_not_awaited()


def test_schedule_enrichment_disabled(monkeypatch):
    """Schedule enrichment no-ops when enrichment is disabled."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.client_enrichment_enabled",
        lambda: False,
    )

    ContactsService.schedule_enrichment(
        background_tasks=bg,
        enrichment_targets=[{"client_id": CONTACT_ID, "organization_id": ORG_ID}],
    )

    add_task.assert_not_called()


def test_schedule_update_bg_tasks_related_events(monkeypatch):
    """Update background tasks publish related lifecycle events."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.EventService.publish_event_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_contacts_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.trigger_enrichment_background",
        AsyncMock(),
    )

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=bg,
        contact_id=CONTACT_ID,
        organization_id=ORG_ID,
        body=UpdateContactRequest(first_name="Janet"),
        update_result={"created_company_id": None},
        update_event={"type": "updated"},
        event_key=CONTACT_ID,
        event_topics=ContactsService.CLIENT_KAFKA_TOPICS,
        related_lifecycle_events=[({"type": "company_updated"}, COMPANY_ID)],
    )

    assert add_task.call_count >= 3


def test_schedule_update_bg_tasks_company_enrichment(monkeypatch):
    """Update background tasks enrich newly created company."""
    bg = BackgroundTasks()
    add_task = MagicMock()
    monkeypatch.setattr(bg, "add_task", add_task)
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.EventService.publish_event_background",
        MagicMock(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.index_contacts_background",
        MagicMock(),
    )
    mock_service = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ClientEnrichmentService.from_settings",
        lambda: mock_service,
    )

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=bg,
        contact_id=CONTACT_ID,
        organization_id=ORG_ID,
        body=UpdateContactRequest(
            company_association=ContactCompanyUpdate(
                create_and_associate=ContactCompanyAssociationCreate(name="NewCo")
            )
        ),
        update_result={
            "created_company_id": COMPANY_ID,
            "companies_delta": {"affected_company_ids": [COMPANY_ID]},
        },
        update_event={"type": "updated"},
        event_key=CONTACT_ID,
        event_topics=ContactsService.CLIENT_KAFKA_TOPICS,
    )

    enrichment_calls = [
        call
        for call in add_task.call_args_list
        if call.args[0] == mock_service.run_client_enrichment
    ]
    assert len(enrichment_calls) == 1


@pytest.mark.asyncio
async def test_trigger_enrichment_contact_not_found():
    """Trigger enrichment raises when contact details are missing."""
    svc = _service(contacts_repo=_FakeContactsRepo(contact_details=None))

    with pytest.raises(NotFoundException):
        await svc.trigger_enrichment(contact_id=CONTACT_ID, organization_id=ORG_ID)


def _property_contact_body(**overrides) -> CreateContactRequest:
    """Build a property-management create request with one primary phone."""
    payload = {
        "contact_type": ContactType.OWNER,
        "first_name": "Jane",
        "last_name": "Doe",
        "phones": [Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        "emails": [Email(email="jane@example.com", is_primary=True)],
        "communication_preferences": CommunicationPreferences(),
    }
    payload.update(overrides)
    return CreateContactRequest(**payload)


@pytest.mark.asyncio
async def test_create_property_contact_success(monkeypatch):
    """Property contact create provisions auth and inserts contact row."""
    repo = _FakeContactsRepo()
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    svc.contacts_repo = repo  # type: ignore[assignment]
    svc.org_repo = _FakeOrgRepo(organization={"id": ORG_ID, "settings": "{}"})  # type: ignore[assignment]
    monkeypatch.setattr(
        svc,
        "_validate_custom_fields_for_create",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_user",
        AsyncMock(return_value={"id": USER_ID}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_isometrik_user",
        AsyncMock(return_value={"userId": "iso-new"}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.generate_random_password",
        lambda: "TempPass@123",
    )
    mock_user_repo = MagicMock()
    mock_user_repo.get_auth_users_by_phone_or_email = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection: mock_user_repo,
    )

    result = await svc._create_property_contact(_property_contact_body(), provision_auth=True)

    assert result["contact_id"]
    assert result["new_data"]["first_name"] == "Jane"
    assert repo.last_insert_contact["user_id"] == USER_ID


@pytest.mark.asyncio
async def test_create_property_contact_without_auth():
    """Property contact create skips auth when provision_auth is false."""
    repo = _FakeContactsRepo()
    svc = _service(contacts_repo=repo)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        svc,
        "_validate_custom_fields_for_create",
        AsyncMock(return_value=[]),
    )
    try:
        result = await svc._create_property_contact(
            _property_contact_body(),
            provision_auth=False,
        )
        assert result["new_data"]["user_id"] is None
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_create_property_contact_missing_type():
    """Property contact create requires contact_type."""
    svc = _service()
    with pytest.raises(ValidationException):
        await svc._create_property_contact(
            CreateContactRequest(
                first_name="Jane",
                phones=[Phone(phone_number="1", phone_isd_code="+1", is_primary=True)],
            ),
            provision_auth=False,
        )


@pytest.mark.asyncio
async def test_create_property_contact_missing_primary_phone():
    """Property contact create requires exactly one primary phone."""
    svc = _service()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        svc,
        "_validate_custom_fields_for_create",
        AsyncMock(return_value=[]),
    )
    try:
        with pytest.raises(ValidationException):
            await svc._create_property_contact(
                CreateContactRequest(
                    contact_type=ContactType.OWNER,
                    first_name="Jane",
                    phones=[],
                ),
                provision_auth=False,
            )
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_create_property_contact_no_supabase():
    """Property contact create with auth requires Supabase client."""
    svc = _service()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        svc,
        "_validate_custom_fields_for_create",
        AsyncMock(return_value=[]),
    )
    try:
        with pytest.raises(ServiceUnavailableException):
            await svc._create_property_contact(_property_contact_body(), provision_auth=True)
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_create_property_contact_user_org_conflict():
    """Property contact insert maps uq_contacts_user_org to ConflictException."""
    repo = _FakeContactsRepo()
    repo.insert_contact = AsyncMock(  # type: ignore[method-assign]
        side_effect=_unique_violation("uq_contacts_user_org"),
    )
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    svc.contacts_repo = repo  # type: ignore[assignment]
    svc.org_repo = _FakeOrgRepo(organization={"id": ORG_ID, "settings": "{}"})  # type: ignore[assignment]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(svc, "_validate_custom_fields_for_create", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_user",
        AsyncMock(return_value={"id": USER_ID}),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_isometrik_user",
        AsyncMock(return_value={"userId": "iso-new"}),
    )
    mock_user_repo = MagicMock()
    mock_user_repo.get_auth_users_by_phone_or_email = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection: mock_user_repo,
    )
    try:
        with pytest.raises(ConflictException):
            await svc._create_property_contact(_property_contact_body(), provision_auth=True)
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_trigger_enrichment_background(monkeypatch):
    """Background enrichment acquires pool connection and delegates to service."""
    mock_conn = MagicMock()
    mock_service = MagicMock()
    mock_service.trigger_enrichment = AsyncMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.get_pool",
        AsyncMock(return_value=MagicMock()),
    )

    class _Acquire:
        """Async context manager stub for AcquireConnection."""

        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.AcquireConnection",
        lambda _pool: _Acquire(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService",
        lambda **kwargs: mock_service,
    )

    await ContactsService.trigger_enrichment_background(CONTACT_ID, ORG_ID)

    mock_service.trigger_enrichment.assert_awaited_once_with(
        contact_id=CONTACT_ID,
        organization_id=ORG_ID,
        conn=mock_conn,
    )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.UserRepository",
)
@patch(
    "apps.user_service.app.services.contacts_service.create_user",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.services.contacts_service.create_isometrik_user",
    new_callable=AsyncMock,
)
async def test_provision_contact_auth_identity_creates_user(
    mock_create_iso: AsyncMock,
    mock_create_user: AsyncMock,
    mock_user_repo_cls: MagicMock,
):
    """Auth identity provisioning creates Supabase user when no match exists."""
    mock_user_repo_cls.return_value.get_auth_users_by_phone_or_email = AsyncMock(return_value=[])
    mock_create_user.return_value = {"id": "auth-new"}
    mock_create_iso.return_value = {"userId": "iso-new"}
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    svc.org_repo = _FakeOrgRepo(organization={"id": ORG_ID, "settings": "{}"})  # type: ignore[assignment]

    user_id, iso_id, password = await svc._provision_contact_auth_identity(
        contact_id=CONTACT_ID,
        phone="+919876543210",
        email="jane@example.com",
        first_name="Jane",
        last_name="Doe",
        prefix=None,
        password="Secret@123",
    )

    assert user_id == "auth-new"
    assert iso_id == "iso-new"
    assert password == "Secret@123"


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.UserRepository",
)
@patch(
    "apps.user_service.app.services.contacts_service.create_isometrik_user",
    new_callable=AsyncMock,
)
async def test_provision_contact_auth_identity_reuses_existing(
    mock_create_iso: AsyncMock,
    mock_user_repo_cls: MagicMock,
):
    """Auth identity provisioning reuses an existing auth user match."""
    mock_user_repo_cls.return_value.get_auth_users_by_phone_or_email = AsyncMock(
        return_value=[{"id": "auth-existing"}],
    )
    mock_create_iso.return_value = {"userId": "iso-reuse"}
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    svc.org_repo = _FakeOrgRepo(organization={"id": ORG_ID, "settings": "{}"})  # type: ignore[assignment]

    user_id, iso_id, password = await svc._provision_contact_auth_identity(
        contact_id=CONTACT_ID,
        phone="+919876543210",
        email="jane@example.com",
        first_name="Jane",
        last_name="Doe",
        prefix=None,
    )

    assert user_id == "auth-existing"
    assert iso_id == "iso-reuse"
    assert password is None
    mock_create_iso.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.UserRepository",
)
async def test_provision_contact_auth_identity_auth_mismatch(
    mock_user_repo_cls: MagicMock,
):
    """Conflicting auth matches raise ConflictException."""
    mock_user_repo_cls.return_value.get_auth_users_by_phone_or_email = AsyncMock(
        return_value=[{"id": "u1"}, {"id": "u2"}],
    )
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )
    svc.org_repo = _FakeOrgRepo(organization={"id": ORG_ID, "settings": "{}"})  # type: ignore[assignment]

    with pytest.raises(ConflictException):
        await svc._provision_contact_auth_identity(
            contact_id=CONTACT_ID,
            phone="+919876543210",
            email="jane@example.com",
            first_name="Jane",
            last_name="Doe",
            prefix=None,
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.UserRepository",
)
@patch(
    "apps.user_service.app.services.contacts_service.get_user_by_id",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.services.contacts_service.update_phone",
    new_callable=AsyncMock,
)
async def test_sync_contact_auth_phone_success(
    mock_update_phone: AsyncMock,
    mock_get_user: AsyncMock,
    mock_user_repo_cls: MagicMock,
):
    """Sync auth phone updates Supabase when phone is free."""
    mock_user_repo_cls.return_value.get_auth_user_by_phone = AsyncMock(return_value=None)
    mock_get_user.return_value = {"user_metadata": {}}
    mock_update_phone.return_value = True
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )

    await svc._sync_contact_auth_phone(
        user_id=USER_ID,
        phone=Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True),
    )

    mock_update_phone.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contacts_service.UserRepository",
)
async def test_sync_contact_auth_phone_conflict(mock_user_repo_cls: MagicMock):
    """Sync auth phone rejects phone owned by another user."""
    mock_user_repo_cls.return_value.get_auth_user_by_phone = AsyncMock(return_value={"id": "other"})
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
    )

    with pytest.raises(ConflictException):
        await svc._sync_contact_auth_phone(
            user_id=USER_ID,
            phone=Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True),
        )
