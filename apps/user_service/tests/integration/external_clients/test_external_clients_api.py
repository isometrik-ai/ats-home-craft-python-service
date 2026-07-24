"""Integration tests for external clients endpoints."""

import pytest
from fastapi import Request

from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.main import app
from apps.user_service.tests.utils.assertions import assert_success

ORG_ID = "org-123"
COMPANY_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "550e8400-e29b-41d4-a716-446655440001"

_FAKE_COMPANY_SUMMARY = {
    "id": COMPANY_ID,
    "organization_id": ORG_ID,
    "status": "active",
    "name": "Acme Corp",
    "industry": "Technology",
    "profile_photo_url": None,
    "email": "info@acme.example",
    "phones": [],
    "contacts": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_COMPANY_DETAILS = {
    **_FAKE_COMPANY_SUMMARY,
    "portal_access": False,
    "primary_contact_id": None,
    "tags": [],
    "websites": [],
    "billing_preferences": {},
    "social_pages": [],
    "linked_pages": [],
    "products": [],
    "key_people": [],
    "custom_fields": [],
    "additional_data": {},
    "notes": [],
    "addresses": [],
    "target_market_segments": [],
    "current_tech_stack": [],
    "preferred_communication_channels": [],
    "industry_specific_terminologies": [],
    "description": None,
}

_FAKE_CONTACT_SUMMARY = {
    "id": CONTACT_ID,
    "organization_id": ORG_ID,
    "status": "active",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phones": [],
    "companies": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_CONTACT_DETAILS = {
    **_FAKE_CONTACT_SUMMARY,
    "portal_access": False,
    "title": "Manager",
    "tags": [],
    "websites": [],
    "social_pages": [],
    "linked_pages": [],
    "custom_fields": [],
    "additional_data": {},
    "notes": [],
    "addresses": [],
}

JOB_ID = "job-ext-12345"

_FAKE_IMPORT_JOB = {
    "job_id": JOB_ID,
    "organization_id": ORG_ID,
    "status": "queued",
    "import_type": "contacts",
    "file_url": "https://example.com/contacts.csv",
    "file_type": "csv",
    "schema_version": 1,
    "total_rows": 10,
    "processed_rows": 0,
    "success_rows": 0,
    "error_rows": 0,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "started_at": None,
    "finished_at": None,
}


async def _fake_org_context(request: Request) -> str:
    """Return a fixed organization id and set external audit actor email."""
    request.state.external_actor_email = "api@acme.com"
    return ORG_ID


@pytest.fixture
def external_org_context():
    """Override external organization context dependency."""
    app.dependency_overrides[get_organization_context] = _fake_org_context
    yield
    app.dependency_overrides.pop(get_organization_context, None)


@pytest.mark.asyncio
async def test_external_list_companies(monkeypatch, client, external_org_context):
    """GET /integrations/clients/companies lists companies."""

    async def fake_list_companies(
        _self,
        *,
        search=None,
        status=None,
        page=1,
        page_size=20,
        dropdown_filters=None,
    ):
        del _self, search, status, dropdown_filters
        assert page == 1
        assert page_size == 20
        return {"items": [_FAKE_COMPANY_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.list_companies",
        fake_list_companies,
    )

    res = await client.get(
        "/v1/integrations/clients/companies",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == COMPANY_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_external_list_contacts(monkeypatch, client, external_org_context):
    """GET /integrations/clients/contacts lists contacts."""

    async def fake_list_contacts(
        _self,
        *,
        search=None,
        status=None,
        page=1,
        page_size=20,
    ):
        del _self, search, status
        assert page == 1
        return {"items": [_FAKE_CONTACT_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.list_contacts",
        fake_list_contacts,
    )

    res = await client.get(
        "/v1/integrations/clients/contacts",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == CONTACT_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_external_lookup_contacts(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts/lookup finds contacts."""

    async def fake_lookup(_self, *, contact_ids):
        del _self
        assert contact_ids == [CONTACT_ID]
        return [
            {
                "id": CONTACT_ID,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "external_contact_id": None,
            }
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contacts_by_ids",
        fake_lookup,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts/lookup",
        json={"contact_ids": [CONTACT_ID]},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_external_get_company(monkeypatch, client, external_org_context):
    """GET /integrations/clients/companies/{id} returns detail."""

    async def fake_get_details(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return _FAKE_COMPANY_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.get_company_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/integrations/clients/companies/{COMPANY_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == COMPANY_ID
    assert body["data"]["name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_external_list_variables(monkeypatch, client, external_org_context):
    """GET /integrations/clients/variables lists entity variables."""

    async def fake_get_definitions(_self, entity_type):
        del _self, entity_type
        return [
            {
                "variable_key": "email",
                "field_name": "Email",
                "field_type": "text",
                "source": "fixed",
            }
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.external_variables_service."
        "ExternalVariablesService.get_variable_definitions",
        fake_get_definitions,
    )

    res = await client.get(
        "/v1/integrations/clients/variables",
        params={"entity_type": "contact"},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["variable_key"] == "email"


@pytest.mark.asyncio
async def test_external_contact_fields_by_phone(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts/by-phone resolves fields."""

    async def fake_resolve(_self, *, phone_number, variable_keys=None):
        del _self, variable_keys
        assert phone_number == "+15551234567"
        return [{"variable_key": "email", "variable_value": "jane@example.com"}]

    monkeypatch.setattr(
        "apps.user_service.app.services.external_variables_service."
        "ExternalVariablesService.resolve_contact_field_values_by_phone",
        fake_resolve,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts/by-phone",
        json={"phone_number": "+15551234567"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data[0]["variable_key"] == "email"


@pytest.mark.asyncio
async def test_external_create_company(monkeypatch, client, external_org_context):
    """POST /integrations/clients/companies creates a company."""

    async def fake_create_company(_self, body):
        del _self, body
        return {
            "company_id": COMPANY_ID,
            "created_entities": [
                {
                    "entity_table": "contacts",
                    "action": "create_contact",
                    "entity_id": CONTACT_ID,
                }
            ],
            "created_lead_id": None,
            "enrichment_targets": [],
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.create_company",
        fake_create_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service."
        "ClientEnrichmentService.from_settings",
        lambda: type("S", (), {"run_client_enrichment": staticmethod(lambda **_k: None)})(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_companies_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_contacts_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/clients/companies",
        json={"name": "Acme Corp"},
    )
    body = assert_success(res, 201)
    assert body["data"]["company_id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_external_delete_contact(monkeypatch, client, external_org_context):
    """DELETE /integrations/clients/contacts/{id} soft-deletes."""

    async def fake_soft_delete(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return {"old_data": {"id": CONTACT_ID}, "new_data": {"status": "deleted"}}

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.soft_delete_contact",
        fake_soft_delete,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )

    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.typesense_index_service.delete_contact_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.delete(f"/v1/integrations/clients/contacts/{CONTACT_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_create_contact(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts creates a contact."""

    async def fake_create_contact(_self, body):
        del _self, body
        return {
            "contact_id": CONTACT_ID,
            "company_id": None,
            "created_entities": [],
            "created_lead_id": None,
            "enrichment_targets": [],
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.create_contact",
        fake_create_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service."
        "ClientEnrichmentService.from_settings",
        lambda: type("S", (), {"run_client_enrichment": staticmethod(lambda **_k: None)})(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_contacts_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts",
        json={
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["contact_id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_external_get_contact_by_id(monkeypatch, client, external_org_context):
    """GET /integrations/clients/contacts/{id} returns detail."""

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_CONTACT_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/integrations/clients/contacts/{CONTACT_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_external_get_contact_by_email(monkeypatch, client, external_org_context):
    """GET /integrations/clients/contacts/by-email finds contact."""

    async def fake_get_by_email(_self, *, email: str):
        del _self
        assert email == "jane@example.com"
        return _FAKE_CONTACT_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.get_contact_details_by_email",
        fake_get_by_email,
    )

    res = await client.get(
        "/v1/integrations/clients/contacts/by-email",
        params={"email": "jane@example.com"},
    )
    body = assert_success(res, 200)
    assert body["data"]["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_external_update_company(monkeypatch, client, external_org_context):
    """PATCH /integrations/clients/companies/{id} updates company."""

    async def fake_update(_self, *, company_id: str, body):
        del _self, body
        assert company_id == COMPANY_ID
        return {"old_data": {"name": "Old"}, "new_data": {"name": "New"}}

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": COMPANY_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.update_company",
        fake_update,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_company_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/integrations/clients/companies/{COMPANY_ID}",
        json={"name": "Updated Acme"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_update_contact(monkeypatch, client, external_org_context):
    """PATCH /integrations/clients/contacts/{id} updates contact."""

    async def fake_update(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {"old_data": {"first_name": "Jane"}, "new_data": {"first_name": "Janet"}}

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.update_contact",
        fake_update,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_contact_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/integrations/clients/contacts/{CONTACT_ID}",
        json={"first_name": "Janet"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_delete_company(monkeypatch, client, external_org_context):
    """DELETE /integrations/clients/companies/{id} soft-deletes."""

    async def fake_soft_delete(_self, *, company_id: str):
        del _self
        assert company_id == COMPANY_ID
        return {"old_data": {"id": COMPANY_ID}, "new_data": {"status": "deleted"}}

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": COMPANY_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.soft_delete_company",
        fake_soft_delete,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.typesense_index_service.delete_company_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.delete(f"/v1/integrations/clients/companies/{COMPANY_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_list_variables_company(monkeypatch, client, external_org_context):
    """GET /integrations/clients/variables?entity_type=company."""

    async def fake_get_definitions(_self, entity_type):
        del _self
        assert entity_type.value == "company"
        return [
            {"variable_key": "name", "field_name": "Name", "field_type": "text", "source": "fixed"}
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.external_variables_service."
        "ExternalVariablesService.get_variable_definitions",
        fake_get_definitions,
    )

    res = await client.get(
        "/v1/integrations/clients/variables",
        params={"entity_type": "company"},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["variable_key"] == "name"


@pytest.mark.asyncio
async def test_external_lookup_contacts_empty(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts/lookup with no matches."""

    async def fake_lookup(_self, *, contact_ids):
        del _self, contact_ids
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contacts_by_ids",
        fake_lookup,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts/lookup",
        json={"contact_ids": ["missing-id"]},
    )
    body = assert_success(res, 200)
    assert body["data"] == []


@pytest.mark.asyncio
async def test_external_create_contacts_import(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts/imports creates job."""

    async def fake_create_job(_self, **kwargs):
        del _self, kwargs
        return _FAKE_IMPORT_JOB, {"event_type": "contacts.import.requested"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.create_job_and_enqueue",
        fake_create_job,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts/imports",
        json={
            "file_url": "https://example.com/contacts.csv",
            "schema_version": 1,
        },
    )
    body = assert_success(res, 202)
    assert body["data"]["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_external_get_contacts_import_job(monkeypatch, client, external_org_context):
    """GET /integrations/clients/contacts/imports/{job_id}."""

    async def fake_get_job(_self, *, job_id: str, organization_id: str):
        del _self
        assert job_id == JOB_ID
        assert organization_id == ORG_ID
        return _FAKE_IMPORT_JOB

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsImportService.get_job",
        fake_get_job,
    )

    res = await client.get(f"/v1/integrations/clients/contacts/imports/{JOB_ID}")
    body = assert_success(res, 200)
    assert body["data"]["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_external_get_contacts_import_errors(monkeypatch, client, external_org_context):
    """GET /integrations/clients/contacts/imports/{job_id}/errors."""

    async def fake_get_job(_self, *, job_id: str, organization_id: str):
        del _self
        assert job_id == JOB_ID
        return _FAKE_IMPORT_JOB

    async def fake_list_errors(_self, **kwargs):
        del _self, kwargs
        return ([{"row": 2, "error": "invalid email"}], 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsImportService.get_job",
        fake_get_job,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.list_job_error_rows",
        fake_list_errors,
        raising=False,
    )

    res = await client.get(f"/v1/integrations/clients/contacts/imports/{JOB_ID}/errors")
    body = assert_success(res, 200)
    assert body["data"][0]["row"] == 2


@pytest.mark.asyncio
async def test_external_retry_contacts_import(monkeypatch, client, external_org_context):
    """POST /integrations/clients/contacts/imports/{job_id}/retry."""

    async def fake_retry(_self, **kwargs):
        del _self, kwargs
        return (_FAKE_IMPORT_JOB, {"event_type": "contacts.import.requested"})

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.retry_job_and_enqueue",
        fake_retry,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.post(f"/v1/integrations/clients/contacts/imports/{JOB_ID}/retry")
    body = assert_success(res, 202)
    assert body["data"]["job_id"] == JOB_ID


@pytest.mark.asyncio
async def test_external_list_companies_empty(monkeypatch, client, external_org_context):
    """GET /integrations/clients/companies returns empty collection."""

    async def fake_list_companies(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.list_companies",
        fake_list_companies,
    )

    res = await client.get(
        "/v1/integrations/clients/companies",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_external_create_company_with_lead_and_enrichment(
    monkeypatch, client, external_org_context
):
    """POST /integrations/clients/companies handles lead and enrichment side effects."""

    async def fake_create_company(_self, body):
        del _self, body
        return {
            "company_id": COMPANY_ID,
            "created_entities": [
                {
                    "entity_table": "contacts",
                    "action": "create_contact",
                    "entity_id": CONTACT_ID,
                }
            ],
            "created_lead_id": "lead-1",
            "enrichment_targets": [
                {
                    "client_id": COMPANY_ID,
                    "organization_id": ORG_ID,
                    "client_type": "company",
                    "payload_data": {"name": "Acme Corp"},
                    "entity_table": "companies",
                }
            ],
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return [({"event_type": "contacts.created"}, CONTACT_ID)]

    async def fake_lead_event(_self, **_kwargs):
        del _self
        return {"event_type": "leads.created", "aggregate_id": "lead-1"}

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.create_company",
        fake_create_company,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService."
        "create_lead_created_lifecycle_event",
        fake_lead_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service."
        "ClientEnrichmentService.from_settings",
        lambda: type("S", (), {"run_client_enrichment": staticmethod(lambda **_k: None)})(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_companies_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_contacts_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/clients/companies",
        json={"name": "Acme Corp"},
    )
    body = assert_success(res, 201)
    assert body["data"]["company_id"] == COMPANY_ID
    assert body["data"]["lead_id"] == "lead-1"


@pytest.mark.asyncio
async def test_external_create_contact_with_company_and_enrichment(
    monkeypatch, client, external_org_context
):
    """POST /integrations/clients/contacts indexes company and runs enrichment."""

    async def fake_create_contact(_self, body):
        del _self, body
        return {
            "contact_id": CONTACT_ID,
            "company_id": COMPANY_ID,
            "created_entities": [
                {
                    "entity_table": "companies",
                    "action": "create_company",
                    "entity_id": COMPANY_ID,
                }
            ],
            "created_lead_id": "lead-1",
            "enrichment_targets": [
                {
                    "client_id": CONTACT_ID,
                    "organization_id": ORG_ID,
                    "client_type": "contact",
                    "payload_data": {"email": "jane@example.com"},
                    "entity_table": "contacts",
                }
            ],
        }

    async def fake_create_lifecycle_events(**_kwargs):
        return []

    async def fake_lead_event(_self, **_kwargs):
        del _self
        return {"event_type": "leads.created", "aggregate_id": "lead-1"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.create_contact",
        fake_create_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.create_lifecycle_events_for_created_entities",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService."
        "create_lead_created_lifecycle_event",
        fake_lead_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.client_enrichment_service."
        "ClientEnrichmentService.from_settings",
        lambda: type("S", (), {"run_client_enrichment": staticmethod(lambda **_k: None)})(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_contacts_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.api.external_clients.index_companies_background",
        lambda *_args, **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts",
        json={
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["contact_id"] == CONTACT_ID
    assert body["data"]["company_id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_external_update_company_with_contacts_delta(
    monkeypatch, client, external_org_context
):
    """PATCH /integrations/clients/companies/{id} emits contact association events."""

    async def fake_update(_self, *, company_id: str, body):
        del _self, body
        assert company_id == COMPANY_ID
        return {
            "old_data": {"name": "Old"},
            "new_data": {"name": "New"},
            "contacts_delta": {
                "affected_contact_ids": [CONTACT_ID, "contact-2"],
                "created_contact_id": "contact-2",
            },
        }

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": COMPANY_ID}

    async def fake_create_lifecycle_events(_self, *, items, topics):
        del _self, topics
        return [
            {"event_id": f"evt-{idx}", "aggregate_id": item["aggregate_id"]}
            for idx, item in enumerate(items)
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service.CompaniesService.update_company",
        fake_update,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_events",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.companies_service."
        "CompaniesService.schedule_company_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/integrations/clients/companies/{COMPANY_ID}",
        json={"name": "Updated Acme"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_update_contact_with_companies_delta(
    monkeypatch, client, external_org_context
):
    """PATCH /integrations/clients/contacts/{id} emits company association events."""

    async def fake_update(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {
            "old_data": {"first_name": "Jane"},
            "new_data": {"first_name": "Janet"},
            "companies_delta": {
                "affected_company_ids": [COMPANY_ID, "company-2"],
                "created_company_id": "company-2",
            },
        }

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": CONTACT_ID}

    async def fake_create_lifecycle_events(_self, *, items, topics):
        del _self, topics
        return [
            {"event_id": f"evt-{idx}", "aggregate_id": item["aggregate_id"]}
            for idx, item in enumerate(items)
        ]

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.update_contact",
        fake_update,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_events",
        fake_create_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_contact_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/integrations/clients/contacts/{CONTACT_ID}",
        json={"first_name": "Janet"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_contact_fields_by_phone_short_number(
    monkeypatch, client, external_org_context
):
    """POST /integrations/clients/contacts/by-phone masks short phone numbers in logs."""

    async def fake_resolve(_self, *, phone_number, variable_keys=None):
        del _self, variable_keys
        assert phone_number == "+1234"
        return [{"variable_key": "email", "variable_value": "jane@example.com"}]

    monkeypatch.setattr(
        "apps.user_service.app.services.external_variables_service."
        "ExternalVariablesService.resolve_contact_field_values_by_phone",
        fake_resolve,
    )

    res = await client.post(
        "/v1/integrations/clients/contacts/by-phone",
        json={"phone_number": "+1234"},
    )
    assert res.status_code == 200
    assert res.json()[0]["variable_key"] == "email"


@pytest.mark.asyncio
async def test_external_get_contacts_import_job_not_found(
    monkeypatch, client, external_org_context
):
    """GET /integrations/clients/contacts/imports/{job_id} returns 404 when missing."""

    async def fake_get_job(_self, *, job_id: str, organization_id: str):
        del _self, job_id, organization_id
        return None

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsImportService.get_job",
        fake_get_job,
    )

    res = await client.get(f"/v1/integrations/clients/contacts/imports/{JOB_ID}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_external_get_contacts_import_errors_not_found(
    monkeypatch, client, external_org_context
):
    """GET /integrations/clients/contacts/imports/{job_id}/errors returns 404 when empty."""

    async def fake_get_job(_self, *, job_id: str, organization_id: str):
        del _self
        assert job_id == JOB_ID
        assert organization_id == ORG_ID
        return _FAKE_IMPORT_JOB

    async def fake_list_errors(_self, **kwargs):
        del _self, kwargs
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service.ContactsImportService.get_job",
        fake_get_job,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_imports_service."
        "ContactsImportService.list_job_error_rows",
        fake_list_errors,
        raising=False,
    )

    res = await client.get(f"/v1/integrations/clients/contacts/imports/{JOB_ID}/errors")
    assert res.status_code == 404
