"""Unit tests for CompaniesService public methods."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.common import AddressesUpdate, AddressUpdateItem
from apps.user_service.app.schemas.companies import (
    CompanyContactAssociationAdd,
    CompanyContactAssociationCreate,
    CompanyContactAssociationUpdate,
    CompanyContactsCreate,
    CompanyContactUpdate,
    CompanyLeadAssociation,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    CompanyEventType,
    ContactEventType,
)
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
COMPANY_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"


def _ctx() -> UserContext:
    """Build user context for company tests."""
    return UserContext(user_id="admin-1", email="admin@example.com", organization_id=ORG_ID)


class _FakeCompaniesRepo:
    """Configurable fake CompaniesRepository."""

    def __init__(
        self,
        *,
        companies: list[dict[str, Any]] | None = None,
        total: int | None = None,
        details: dict[str, Any] | None = None,
        created: dict[str, Any] | None = None,
        for_update: dict[str, Any] | None = None,
        updated: dict[str, Any] | None = None,
    ) -> None:
        self.companies = companies or []
        self.total = total if total is not None else len(self.companies)
        self.details = details
        self.created = created
        self.for_update = for_update
        self.updated = updated
        self.last_list_kwargs: dict[str, Any] | None = None
        self.last_create_kwargs: dict[str, Any] | None = None
        self.last_update_kwargs: dict[str, Any] | None = None
        self.deleted_address_ids: list[str] | None = None

    async def list_companies(self, **kwargs):
        """Return paginated companies."""
        self.last_list_kwargs = kwargs
        return self.companies, self.total

    async def get_company_details(self, *, company_id: str, organization_id: str):
        """Return company details row."""
        del company_id, organization_id
        return self.details

    async def get_company_for_update(self, *, company_id: str, organization_id: str):
        """Return locked company row for update flows."""
        del company_id, organization_id
        return self.for_update

    async def update_company(self, **kwargs):
        """Record update payload and return configured row."""
        self.last_update_kwargs = kwargs
        return self.updated

    async def delete_company_addresses(self, *, company_id: str, address_ids: list[str]):
        """Record deleted address ids."""
        del company_id
        self.deleted_address_ids = list(address_ids)

    async def update_company_address(self, **kwargs):
        """Return None unless overridden in tests."""
        del kwargs
        return None

    async def create_company_addresses(self, rows):
        """Return inserted address rows unchanged."""
        return rows

    async def create_company_with_optional_contact_link(self, **kwargs):
        """Record create payload and return configured result."""
        self.last_create_kwargs = kwargs
        return self.created or {}


class _FakeContactsRepo:
    """Configurable fake ContactsRepository."""

    def __init__(self, *, found_ids: set[str] | None = None) -> None:
        self.found_ids = found_ids or set()
        self.last_filter_kwargs: dict[str, Any] | None = None

    async def filter_contact_ids_in_organization(self, **kwargs):
        """Return configured contact ids present in org."""
        self.last_filter_kwargs = kwargs
        return self.found_ids


class _FakeContactCompaniesRepo:
    """Configurable fake ContactCompaniesRepository."""

    def __init__(self) -> None:
        self.last_delta_kwargs: dict[str, Any] | None = None
        self.snapshot: list[dict[str, Any]] = []

    async def apply_contacts_update_delta(self, **kwargs):
        """Record delta application kwargs."""
        self.last_delta_kwargs = kwargs

    async def get_company_contacts_snapshot(self, **kwargs):
        """Return configured contacts snapshot."""
        del kwargs
        return self.snapshot


def _company_row(**overrides) -> dict[str, Any]:
    """Build a company list/detail row."""
    row = {
        "id": COMPANY_ID,
        "organization_id": ORG_ID,
        "name": "Acme Corp",
        "industry": "Tech",
        "status": ClientStatus.ACTIVE.value,
        "email": "info@acme.com",
        "phones": "[]",
        "contacts": [],
        "tags": [],
        "websites": [],
        "social_pages": [],
        "linked_pages": [],
        "products": [],
        "key_people": [],
        "custom_fields": [],
        "target_market_segments": [],
        "current_tech_stack": [],
        "preferred_communication_channels": [],
        "industry_specific_terminologies": [],
        "notes": [],
        "leads": [],
        "addresses": [],
        "billing_preferences": {},
        "additional_data": {},
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _service(
    *,
    companies_repo: _FakeCompaniesRepo | None = None,
    contacts_repo: _FakeContactsRepo | None = None,
    cc_repo: _FakeContactCompaniesRepo | None = None,
) -> CompaniesService:
    """Build CompaniesService with fake repositories."""
    svc = CompaniesService(db_connection=MagicMock(), user_context=_ctx())
    svc.companies_repo = companies_repo or _FakeCompaniesRepo()
    svc.contacts_repo = contacts_repo or _FakeContactsRepo()
    svc.cc_repo = cc_repo or _FakeContactCompaniesRepo()
    return svc


def _patch_custom_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch CustomFieldService for company list/create flows."""

    class _FakeCFS:
        """Minimal CustomFieldService fake."""

        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def validate_dropdown_filters_for_entity(self, entity_type, parsed_filters):
            """No-op dropdown validation."""
            del entity_type, parsed_filters

        async def validate_for_create(self, custom_fields, entity_type):
            """Return custom fields unchanged."""
            del entity_type
            return list(custom_fields or [])

        async def merge_for_update(self, patch, existing, entity_type):
            """Return existing list unchanged."""
            del patch, entity_type
            return list(existing or [])

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CustomFieldService",
        _FakeCFS,
    )


