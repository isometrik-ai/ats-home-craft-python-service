"""Integration tests for external entity lists endpoints."""

import pytest
from fastapi import Request

from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.main import app
from apps.user_service.tests.utils.assertions import assert_success

ORG_ID = "org-123"
LIST_ID = "list-1"

_FAKE_LIST_SUMMARY = {
    "id": LIST_ID,
    "organization_id": ORG_ID,
    "name": "VIP Contacts",
    "entity_type": "contact",
    "status": "active",
    "description": None,
    "tags": [],
    "total_items": 2,
    "enriched": 1,
    "pending": 1,
    "failed": 0,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_FAKE_LIST_DETAILS = {
    **_FAKE_LIST_SUMMARY,
    "member_ids": ["contact-1", "contact-2"],
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
async def test_external_create_list(monkeypatch, client, external_org_context):
    """POST /integrations/lists creates a list."""

    async def fake_create_list(_self, body):
        del _self
        assert body.name == "VIP Contacts"
        assert body.entity_type.value == "contact"
        return {"list": {"id": LIST_ID}, "members": {"added": 0}}

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.create_list",
        fake_create_list,
    )

    res = await client.post(
        "/v1/integrations/lists",
        json={"name": "VIP Contacts", "entity_type": "contact"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == LIST_ID


@pytest.mark.asyncio
async def test_external_list_lists(monkeypatch, client, external_org_context):
    """GET /integrations/lists returns paginated lists."""

    async def fake_list_lists(
        _self,
        *,
        entity_type,
        status=None,
        search=None,
        limit=20,
        offset=0,
    ):
        del _self, status, search
        assert entity_type.value == "contact"
        assert limit == 20
        assert offset == 0
        return ([_FAKE_LIST_SUMMARY], 1)

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.list_lists",
        fake_list_lists,
    )

    res = await client.get(
        "/v1/integrations/lists",
        params={"entity_type": "contact", "page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == LIST_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_external_get_list(monkeypatch, client, external_org_context):
    """GET /integrations/lists/{id} returns list details."""

    async def fake_get_details(_self, *, list_id: str):
        del _self
        assert list_id == LIST_ID
        return _FAKE_LIST_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.get_list_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/integrations/lists/{LIST_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == LIST_ID
    assert body["data"]["name"] == "VIP Contacts"


@pytest.mark.asyncio
async def test_external_update_list(monkeypatch, client, external_org_context):
    """PATCH /integrations/lists/{id} updates list metadata."""

    async def fake_update_list(_self, *, list_id: str, body):
        del _self
        assert list_id == LIST_ID
        assert body.name == "Updated List"
        return {**_FAKE_LIST_SUMMARY, "name": "Updated List"}

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.update_list",
        fake_update_list,
    )

    res = await client.patch(
        f"/v1/integrations/lists/{LIST_ID}",
        json={"name": "Updated List"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_external_delete_list(monkeypatch, client, external_org_context):
    """DELETE /integrations/lists/{id} soft-deletes a list."""

    async def fake_soft_delete(_self, *, list_id: str):
        del _self
        assert list_id == LIST_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.soft_delete",
        fake_soft_delete,
    )

    res = await client.delete(f"/v1/integrations/lists/{LIST_ID}")
    assert_success(res, 200)
