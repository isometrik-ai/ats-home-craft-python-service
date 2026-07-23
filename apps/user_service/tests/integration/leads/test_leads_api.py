"""Integration tests for leads endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import patch_check_permissions
from apps.user_service.tests.utils.assertions import assert_success

LEAD_ID = "lead-1"
STAGE_ID = "stage-1"

_FAKE_LEAD = {
    "id": LEAD_ID,
    "name": "Acme Opportunity",
    "stage_id": STAGE_ID,
    "owner_id": "test-user-id",
    "contacts": [],
    "companies": [],
}

_FAKE_LEAD_LIST_ITEM = {
    "id": LEAD_ID,
    "name": "Acme Opportunity",
    "stage_id": STAGE_ID,
    "contacts": [],
    "companies": [],
}


def _patch_leads_access(monkeypatch) -> None:
    """Bypass RBAC and system-lead visibility checks."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.leads")

    async def fake_check_user_access_async(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.user_service.app.api.leads.check_user_access_async",
        fake_check_user_access_async,
    )


@pytest.mark.asyncio
async def test_list_leads(monkeypatch, client):
    """POST /leads/list returns paginated leads."""

    _patch_leads_access(monkeypatch)

    async def fake_list_leads(_self, params, *, owner_id=None, dropdown_filters=None):
        del _self, owner_id, dropdown_filters
        assert params.mode.value == "list"
        assert params.page == 1
        assert params.limit == 20
        return ([_FAKE_LEAD_LIST_ITEM], 1, 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.list_leads",
        fake_list_leads,
    )

    res = await client.post(
        "/v1/leads/list",
        json={"mode": "list", "page": 1, "limit": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == LEAD_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_leads_empty(monkeypatch, client):
    """POST /leads/list returns empty collection."""

    _patch_leads_access(monkeypatch)

    async def fake_list_leads(_self, params, *, owner_id=None, dropdown_filters=None):
        del _self, params, owner_id, dropdown_filters
        return ([], 0, 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.list_leads",
        fake_list_leads,
    )

    res = await client.post(
        "/v1/leads/list",
        json={"mode": "list", "page": 1, "limit": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_lead(monkeypatch, client):
    """GET /leads/{lead_id} returns lead detail."""

    _patch_leads_access(monkeypatch)

    async def fake_get_lead(_self, lead_id, *, owner_id=None):
        del _self, owner_id
        assert lead_id == LEAD_ID
        return _FAKE_LEAD

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.get_lead",
        fake_get_lead,
    )

    res = await client.get(f"/v1/leads/{LEAD_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == LEAD_ID
    assert body["data"]["name"] == "Acme Opportunity"


@pytest.mark.asyncio
async def test_update_lead(monkeypatch, client):
    """PATCH /leads/{lead_id} updates a lead."""

    _patch_leads_access(monkeypatch)

    async def fake_update_lead(_self, *, lead_id: str, body):
        del _self
        assert lead_id == LEAD_ID
        assert body.name == "Updated Opportunity"
        return _FAKE_LEAD, {**_FAKE_LEAD, "name": "Updated Opportunity"}

    async def fake_create_lifecycle_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-1", "aggregate_id": LEAD_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.update_lead",
        fake_update_lead,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
    )

    res = await client.patch(
        f"/v1/leads/{LEAD_ID}",
        json={"name": "Updated Opportunity"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_lead(monkeypatch, client):
    """DELETE /leads/{lead_id} hard-deletes a lead."""

    _patch_leads_access(monkeypatch)

    async def fake_delete_lead(_self, lead_id):
        del _self
        assert lead_id == LEAD_ID
        return _FAKE_LEAD

    async def fake_create_lifecycle_event(_self, **_kwargs):
        del _self
        return {"event_id": "evt-2", "aggregate_id": LEAD_ID}

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.delete_lead",
        fake_delete_lead,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.create_lifecycle_event",
        fake_create_lifecycle_event,
    )

    res = await client.delete(f"/v1/leads/{LEAD_ID}")
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_create_lead(monkeypatch, client):
    """POST /leads creates a lead via ExternalLeadsService."""

    _patch_leads_access(monkeypatch)

    async def fake_create_lead_with_optional_contact(_self, **kwargs):
        del _self, kwargs
        return {"lead": _FAKE_LEAD, "contact": None, "company": None}

    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.create_lead_with_optional_contact",
        fake_create_lead_with_optional_contact,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.apply_create_audit_state",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.schedule_create_post_commit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.build_create_response_data",
        lambda result: result.get("lead"),
    )

    res = await client.post(
        "/v1/leads",
        json={"name": "New Opportunity", "stage_id": STAGE_ID},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == LEAD_ID


@pytest.mark.asyncio
async def test_get_lead_activity_empty(monkeypatch, client):
    """GET /leads/activity/{id} returns no-content pagination when no rows."""

    _patch_leads_access(monkeypatch)

    async def fake_get_lead(_self, lead_id, *, owner_id=None):
        del _self, owner_id
        return _FAKE_LEAD

    async def fake_get_lead_activity(_self, *, lead_id, limit, offset):
        del _self, lead_id, limit, offset
        return [], 0

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.get_lead",
        fake_get_lead,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_lead_activity",
        fake_get_lead_activity,
    )

    res = await client.get(f"/v1/leads/activity/{LEAD_ID}/")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_lead_activity_paginated(monkeypatch, client):
    """GET /leads/activity/{id} returns activity items."""

    _patch_leads_access(monkeypatch)

    async def fake_get_lead(_self, lead_id, *, owner_id=None):
        del _self, owner_id
        return _FAKE_LEAD

    async def fake_get_lead_activity(_self, *, lead_id, limit, offset):
        del _self, lead_id, offset
        assert limit == 20
        return [{"id": "act-1", "summary": "Updated stage"}], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.get_lead",
        fake_get_lead,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.activity_service.ActivityService.get_lead_activity",
        fake_get_lead_activity,
    )

    res = await client.get(f"/v1/leads/activity/{LEAD_ID}/?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "act-1"
    assert body["total"] == 1