@pytest.mark.asyncio
async def test_list_companies_returns_items(monkeypatch):
    """List companies forwards org filters and normalizes rows."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeCompaniesRepo(companies=[_company_row()], total=1)
    svc = _service(companies_repo=repo)

    result = await svc.list_companies(
        search="acme",
        status=ClientStatus.ACTIVE.value,
        dropdown_filters=None,
        page=1,
        page_size=20,
    )

    assert result["total"] == 1
    assert result["items"][0]["name"] == "Acme Corp"
    assert repo.last_list_kwargs["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_get_company_details_found():
    """Get company details normalizes nested JSON fields."""
    repo = _FakeCompaniesRepo(details=_company_row())
    svc = _service(companies_repo=repo)

    result = await svc.get_company_details(company_id=COMPANY_ID)

    assert result["id"] == COMPANY_ID
    assert result["organization_id"] == ORG_ID
    assert result["phones"] == []
    assert result["created_at"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_get_company_details_not_found():
    """Missing company raises NotFoundException."""
    svc = _service(companies_repo=_FakeCompaniesRepo(details=None))
    with pytest.raises(NotFoundException):
        await svc.get_company_details(company_id=COMPANY_ID)


@pytest.mark.asyncio
async def test_create_company_only(monkeypatch):
    """Create company inserts row and returns lifecycle payload."""
    _patch_custom_fields(monkeypatch)
    company = _company_row()
    repo = _FakeCompaniesRepo(
        created={
            "company_id": COMPANY_ID,
            "company": company,
            "contact": None,
            "contact_id": None,
            "contact_found": False,
        }
    )
    svc = _service(companies_repo=repo)

    result = await svc.create_company(CreateCompanyRequest(name="Acme Corp"))

    assert result["company_id"] == COMPANY_ID
    assert result["new_data"]["name"] == "Acme Corp"
    assert result["created_entities"][0]["entity_table"] == "companies"
    assert repo.last_create_kwargs["organization_id"] == ORG_ID


@pytest.mark.asyncio
async def test_create_company_with_contact_id(monkeypatch):
    """Create company links existing contact when requested."""
    _patch_custom_fields(monkeypatch)
    contact_id = "770e8400-e29b-41d4-a716-446655440002"
    company = _company_row()
    repo = _FakeCompaniesRepo(
        created={
            "company_id": COMPANY_ID,
            "company": company,
            "contact": {"id": contact_id, "email": "jane@example.com"},
            "contact_id": contact_id,
            "contact_found": True,
        }
    )
    svc = _service(companies_repo=repo)
    body = CreateCompanyRequest(
        name="Acme Corp",
        contact_association=CompanyContactsCreate(
            add_association=CompanyContactAssociationAdd(
                contact_id=contact_id,
                is_primary=True,
            )
        ),
    )

    result = await svc.create_company(body)

    assert result["company_id"] == COMPANY_ID
    assert repo.last_create_kwargs["contact_id"] == contact_id
    assert repo.last_create_kwargs["set_primary"] is True


def test_typesense_hits_to_company_rows():
    """Static mapper converts Typesense company hits."""
    hits = [
        {
            "document": {
                "id": COMPANY_ID,
                "organization_id": ORG_ID,
                "status": ClientStatus.ACTIVE.value,
                "name": "Acme Corp",
                "email": "info@acme.com",
                "phones_display": [],
                "contacts": [],
                "created_at": 1735689600,
                "updated_at": 1735776000,
            }
        }
    ]
    rows = CompaniesService.typesense_hits_to_company_summary_rows(hits)
    assert rows[0]["name"] == "Acme Corp"


def test_ensure_list_item_ids_adds_uuid():
    """List items without ids receive generated ids."""
    items = [{"label": "site"}]
    out = CompaniesService._ensure_list_item_ids(items)
    assert out[0]["label"] == "site"
    assert out[0]["id"]


def test_created_entity_lifecycle_mapping():
    """Created entity rows map to contact or company events."""
    contact_type, module = CompaniesService._created_entity_lifecycle_type_and_module(
        {"entity_table": "contacts"}
    )
    assert contact_type == ContactEventType.CREATED.value
    assert module == "contacts"

    company_type, company_module = CompaniesService._created_entity_lifecycle_type_and_module(
        {"entity_table": "companies"}
    )
    assert company_type == CompanyEventType.CREATED.value
    assert company_module == "companies"


@pytest.mark.asyncio
async def test_update_company_scalar_fields(monkeypatch):
    """Update company persists scalar changes and audit snapshot."""
    _patch_custom_fields(monkeypatch)
    current = _company_row(contacts=[], addresses=[])
    repo = _FakeCompaniesRepo(
        for_update=current,
        updated={**current, "name": "Renamed Corp", "industry": "Finance"},
    )
    svc = _service(companies_repo=repo)

    result = await svc.update_company(
        company_id=COMPANY_ID,
        body=UpdateCompanyRequest(name="Renamed Corp", industry="Finance"),
    )

    assert result["ok"] is True
    assert result["old_data"]["name"] == "Acme Corp"
    assert result["new_data"]["name"] == "Renamed Corp"
    assert repo.last_update_kwargs["company_id"] == COMPANY_ID
    assert repo.last_update_kwargs["update_data"]["name"] == "Renamed Corp"


@pytest.mark.asyncio
async def test_update_company_not_found():
    """Update missing company raises NotFoundException."""
    svc = _service(companies_repo=_FakeCompaniesRepo(for_update=None))
    with pytest.raises(NotFoundException):
        await svc.update_company(
            company_id=COMPANY_ID,
            body=UpdateCompanyRequest(name="Ghost"),
        )


@pytest.mark.asyncio
async def test_apply_contacts_delta_add_primary():
    """Apply delta validates ids and sets primary contact."""
    cc_repo = _FakeContactCompaniesRepo()
    cc_repo.snapshot = [{"id": CONTACT_ID, "is_primary": True}]
    contacts_repo = _FakeContactsRepo(found_ids={CONTACT_ID})
    svc = _service(contacts_repo=contacts_repo, cc_repo=cc_repo)
    delta = CompanyContactUpdate(
        add_associations=[
            CompanyContactAssociationAdd(contact_id=CONTACT_ID, is_primary=True),
        ]
    )

    result = await svc.apply_contacts_update_delta(
        company_id=COMPANY_ID,
        delta=delta,
    )

    assert result["ok"] is True
    assert CONTACT_ID in result["affected_contact_ids"]
    assert cc_repo.last_delta_kwargs["set_primary_contact_id"] == CONTACT_ID
    assert CONTACT_ID in cc_repo.last_delta_kwargs["add_contact_ids"]


@pytest.mark.asyncio
async def test_apply_contacts_delta_missing_contact():
    """Apply delta rejects contact ids missing from organization."""
    svc = _service(contacts_repo=_FakeContactsRepo(found_ids=set()))
    delta = CompanyContactUpdate(
        add_associations=[
            CompanyContactAssociationAdd(contact_id=CONTACT_ID, is_primary=False),
        ]
    )

    with pytest.raises(NotFoundException):
        await svc.apply_contacts_update_delta(
            company_id=COMPANY_ID,
            delta=delta,
        )


@pytest.mark.asyncio
async def test_apply_contacts_delta_remove_ids():
    """Apply delta forwards remove ids to contact_companies repo."""
    cc_repo = _FakeContactCompaniesRepo()
    contacts_repo = _FakeContactsRepo(found_ids={CONTACT_ID})
    svc = _service(contacts_repo=contacts_repo, cc_repo=cc_repo)
    delta = CompanyContactUpdate(remove_associations=[CONTACT_ID])

    result = await svc.apply_contacts_update_delta(
        company_id=COMPANY_ID,
        delta=delta,
    )

    assert result["ok"] is True
    assert cc_repo.last_delta_kwargs["remove_contact_ids"] == [CONTACT_ID]
    assert CONTACT_ID in result["affected_contact_ids"]


@pytest.mark.asyncio
async def test_apply_contacts_delta_multi_primary():
    """Apply delta rejects multiple primary contacts in one request."""
    contacts_repo = _FakeContactsRepo(
        found_ids={CONTACT_ID, "880e8400-e29b-41d4-a716-446655440003"}
    )
    svc = _service(contacts_repo=contacts_repo)
    other_id = "880e8400-e29b-41d4-a716-446655440003"
    delta = CompanyContactUpdate(
        add_associations=[
            CompanyContactAssociationAdd(contact_id=CONTACT_ID, is_primary=True),
            CompanyContactAssociationAdd(contact_id=other_id, is_primary=True),
        ]
    )

    with pytest.raises(ValidationException):
        await svc.apply_contacts_update_delta(
            company_id=COMPANY_ID,
            delta=delta,
        )


@pytest.mark.asyncio
async def test_soft_delete_company_success():
    """soft_delete_company marks company deleted and returns audit snapshot."""
    current = _company_row()
    updated = {**current, "status": ClientStatus.DELETED.value}
    repo = _FakeCompaniesRepo(for_update=current, updated=updated)
    svc = _service(companies_repo=repo)

    result = await svc.soft_delete_company(company_id=COMPANY_ID)

    assert result["ok"] is True
    assert result["old_data"]["name"] == "Acme Corp"
    assert result["new_data"]["status"] == ClientStatus.DELETED.value


@pytest.mark.asyncio
async def test_soft_delete_company_not_found():
    """soft_delete_company raises when company missing."""
    svc = _service(companies_repo=_FakeCompaniesRepo(for_update=None))
    with pytest.raises(NotFoundException):
        await svc.soft_delete_company(company_id=COMPANY_ID)


@pytest.mark.asyncio
async def test_search_companies():
    """search_companies queries Typesense and maps hits."""
    svc = _service()
    typesense = MagicMock()
    typesense.embed_query_text = AsyncMock(return_value=None)
    typesense.search = AsyncMock(
        return_value={
            "hits": [
                {
                    "document": {
                        "id": COMPANY_ID,
                        "organization_id": ORG_ID,
                        "status": ClientStatus.ACTIVE.value,
                        "name": "Acme Corp",
                        "created_at": 1735689600,
                        "updated_at": 1735776000,
                    }
                }
            ],
            "found": 1,
        }
    )
    svc._typesense = typesense

    result = await svc.search_companies(query="acme", page=1, page_size=20, status=None)

    assert result["total"] == 1
    assert result["items"][0]["name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_create_lifecycle_events_for_created_entities():
    """create_lifecycle_events_for_created_entities builds lifecycle rows."""
    event_service = MagicMock()
    event_service.create_lifecycle_event = AsyncMock(
        return_value={"event_id": "evt-1"},
    )

    created = await CompaniesService.create_lifecycle_events_for_created_entities(
        event_service=event_service,
        created_entities=[
            {"entity_id": COMPANY_ID, "entity_table": "companies", "action": "create"},
            {"entity_id": CONTACT_ID, "entity_table": "contacts", "action": "create_contact"},
        ],
        organization_id=ORG_ID,
        actor_user_id="admin-1",
    )

    assert len(created) == 2
    assert created[0][1] == COMPANY_ID


def test_normalize_company_list_row():
    """_normalize_company_list_row coerces JSON list fields on list rows."""
    row = _company_row(phones="[]", contacts="[]")
    CompaniesService._normalize_company_list_row(row)
    assert row["phones"] == []
    assert row["contacts"] == []


def test_normalize_company_audit_snapshot():
    """_normalize_company_audit_snapshot formats audit payloads."""
    snapshot = CompaniesService._normalize_company_audit_snapshot(_company_row())
    assert snapshot is not None
    assert snapshot["id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_update_company_with_addresses(monkeypatch):
    """Update company applies address delta operations."""
    _patch_custom_fields(monkeypatch)
    address_id = "990e8400-e29b-41d4-a716-446655440004"
    current = _company_row(
        contacts=[],
        addresses=[{"id": address_id, "city": "Old City", "company_id": COMPANY_ID}],
    )
    repo = _FakeCompaniesRepo(
        for_update=current,
        updated={**current, "name": "Renamed Corp"},
    )
    repo.update_company_address = AsyncMock(
        return_value={"id": address_id, "city": "New City", "company_id": COMPANY_ID},
    )
    svc = _service(companies_repo=repo)

    body = UpdateCompanyRequest(
        name="Renamed Corp",
        addresses=AddressesUpdate(
            update=[AddressUpdateItem(id=address_id, city="New City")],
        ),
    )

    result = await svc.update_company(company_id=COMPANY_ID, body=body)

    assert result["ok"] is True
    assert repo.deleted_address_ids is None or repo.deleted_address_ids == []


def test_schedule_lifecycle_event_publishes():
    """schedule_lifecycle_event_publishes registers background publish tasks."""
    background_tasks = MagicMock()
    CompaniesService.schedule_lifecycle_event_publishes(
        background_tasks=background_tasks,
        created_events=[({"event_id": "evt-1"}, COMPANY_ID)],
    )
    background_tasks.add_task.assert_called_once()


# ---------------------------------------------------------------------------
# Module-level normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_notes_for_detail_filters_invalid():
    """Notes without title/content are dropped."""
    from apps.user_service.app.services.companies_service import (
        _normalize_notes_for_detail,
    )

    result = _normalize_notes_for_detail(
        [
            {"title": "  T  ", "content": "  C  "},
            {"title": "", "content": "x"},
            "not-a-dict",
        ]
    )
    assert result == [{"title": "T", "content": "C"}]


def test_normalize_company_detail_helpers():
    """Detail normalization helpers coerce JSON strings and UUIDs."""
    from apps.user_service.app.services import companies_service as mod

    details = _company_row(
        billing_preferences='{"currency": "USD"}',
        additional_data='{"tier": "enterprise"}',
        sales_intelligence='{"score": 90}',
        notes=[{"title": "Note", "content": "Body"}],
        contacts=[{"id": CONTACT_ID, "phones": "[]"}],
    )
    mod._stringify_company_detail_uuids(details)
    mod._coerce_company_detail_json_lists(details)
    mod._normalize_company_billing_preferences(details)
    mod._normalize_company_additional_data(details)
    mod._normalize_company_sales_intelligence(details)
    mod._normalize_company_detail_contacts(details)
    mod._normalize_company_detail_timestamps(details)

    assert details["billing_preferences"] == {"currency": "USD"}
    assert details["additional_data"] == {"tier": "enterprise"}
    assert details["sales_intelligence"] == {"score": 90}
    assert details["contacts"][0]["phones"] == []
    assert isinstance(details["id"], str)


def test_merge_primary_contact_id():
    """Only one primary contact id is allowed when merging."""
    assert CompaniesService._merge_primary_contact_id(None, CONTACT_ID) == CONTACT_ID
    assert CompaniesService._merge_primary_contact_id(CONTACT_ID, CONTACT_ID) == CONTACT_ID
    with pytest.raises(ValidationException):
        CompaniesService._merge_primary_contact_id(
            CONTACT_ID, "880e8400-e29b-41d4-a716-446655440003"
        )


def test_parse_company_contacts_update_delta():
    """Contact delta parser splits remove/add/unset/primary lists."""
    other_id = "880e8400-e29b-41d4-a716-446655440003"
    delta = CompanyContactUpdate(
        remove_associations=[CONTACT_ID],
        add_associations=[
            CompanyContactAssociationAdd(contact_id=other_id, is_primary=True),
        ],
        update_associations=[],
    )
    remove_ids, add_ids, unset_ids, primary = CompaniesService._parse_company_contacts_update_delta(
        delta
    )
    assert remove_ids == [CONTACT_ID]
    assert other_id in add_ids
    assert primary == other_id
    assert unset_ids == []


# ---------------------------------------------------------------------------
# Background scheduling helpers
# ---------------------------------------------------------------------------


def test_schedule_typesense_indexing_for_created_entities():
    """Typesense indexing schedules company and created contact rows."""
    background_tasks = MagicMock()
    CompaniesService.schedule_typesense_indexing_for_created_entities(
        background_tasks=background_tasks,
        company_id=COMPANY_ID,
        created_entities=[
            {"entity_table": "contacts", "action": "create_contact", "entity_id": CONTACT_ID},
        ],
        organization_id=ORG_ID,
    )
    assert background_tasks.add_task.call_count == 2


def test_schedule_enrichment_disabled(monkeypatch: pytest.MonkeyPatch):
    """Enrichment scheduling is skipped when feature flag is off."""
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.client_enrichment_enabled",
        lambda: False,
    )
    background_tasks = MagicMock()
    CompaniesService.schedule_enrichment(
        background_tasks=background_tasks,
        enrichment_targets=[{"client_id": COMPANY_ID}],
    )
    background_tasks.add_task.assert_not_called()


def test_schedule_company_update_background_tasks(monkeypatch: pytest.MonkeyPatch):
    """Company update schedules events, indexing, and enrichment."""
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.client_enrichment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.ClientEnrichmentService.from_settings",
        lambda: MagicMock(run_client_enrichment=MagicMock()),
    )
    background_tasks = MagicMock()
    body = UpdateCompanyRequest(name="Renamed", industry="Finance")
    CompaniesService.schedule_company_update_background_tasks(
        background_tasks=background_tasks,
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
        body=body,
        update_result={"contacts_delta": {"affected_contact_ids": [CONTACT_ID]}},
        update_event={"event_id": "evt-1"},
        event_key=COMPANY_ID,
        event_topics=CompaniesService.CLIENT_KAFKA_TOPICS,
    )
    assert background_tasks.add_task.call_count >= 2


# ---------------------------------------------------------------------------
# Create company — extended flows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_company_contact_not_found(monkeypatch: pytest.MonkeyPatch):
    """Linking a missing contact raises NotFoundException."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeCompaniesRepo(
        created={
            "company_id": COMPANY_ID,
            "company": _company_row(),
            "contact": None,
            "contact_id": None,
            "contact_found": False,
        }
    )
    svc = _service(companies_repo=repo)
    body = CreateCompanyRequest(
        name="Acme Corp",
        contact_association=CompanyContactsCreate(
            add_association=CompanyContactAssociationAdd(
                contact_id=CONTACT_ID,
                is_primary=False,
            )
        ),
    )
    with pytest.raises(NotFoundException):
        await svc.create_company(body)


