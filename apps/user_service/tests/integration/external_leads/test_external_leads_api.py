"""Integration tests for external leads endpoints."""

import pytest
from fastapi import Request

from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.main import app
from apps.user_service.app.schemas.leads import CreateLeadRequest
from apps.user_service.app.services.external_leads_service import (
    ExternalLeadCreateResult,
)
from apps.user_service.tests.utils.assertions import assert_success

ORG_ID = "org-123"
LEAD_ID = "lead-1"
STAGE_ID = "stage-1"

_FAKE_LEAD = {
    "id": LEAD_ID,
    "name": "Acme Opportunity",
    "stage_id": STAGE_ID,
    "owner_id": "00000000-0000-0000-0000-000000000000",
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
async def test_external_list_leads(monkeypatch, client, external_org_context):
    """GET /integrations/leads lists leads."""

    async def fake_list_leads(_self, params, *, owner_id=None, dropdown_filters=None):
        del _self, owner_id, dropdown_filters
        assert params.page == 1
        assert params.limit == 20
        return ([_FAKE_LEAD_LIST_ITEM], 1, 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.list_leads",
        fake_list_leads,
    )

    res = await client.get(
        "/v1/integrations/leads",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == LEAD_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_external_get_lead(monkeypatch, client, external_org_context):
    """GET /integrations/leads/{id} returns detail."""

    async def fake_get_lead(_self, lead_id, *, owner_id=None):
        del _self, owner_id
        assert lead_id == LEAD_ID
        return _FAKE_LEAD

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.LeadService.get_lead",
        fake_get_lead,
    )

    res = await client.get(f"/v1/integrations/leads/{LEAD_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == LEAD_ID
    assert body["data"]["name"] == "Acme Opportunity"


@pytest.mark.asyncio
async def test_external_create_lead(monkeypatch, client, external_org_context):
    """POST /integrations/leads creates a lead."""

    async def fake_create_lead(_self, **kwargs):
        del _self, kwargs
        return ExternalLeadCreateResult(
            created={"id": LEAD_ID},
            lead_payload=CreateLeadRequest(name="Acme Opportunity", stage_id=STAGE_ID),
            created_contact_id=None,
            created_company_id=None,
            lead_company_id=None,
            contact_created_events=[],
            lead_created_event=None,
            lead_event_key=None,
            contact_result=None,
            company_result=None,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.create_lead_with_optional_contact",
        fake_create_lead,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.external_leads_service."
        "ExternalLeadsService.schedule_create_post_commit",
        lambda *_args, **_kwargs: None,
    )

    res = await client.post(
        "/v1/integrations/leads",
        json={"lead": {"name": "Acme Opportunity", "stage_id": STAGE_ID}},
    )
    body = assert_success(res, 201)
    assert body["data"]["lead_id"] == LEAD_ID


@pytest.mark.asyncio
async def test_external_update_lead(monkeypatch, client, external_org_context):
    """PATCH /integrations/leads/{id} updates a lead."""

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
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.patch(
        f"/v1/integrations/leads/{LEAD_ID}",
        json={"name": "Updated Opportunity"},
    )
    body = assert_success(res, 200)
    assert body["data"]["id"] == LEAD_ID


@pytest.mark.asyncio
async def test_external_delete_lead(monkeypatch, client, external_org_context):
    """DELETE /integrations/leads/{id} hard-deletes a lead."""

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
    monkeypatch.setattr(
        "apps.user_service.app.services.event_service.EventService.publish_event_background",
        lambda **_kwargs: None,
    )

    res = await client.delete(f"/v1/integrations/leads/{LEAD_ID}")
    assert_success(res, 200)
