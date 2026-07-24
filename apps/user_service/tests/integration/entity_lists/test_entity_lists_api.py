"""Integration tests for entity lists endpoints."""

import pytest

from apps.user_service.app.schemas.enums import EntityType
from apps.user_service.tests.integration.helpers import (
    admin_context,
    patch_check_permissions,
)
from apps.user_service.tests.utils.assertions import assert_success

LIST_ID = "list-1"

_FAKE_LIST_SUMMARY = {
    "id": LIST_ID,
    "organization_id": "org-123",
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


def _patch_list_access(monkeypatch) -> None:
    """Bypass RBAC and list-level permission checks."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.entity_lists")

    async def fake_require_list_permission(**kwargs):
        del kwargs
        return admin_context(), EntityType.CONTACT

    monkeypatch.setattr(
        "apps.user_service.app.api.entity_lists.EntityListsService.require_list_permission",
        fake_require_list_permission,
    )


@pytest.mark.asyncio
async def test_create_entity_list(monkeypatch, client):
    """POST /lists creates an entity list."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.entity_lists")

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
        "/v1/lists",
        json={"name": "VIP Contacts", "entity_type": "contact"},
    )
    body = assert_success(res, 201)
    assert body["data"]["id"] == LIST_ID


@pytest.mark.asyncio
async def test_list_entity_lists(monkeypatch, client):
    """GET /lists returns paginated lists."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.entity_lists")

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
        assert entity_type == EntityType.CONTACT
        assert limit == 20
        assert offset == 0
        return [_FAKE_LIST_SUMMARY], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.list_lists",
        fake_list_lists,
    )

    res = await client.get(
        "/v1/lists",
        params={"entity_type": "contact", "page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == LIST_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_list_entity_lists_empty(monkeypatch, client):
    """GET /lists returns empty collection."""

    patch_check_permissions(monkeypatch, "apps.user_service.app.api.entity_lists")

    async def fake_list_lists(_self, **kwargs):
        del _self, kwargs
        return [], 0

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.list_lists",
        fake_list_lists,
    )

    res = await client.get(
        "/v1/lists",
        params={"entity_type": "contact", "page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_entity_list_details(monkeypatch, client):
    """GET /lists/{list_id} returns list details."""

    _patch_list_access(monkeypatch)

    async def fake_get_details(_self, *, list_id: str):
        del _self
        assert list_id == LIST_ID
        return _FAKE_LIST_DETAILS

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.get_list_details",
        fake_get_details,
    )

    res = await client.get(f"/v1/lists/{LIST_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == LIST_ID
    assert body["data"]["name"] == "VIP Contacts"


@pytest.mark.asyncio
async def test_update_entity_list(monkeypatch, client):
    """PATCH /lists/{list_id} updates list metadata."""

    _patch_list_access(monkeypatch)

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
        f"/v1/lists/{LIST_ID}",
        json={"name": "Updated List"},
    )
    assert_success(res, 200)


@pytest.mark.asyncio
async def test_delete_entity_list(monkeypatch, client):
    """DELETE /lists/{list_id} soft-deletes a list."""

    _patch_list_access(monkeypatch)

    async def fake_soft_delete(_self, *, list_id: str):
        del _self
        assert list_id == LIST_ID

    monkeypatch.setattr(
        "apps.user_service.app.services.entity_lists_service.EntityListsService.soft_delete",
        fake_soft_delete,
    )

    res = await client.delete(f"/v1/lists/{LIST_ID}")
    assert_success(res, 200)