@pytest.mark.asyncio
async def test_create_company_unique_violation_contact_user(monkeypatch: pytest.MonkeyPatch):
    """UniqueViolation on contact user maps to ConflictException."""
    from asyncpg import UniqueViolationError

    _patch_custom_fields(monkeypatch)
    repo = _FakeCompaniesRepo()

    async def _raise_unique(**kwargs):
        del kwargs
        exc = UniqueViolationError("duplicate contact user")
        exc.constraint_name = "uq_contacts_user_org"
        raise exc

    repo.create_company_with_optional_contact_link = _raise_unique
    svc = _service(companies_repo=repo)

    with pytest.raises(ConflictException):
        await svc.create_company(CreateCompanyRequest(name="Acme Corp"))


@pytest.mark.asyncio
async def test_create_company_with_lead(monkeypatch: pytest.MonkeyPatch):
    """Create company optionally creates an associated lead."""
    _patch_custom_fields(monkeypatch)
    company = _company_row()
    repo = _FakeCompaniesRepo(
        created={
            "company_id": COMPANY_ID,
            "company": company,
            "contact": None,
            "contact_id": None,
            "contact_found": False,
        }
    )
    svc = _service(companies_repo=repo)

    class _FakeLeadService:
        """Minimal LeadService fake."""

        def __init__(self, **kwargs):
            del kwargs

        async def create_lead(self, _body):
            return {"id": "lead-1"}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.LeadService",
        _FakeLeadService,
    )

    body = CreateCompanyRequest(
        name="Acme Corp",
        lead=CompanyLeadAssociation(
            stage_id="550e8400-e29b-41d4-a716-446655440099",
            intake_stage="web",
        ),
    )
    result = await svc.create_company(body)
    assert result["created_lead_id"] == "lead-1"


