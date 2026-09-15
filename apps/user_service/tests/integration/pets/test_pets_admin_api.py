"""Integration tests for admin project pets endpoints."""

from __future__ import annotations

import pytest

from apps.user_service.app.api import pets_admin as pets_admin_api
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

PROJECT_ID = "project-1"
PET_ID = "pet-1"
UNIT_ID = "unit-1"

_ADMIN_PET = {
    "id": PET_ID,
    "organization_id": "org-123",
    "project_id": PROJECT_ID,
    "unit_id": UNIT_ID,
    "created_by_contact_id": "owner-1",
    "name": "Romeo",
    "pet_type": "Dog",
    "breed": "Golden Retriever",
    "vaccination_status": "completely",
    "gender": "male",
    "photo_paths": [],
    "primary_photo_path": None,
    "status": "active",
    "sort_order": 0,
    "created_at": "2026-09-10T12:00:00Z",
    "updated_at": "2026-09-10T12:00:00Z",
    "created_by": {
        "contact_id": "owner-1",
        "display_name": "Ajay Thakur",
        "profile_photo_url": None,
        "relationship": "self",
    },
    "unit": {
        "id": UNIT_ID,
        "code": "A-0101",
        "unit_label": "Tower A - 101",
        "tower_id": "tower-1",
        "tower_name": "Tower A",
    },
}


def test_pets_admin_router_registered():
    """Admin router exposes project-scoped pet registry routes."""
    paths = [route.path for route in pets_admin_api.router.routes]
    assert "/projects/{project_id}/pets/summary" in paths
    assert "/projects/{project_id}/pets/catalog" in paths
    assert "/projects/{project_id}/pets" in paths
    assert "/projects/{project_id}/pets/{pet_id}" in paths
    assert any("/remove" in path for path in paths)


@pytest.mark.asyncio
async def test_get_project_pets_summary(monkeypatch, client):
    """GET /projects/{id}/pets/summary returns counts."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_summary(_self, *, project_id):
        del _self
        assert project_id == PROJECT_ID
        return {"active_count": 3, "total_count": 3}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.get_project_summary",
        fake_summary,
    )

    response = await client.get(f"/v1/projects/{PROJECT_ID}/pets/summary")
    payload = assert_success(response)
    assert payload["data"]["active_count"] == 3


@pytest.mark.asyncio
async def test_list_project_pets(monkeypatch, client):
    """GET /projects/{id}/pets lists pets with filters."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_list(_self, *, project_id, query):
        del _self
        assert project_id == PROJECT_ID
        assert query.search == "romeo"
        assert query.status.value == "active"
        return [_ADMIN_PET], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.list_pets_for_project",
        fake_list,
    )

    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/pets",
        params={"search": "romeo", "status": "active"},
    )
    payload = assert_success(response)
    assert payload["data"][0]["name"] == "Romeo"
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_create_project_pet(monkeypatch, client):
    """POST /projects/{id}/pets creates a pet."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_create(_self, *, project_id, body):
        del _self, body
        assert project_id == PROJECT_ID
        return _ADMIN_PET

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.create_pet_admin",
        fake_create,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "Romeo",
            "pet_type": "Dog",
            "breed": "Golden Retriever",
            "vaccination_status": "completely",
            "gender": "male",
        },
    )
    payload = assert_success(response, status_code=201)
    assert payload["data"]["unit"]["tower_name"] == "Tower A"


@pytest.mark.asyncio
async def test_update_project_pet(monkeypatch, client):
    """PATCH /projects/{id}/pets/{pet_id} updates a pet."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_update(_self, *, project_id, pet_id, body):
        del _self, body
        assert project_id == PROJECT_ID
        assert pet_id == PET_ID
        return {**_ADMIN_PET, "name": "Luna"}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.update_pet_admin",
        fake_update,
    )

    response = await client.patch(
        f"/v1/projects/{PROJECT_ID}/pets/{PET_ID}",
        json={"name": "Luna"},
    )
    payload = assert_success(response)
    assert payload["data"]["name"] == "Luna"


@pytest.mark.asyncio
async def test_remove_project_pet(monkeypatch, client):
    """POST /projects/{id}/pets/{pet_id}/remove soft-deletes a pet."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_remove(_self, *, project_id, pet_id, body):
        del _self, body
        assert project_id == PROJECT_ID
        assert pet_id == PET_ID
        return {**_ADMIN_PET, "status": "removed"}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.remove_pet_admin",
        fake_remove,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/pets/{PET_ID}/remove",
        json={"reason": "Pet moved out"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_get_project_pet_detail_not_found(monkeypatch, client):
    """GET /projects/{id}/pets/{pet_id} returns 404 when missing."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_detail(_self, *, project_id, pet_id):
        del _self, project_id, pet_id
        raise NotFoundException(
            message_key="pets.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.get_pet_detail_admin",
        fake_detail,
    )

    response = await client.get(f"/v1/projects/{PROJECT_ID}/pets/missing-pet")
    assert_error(response, status_code=404)


@pytest.mark.asyncio
async def test_create_project_pet_invalid_catalog(monkeypatch, client):
    """POST /projects/{id}/pets rejects unknown catalog values."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_create(_self, *, project_id, body):
        del _self, project_id, body
        raise ValidationException(
            message_key="pets.errors.invalid_pet_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.create_pet_admin",
        fake_create,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "Romeo",
            "pet_type": "Dragon",
            "breed": "Golden Retriever",
            "vaccination_status": "completely",
        },
    )
    assert_error(response, status_code=422)


@pytest.mark.asyncio
async def test_create_project_pet_unit_without_owner(monkeypatch, client):
    """POST /projects/{id}/pets rejects units with no owner contact."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_create(_self, *, project_id, body):
        del _self, project_id, body
        raise ValidationException(
            message_key="pets.errors.unit_has_no_contact_for_pet",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.create_pet_admin",
        fake_create,
    )

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "Romeo",
            "pet_type": "Dog",
            "breed": "Golden Retriever",
            "vaccination_status": "completely",
        },
    )
    assert_error(response, status_code=422)


@pytest.mark.asyncio
async def test_remove_project_pet_reason_too_short(monkeypatch, client):
    """POST /projects/{id}/pets/{pet_id}/remove validates reason length."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    response = await client.post(
        f"/v1/projects/{PROJECT_ID}/pets/{PET_ID}/remove",
        json={"reason": "no"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_project_pets_by_unit_id(monkeypatch, client):
    """GET /projects/{id}/pets?unit_id= scopes list to one unit."""
    patch_ensure_staff_project_access(monkeypatch, "apps.user_service.app.api.pets_admin")

    async def fake_list(_self, *, project_id, query):
        del _self
        assert project_id == PROJECT_ID
        assert query.unit_id == UNIT_ID
        return [_ADMIN_PET], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.list_pets_for_project",
        fake_list,
    )

    response = await client.get(
        f"/v1/projects/{PROJECT_ID}/pets",
        params={"unit_id": UNIT_ID},
    )
    payload = assert_success(response)
    assert payload["data"][0]["unit_id"] == UNIT_ID
