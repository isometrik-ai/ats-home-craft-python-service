"""Integration tests for contacts endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

CONTACT_ID = "contact-1"
ORG_ID = "org-123"
UNIT_ID = "unit-1"
COMPANY_ID = "company-1"

_FAKE_SUMMARY = {
    "id": CONTACT_ID,
    "organization_id": ORG_ID,
    "status": ClientStatus.ACTIVE.value,
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phones": [],
    "company_names": [],
    "tags": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_DETAILS = {
    **_FAKE_SUMMARY,
    "portal_access": False,
    "emails": [],
    "custom_fields": [],
    "additional_data": {},
    "social_pages": [],
    "notes": [],
    "work_history": [],
    "educational_history": [],
    "skills": [],
    "companies": [],
    "leads": [],
    "addresses": [],
    "communication_preferences": {},
}


def _patch_contacts_access(monkeypatch) -> None:
    """Bypass RBAC for contacts routes."""
    patch_check_permissions(monkeypatch, "apps.user_service.app.api.contacts")


@pytest.mark.asyncio
async def test_create_contact(monkeypatch, client):
    """POST /contacts creates a contact."""
    _patch_contacts_access(monkeypatch)

    async def fake_create_contact(_self, body):
        del _self, body
        return {
            "contact_id": CONTACT_ID,
            "old_data": None,
            "new_data": {"email": "jane@example.com"},
            "created_entities": [],
        }

    async def fake_lifecycle_events(**_kwargs):
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.create_contact",
        fake_create_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.create_lifecycle_events_for_created_entities",
        fake_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )

    res = await client.post(
        "/v1/contacts",
        json={"email": "jane@example.com", "first_name": "Jane", "last_name": "Doe"},
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_create_contact_with_lead(monkeypatch, client):
    """POST /contacts publishes lead lifecycle event when a lead is created."""
    _patch_contacts_access(monkeypatch)

    async def fake_create_contact(_self, body):
        del _self, body
        return {
            "contact_id": CONTACT_ID,
            "old_data": None,
            "new_data": {"email": "jane@example.com"},
            "created_entities": [],
            "enrichment_targets": [],
            "created_lead_id": "lead-1",
        }

    async def fake_lifecycle_events(**_kwargs):
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
        fake_lifecycle_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService."
        "create_lead_created_lifecycle_event",
        fake_lead_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_lifecycle_event_publishes",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_typesense_indexing_for_created_entities",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.schedule_enrichment",
        lambda **_kwargs: None,
    )

    res = await client.post(
        "/v1/contacts",
        json={"email": "jane@example.com", "first_name": "Jane", "last_name": "Doe"},
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_search_contacts_empty(monkeypatch, client):
    """GET /contacts/search returns empty collection."""
    _patch_contacts_access(monkeypatch)

    async def fake_search_contacts(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.search_contacts",
        fake_search_contacts,
    )

    res = await client.get("/v1/contacts/search", params={"query": "missing"})
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_contact_activity_empty_with_total(monkeypatch, client):
    """GET /contacts/activity/{id}/ handles empty page with non-zero total."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_get_activity(_self, *, contact_id: str, limit: int, offset: int):
        del _self, contact_id, limit, offset
        return ([], 5)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_contact_activity",
        fake_get_activity,
    )

    res = await client.get(
        f"/v1/contacts/activity/{CONTACT_ID}/",
        params={"page": 2, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 5


@pytest.mark.asyncio
async def test_update_contact_with_company_association(monkeypatch, client):
    """PATCH /contacts/{id} emits company association lifecycle events."""
    _patch_contacts_access(monkeypatch)

    async def fake_update_contact(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {
            "old_data": _FAKE_DETAILS,
            "new_data": {**_FAKE_DETAILS, "first_name": "Janet"},
            "companies_delta": {
                "affected_company_ids": [COMPANY_ID, "company-2"],
                "created_company_id": "company-2",
            },
        }

    async def fake_create_lifecycle_event(_self, **_kwargs):
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
        fake_update_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
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
        f"/v1/contacts/{CONTACT_ID}",
        json={"first_name": "Janet"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_list_contacts(monkeypatch, client):
    """POST /contacts/list returns paginated contacts."""
    _patch_contacts_access(monkeypatch)

    async def fake_list_contacts(_self, **kwargs):
        del _self, kwargs
        return {"items": [_FAKE_SUMMARY], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.list_contacts",
        fake_list_contacts,
    )

    res = await client.post("/v1/contacts/list", json={"page": 1, "page_size": 20})
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == CONTACT_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_contacts_empty(monkeypatch, client):
    """POST /contacts/list returns empty collection."""
    _patch_contacts_access(monkeypatch)

    async def fake_list_contacts(_self, **kwargs):
        del _self, kwargs
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.list_contacts",
        fake_list_contacts,
    )

    res = await client.post("/v1/contacts/list", json={"page": 1, "page_size": 20})
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_search_contacts(monkeypatch, client):
    """GET /contacts/search returns Typesense hits."""
    _patch_contacts_access(monkeypatch)

    async def fake_search_contacts(_self, **kwargs):
        del _self, kwargs
        return {"items": [{"document": _FAKE_SUMMARY}], "total": 1}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.search_contacts",
        fake_search_contacts,
    )

    res = await client.get("/v1/contacts/search", params={"query": "jane"})
    body = assert_success(res, 200)
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_contact_overview(monkeypatch, client):
    """GET /contacts/overview returns dashboard counts."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_overview(_self, *, status=None):
        del _self, status
        return {"total": 10, "owners": 4, "tenants": 2, "vendors": 4}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_overview",
        fake_get_overview,
    )

    res = await client.get("/v1/contacts/overview")
    body = assert_success(res, 200)
    assert body["data"]["total"] == 10


@pytest.mark.asyncio
async def test_get_contact_details(monkeypatch, client):
    """GET /contacts/{id} returns contact details."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/contacts/{CONTACT_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == CONTACT_ID


@pytest.mark.asyncio
async def test_update_contact(monkeypatch, client):
    """PATCH /contacts/{id} updates a contact."""
    _patch_contacts_access(monkeypatch)

    async def fake_update_contact(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {"old_data": _FAKE_DETAILS, "new_data": {**_FAKE_DETAILS, "first_name": "Janet"}}

    async def fake_company_events(**_kwargs):
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.update_contact",
        fake_update_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.create_lifecycle_events_for_created_entities",
        fake_company_events,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.schedule_contact_update_background_tasks",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/contacts/{CONTACT_ID}",
        json={"first_name": "Janet"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_contact(monkeypatch, client):
    """DELETE /contacts/{id} soft-deletes a contact."""
    _patch_contacts_access(monkeypatch)

    async def fake_soft_delete(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return {"old_data": _FAKE_DETAILS, "new_data": None}

    async def fake_delete_event(_self, **_kwargs):
        del _self
        return {"event_type": "contacts.deleted"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.soft_delete_contact",
        fake_soft_delete,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_delete_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.typesense_index_service.delete_contact_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.delete(f"/v1/contacts/{CONTACT_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_get_contact_activity(monkeypatch, client):
    """GET /contacts/activity/{id}/ returns audit activity."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_get_activity(_self, *, contact_id: str, limit: int, offset: int):
        del _self, contact_id, limit, offset
        return ([{"action": "UPDATE"}], 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_contact_activity",
        fake_get_activity,
    )

    res = await client.get(f"/v1/contacts/activity/{CONTACT_ID}/")
    body = assert_success(res, 200)
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_contact_activity_empty_no_data(monkeypatch, client):
    """GET /contacts/activity/{id}/ returns no_data when total is zero."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_get_activity(_self, *, contact_id: str, limit: int, offset: int):
        del _self, contact_id, limit, offset
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_contact_activity",
        fake_get_activity,
    )

    res = await client.get(f"/v1/contacts/activity/{CONTACT_ID}/")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_contact_units(monkeypatch, client):
    """GET /contacts/{id}/units lists unit assignments."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_list_units(_self, *, contact_id: str, statuses=None):
        del _self, contact_id, statuses
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service."
        "ContactUnitsService.list_contact_units",
        fake_list_units,
    )

    res = await client.get(f"/v1/contacts/{CONTACT_ID}/units")
    body = assert_success(res, 200)
    assert body["data"] == []


@pytest.mark.asyncio
async def test_list_contact_vehicles(monkeypatch, client):
    """GET /contacts/{id}/vehicles lists vehicles."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_list_vehicles(_self, *, contact_id: str, unit_id=None):
        del _self, contact_id, unit_id
        return []

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.list_vehicles",
        fake_list_vehicles,
    )

    res = await client.get(f"/v1/contacts/{CONTACT_ID}/vehicles")
    body = assert_success(res, 200)
    assert body["data"] == []


@pytest.mark.asyncio
async def test_enrich_contact(monkeypatch, client):
    """POST /contacts/{id}/enrich triggers enrichment."""
    _patch_contacts_access(monkeypatch)
    monkeypatch.setattr(
        "apps.user_service.app.api.contacts.require_client_enrichment_enabled",
        lambda: None,
    )

    async def fake_create_event(_self, **_kwargs):
        del _self
        return {"event_type": "contacts.enrichment_requested"}

    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_event,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service."
        "ContactsService.trigger_enrichment_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.post(f"/v1/contacts/{CONTACT_ID}/enrich")
    assert_success(res, 202)


@pytest.mark.asyncio
async def test_assign_unit(monkeypatch, client):
    """POST /contacts/{id}/units assigns a unit."""
    _patch_contacts_access(monkeypatch)

    async def fake_assign_unit(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {"id": "cu-1", "unit_id": UNIT_ID, "contact_id": CONTACT_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_units_service."
        "ContactUnitsService.admin_assign_unit",
        fake_assign_unit,
    )

    res = await client.post(
        f"/v1/contacts/{CONTACT_ID}/units",
        json={"unit_id": UNIT_ID},
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_create_contact_vehicle(monkeypatch, client):
    """POST /contacts/{id}/vehicles registers a vehicle."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_create_vehicle(_self, *, contact_id: str, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return {"id": "veh-1", "registration_number": "MH01AB1234"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.vehicles_service.VehiclesService.create_vehicle",
        fake_create_vehicle,
    )

    res = await client.post(
        f"/v1/contacts/{CONTACT_ID}/vehicles",
        json={
            "unit_id": UNIT_ID,
            "vehicle_type": "four_wheeler",
            "registration_number": "MH01AB1234",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_list_contact_household(monkeypatch, client):
    """GET /contacts/{id}/household lists members."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_list_household(_self, *, contact_id: str, unit_id=None):
        del _self, unit_id
        assert contact_id == CONTACT_ID
        return [{"id": "member-1", "first_name": "Sam"}]

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.list_household",
        fake_list_household,
    )

    res = await client.get(f"/v1/contacts/{CONTACT_ID}/household")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "member-1"


@pytest.mark.asyncio
async def test_add_household_member(monkeypatch, client):
    """POST /contacts/{id}/household adds a member."""
    _patch_contacts_access(monkeypatch)

    async def fake_get_details(_self, *, contact_id: str):
        del _self
        assert contact_id == CONTACT_ID
        return _FAKE_DETAILS

    async def fake_add_member(_self, *, primary_contact_id: str, body):
        del _self, body
        assert primary_contact_id == CONTACT_ID
        return {"id": "member-1", "first_name": "Sam"}

    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.ContactsService.get_contact_details",
        fake_get_details,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service."
        "ContactOnboardingService.add_household_member",
        fake_add_member,
    )

    res = await client.post(
        f"/v1/contacts/{CONTACT_ID}/household",
        json={
            "unit_id": UNIT_ID,
            "first_name": "Sam",
            "phones": [
                {
                    "phone_number": "5551234567",
                    "phone_isd_code": "+1",
                    "is_primary": True,
                }
            ],
            "relationship": "child",
        },
    )
    assert_success(res, 201)