@pytest.mark.asyncio
async def test_create_company_sends_portal_welcome_email(monkeypatch: pytest.MonkeyPatch):
    """Portal access triggers welcome email for created contact."""
    svc = _service()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.send_client_creation_email",
        lambda **kwargs: sent.append(kwargs),
    )

    svc._maybe_send_portal_welcome_email(
        portal_access=True,
        created_contact_row={"email": "jane@example.com"},
        created_contact_password="TempPass1!",
    )

    assert sent[0]["email"] == "jane@example.com"
    assert sent[0]["password"] == "TempPass1!"


@pytest.mark.asyncio
async def test_build_create_company_enrichment_targets_with_contact():
    """Enrichment targets include person payload when contact was created."""
    svc = _service()
    body = CreateCompanyRequest(name="Acme Corp")
    targets = svc._build_create_company_enrichment_targets(
        body=body,
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
        websites_payload=[],
        social_pages_payload=[],
        created_contact_row={
            "id": CONTACT_ID,
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "phones": [{"is_primary": True, "phone_isd_code": "+1", "phone_number": "555"}],
        },
        created_contact_id=CONTACT_ID,
    )
    assert len(targets) == 2
    assert targets[1]["client_type"] == "person"


# ---------------------------------------------------------------------------
# Update company — JSONB lists, addresses, custom fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_add_update_remove(monkeypatch: pytest.MonkeyPatch):
    """JSONB list delta supports add, update, and remove operations."""
    svc = _service()
    from apps.user_service.app.schemas.common import (
        WebsiteInput,
        WebsitesUpdate,
        WebsiteUpdateItem,
    )

    current = _company_row(
        websites=[{"id": "w1", "url": "https://old.example", "is_primary": True}],
    )
    payload: dict[str, Any] = {}
    update_obj = WebsitesUpdate(
        add=[WebsiteInput(url="https://new.example", type="corporate", is_primary=False)],
        update=[WebsiteUpdateItem(id="w1", url="https://updated.example")],
        remove=["w2"],
    )
    await svc._apply_jsonb_list_changes(
        update_obj,
        current=current,
        payload=payload,
        field_name="websites",
        not_found_message_key="companies.errors.website_not_found",
    )
    urls = [item["url"] for item in payload["websites"]]
    assert "https://updated.example" in urls
    assert any("https://new.example" in u for u in urls)


@pytest.mark.asyncio
async def test_apply_jsonb_list_changes_update_not_found():
    """Updating a missing JSONB list item raises NotFoundException."""
    svc = _service()
    from apps.user_service.app.schemas.common import WebsitesUpdate, WebsiteUpdateItem

    current = _company_row(websites=[])
    update_obj = WebsitesUpdate(
        update=[WebsiteUpdateItem(id="missing", url="https://x.example")],
    )
    with pytest.raises(NotFoundException):
        await svc._apply_jsonb_list_changes(
            update_obj,
            current=current,
            payload={},
            field_name="websites",
            not_found_message_key="companies.errors.website_not_found",
        )


@pytest.mark.asyncio
async def test_update_company_billing_preferences_merge(monkeypatch: pytest.MonkeyPatch):
    """Billing preferences are merged with existing JSONB."""
    _patch_custom_fields(monkeypatch)
    current = _company_row(
        contacts=[],
        addresses=[],
        billing_preferences={"currency": "USD"},
    )
    repo = _FakeCompaniesRepo(for_update=current, updated=current)
    svc = _service(companies_repo=repo)
    from apps.user_service.app.schemas.common import BillingPreferencesUpdate

    result = await svc.update_company(
        company_id=COMPANY_ID,
        body=UpdateCompanyRequest(
            billing_preferences=BillingPreferencesUpdate(payment_terms="Net 30"),
        ),
    )
    assert result["ok"] is True
    assert repo.last_update_kwargs["update_data"]["billing_preferences"]["currency"] == "USD"


@pytest.mark.asyncio
async def test_apply_company_addresses_remove_add(monkeypatch: pytest.MonkeyPatch):
    """Address delta remove/add updates in-memory snapshot."""
    _patch_custom_fields(monkeypatch)
    address_id = "990e8400-e29b-41d4-a716-446655440004"
    new_id = "aa0e8400-e29b-41d4-a716-446655440005"
    current = _company_row(
        contacts=[],
        addresses=[{"id": address_id, "city": "Old", "company_id": COMPANY_ID}],
    )
    repo = _FakeCompaniesRepo(for_update=current, updated=current)
    repo.create_company_addresses = AsyncMock(
        return_value=[{"id": new_id, "city": "New City", "company_id": COMPANY_ID}],
    )
    svc = _service(companies_repo=repo)
    from apps.user_service.app.schemas.common import AddressesUpdate, AddressInput

    body = UpdateCompanyRequest(
        addresses=AddressesUpdate(
            remove=[address_id],
            add=[AddressInput(city="New City", country="US")],
        ),
    )
    result = await svc.update_company(company_id=COMPANY_ID, body=body)
    assert result["ok"] is True
    assert repo.deleted_address_ids == [address_id]


@pytest.mark.asyncio
async def test_create_contact_for_company_association(monkeypatch: pytest.MonkeyPatch):
    """Creating a contact during association delta inserts contact row."""
    _patch_custom_fields(monkeypatch)
    contacts_repo = _FakeContactsRepo()
    contacts_repo.create_contacts = AsyncMock(
        return_value=[{"id": CONTACT_ID, "email": "new@example.com"}],
    )
    contacts_repo.create_contact_addresses = AsyncMock()
    svc = _service(contacts_repo=contacts_repo)
    monkeypatch.setattr(
        svc,
        "_provision_contact_identity",
        AsyncMock(return_value=("new@example.com", "user-1", "iso-1", None)),
    )
    from apps.user_service.app.schemas.contacts import CreateContactRequest

    contact_id, row = await svc._create_contact_for_company_association(
        create_contact=CreateContactRequest(
            first_name="New",
            last_name="Contact",
            email="new@example.com",
        ),
    )
    assert contact_id == CONTACT_ID
    assert row["email"] == "new@example.com"


@pytest.mark.asyncio
async def test_search_companies_email_query(monkeypatch: pytest.MonkeyPatch):
    """Email-shaped queries use email search params."""
    svc = _service()
    typesense = MagicMock()
    typesense.embed_query_text = AsyncMock(return_value=[0.1, 0.2])
    typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = typesense
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.shared_settings",
        MagicMock(typesense=MagicMock(vector_distance_threshold=0.5)),
    )

    await svc.search_companies(query="info@acme.com", page=1, page_size=10, status=None)

    params = typesense.search.await_args.args[0]
    assert "@" in params["q"]
    assert "vector_query" in params


@pytest.mark.asyncio
async def test_search_companies_phone_query():
    """Digit-heavy queries use phone search params."""
    svc = _service()
    typesense = MagicMock()
    typesense.embed_query_text = AsyncMock(return_value=None)
    typesense.search = AsyncMock(return_value={"hits": [], "found": 0})
    svc._typesense = typesense

    await svc.search_companies(
        query="5551234567", page=1, page_size=10, status=ClientStatus.ACTIVE.value
    )

    params = typesense.search.await_args.args[0]
    assert "filter_by" in params
    assert "status:=active" in params["filter_by"]


def test_typesense_hits_skips_invalid_documents():
    """Typesense mapper ignores malformed hits."""
    rows = CompaniesService.typesense_hits_to_company_summary_rows(
        [{"document": None}, {"document": {"id": ""}}],
    )
    assert rows == []


def test_typesense_hits_maps_contacts_and_phones():
    """Typesense mapper normalizes nested contacts."""
    hits = [
        {
            "document": {
                "id": COMPANY_ID,
                "organization_id": ORG_ID,
                "name": "Acme",
                "created_at": 1735689600,
                "updated_at": 1735776000,
                "contacts": [
                    {
                        "id": CONTACT_ID,
                        "first_name": "Jane",
                        "phones_display": [{"phone_number": "555"}],
                        "is_primary": True,
                    }
                ],
            }
        }
    ]
    rows = CompaniesService.typesense_hits_to_company_summary_rows(hits)
    assert rows[0]["contacts"][0]["id"] == CONTACT_ID
    assert rows[0]["contacts"][0]["is_primary"] is True


def test_typesense_property_lazy_init(monkeypatch: pytest.MonkeyPatch):
    """Typesense client is constructed lazily from settings."""
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.TypesenseService.from_settings",
        lambda **kwargs: MagicMock(name="typesense-client"),
    )
    svc = _service()
    client = svc.typesense
    assert client is not None
    assert svc.typesense is client


@pytest.mark.asyncio
async def test_prepare_optional_contact_create_and_associate(monkeypatch: pytest.MonkeyPatch):
    """Create-and-associate path builds contact_data with provisioned identity."""
    _patch_custom_fields(monkeypatch)
    svc = _service()
    monkeypatch.setattr(
        svc,
        "_provision_contact_identity",
        AsyncMock(return_value=("jane@example.com", "user-1", "iso-1", "Pass1!")),
    )
    from apps.user_service.app.schemas.contacts import CreateContactRequest

    body = CreateCompanyRequest(
        name="Acme Corp",
        contact_association=CompanyContactsCreate(
            create_and_associate=CompanyContactAssociationCreate(
                contact=CreateContactRequest(
                    first_name="Jane",
                    last_name="Doe",
                    email="jane@example.com",
                ),
                is_primary=True,
            )
        ),
    )
    (
        contact_id,
        contact_data,
        _addresses,
        set_primary,
        password,
    ) = await svc._prepare_optional_company_contact_association(
        body=body,
        contact_phones_payload=[],
        contact_social_pages_payload=[],
    )
    assert contact_id is None
    assert contact_data is not None
    assert contact_data["email"] == "jane@example.com"
    assert set_primary is True
    assert password == "Pass1!"


@pytest.mark.asyncio
async def test_apply_contacts_delta_creates_contact(monkeypatch: pytest.MonkeyPatch):
    """Apply delta can create a new contact and set primary."""
    _patch_custom_fields(monkeypatch)
    cc_repo = _FakeContactCompaniesRepo()
    svc = _service(cc_repo=cc_repo)
    from apps.user_service.app.schemas.contacts import CreateContactRequest

    monkeypatch.setattr(
        svc,
        "_create_contact_for_company_association",
        AsyncMock(return_value=(CONTACT_ID, {"id": CONTACT_ID})),
    )
    delta = CompanyContactUpdate(
        create_and_associate=CompanyContactAssociationCreate(
            contact=CreateContactRequest(
                first_name="New",
                last_name="Person",
                email="new@example.com",
            ),
            is_primary=True,
        ),
    )

    result = await svc.apply_contacts_update_delta(company_id=COMPANY_ID, delta=delta)

    assert result["created_contact_id"] == CONTACT_ID
    assert cc_repo.last_delta_kwargs["set_primary_contact_id"] == CONTACT_ID


def test_schedule_company_and_contact_index_tasks(monkeypatch: pytest.MonkeyPatch):
    """Update background indexing includes created contact enrichment."""
    background_tasks = MagicMock()
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.ContactsService.trigger_enrichment_background",
        MagicMock(),
    )
    body = UpdateCompanyRequest(
        contact_association=CompanyContactUpdate(
            add_associations=[
                CompanyContactAssociationAdd(contact_id=CONTACT_ID, is_primary=False),
            ]
        ),
    )
    CompaniesService._schedule_company_and_contact_index_tasks(
        background_tasks=background_tasks,
        company_id=COMPANY_ID,
        organization_id=ORG_ID,
        body=body,
        update_result={
            "contacts_delta": {
                "affected_contact_ids": [CONTACT_ID],
                "created_contact_id": CONTACT_ID,
            }
        },
    )
    assert background_tasks.add_task.call_count >= 2


@pytest.mark.asyncio
async def test_soft_delete_company_update_missing(monkeypatch: pytest.MonkeyPatch):
    """soft_delete raises when update returns no row."""
    current = _company_row()
    repo = _FakeCompaniesRepo(for_update=current, updated=None)
    svc = _service(companies_repo=repo)
    with pytest.raises(NotFoundException):
        await svc.soft_delete_company(company_id=COMPANY_ID)


@pytest.mark.asyncio
async def test_list_companies_parses_string_contacts(monkeypatch: pytest.MonkeyPatch):
    """List companies parses JSON string contacts from DB."""
    _patch_custom_fields(monkeypatch)
    repo = _FakeCompaniesRepo(
        companies=[_company_row(contacts='[{"id": "c1", "first_name": "Jane"}]')],
        total=1,
    )
    svc = _service(companies_repo=repo)
    result = await svc.list_companies(
        search=None,
        status=None,
        dropdown_filters=None,
        page=1,
        page_size=20,
    )
    assert isinstance(result["items"][0]["contacts"], list)


@pytest.mark.asyncio
async def test_update_company_with_contact_association_snapshot(monkeypatch: pytest.MonkeyPatch):
    """Update company refreshes contacts snapshot when association delta applied."""
    _patch_custom_fields(monkeypatch)
    current = _company_row(contacts=[], addresses=[])
    repo = _FakeCompaniesRepo(for_update=current, updated=current)
    cc_repo = _FakeContactCompaniesRepo()
    cc_repo.snapshot = [{"id": CONTACT_ID, "is_primary": True}]
    svc = _service(companies_repo=repo, cc_repo=cc_repo)
    monkeypatch.setattr(
        svc,
        "apply_contacts_update_delta",
        AsyncMock(return_value={"affected_contact_ids": [CONTACT_ID]}),
    )
    body = UpdateCompanyRequest(
        contact_association=CompanyContactUpdate(
            update_associations=[
                CompanyContactAssociationUpdate(contact_id=CONTACT_ID, is_primary=True),
            ]
        ),
    )
    result = await svc.update_company(company_id=COMPANY_ID, body=body)
    assert result["contacts_delta"]["affected_contact_ids"] == [CONTACT_ID]


def test_maybe_send_portal_welcome_email_skips_without_access():
    """Portal welcome email is skipped when portal_access is false."""
    svc = _service()
    svc._maybe_send_portal_welcome_email(
        portal_access=False,
        created_contact_row={"email": "jane@example.com"},
        created_contact_password="x",
    )


def test_extract_created_contact_json_string():
    """Created contact JSON string is parsed when returned as text."""
    svc = _service()
    contact, contact_id = svc._extract_created_contact(
        created={
            "contact": json.dumps({"id": CONTACT_ID, "email": "j@example.com"}),
            "contact_id": CONTACT_ID,
        }
    )
    assert contact is not None
    assert contact_id == CONTACT_ID
