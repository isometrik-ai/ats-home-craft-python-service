"""Integration tests for resident pets endpoints."""

import pytest

from apps.user_service.tests.integration.helpers import admin_context
from apps.user_service.tests.utils.assertions import assert_error, assert_success
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

CONTACT_ID = "contact-1"
UNIT_ID = "unit-1"

_FAKE_PET = {
    "id": "pet-1",
    "organization_id": "org-123",
    "project_id": "project-1",
    "unit_id": UNIT_ID,
    "created_by_contact_id": CONTACT_ID,
    "name": "Romeo",
    "pet_type": "Dog",
    "breed": "Golden Retriever",
    "vaccination_status": "completely",
    "photo_paths": ["org/pets/romeo.jpg"],
    "primary_photo_path": "org/pets/romeo.jpg",
    "status": "active",
    "sort_order": 0,
    "created_at": "2026-09-10T12:00:00Z",
    "updated_at": "2026-09-10T12:00:00Z",
    "created_by": {
        "contact_id": CONTACT_ID,
        "display_name": "Ajay Thakur",
        "profile_photo_url": None,
        "relationship": "self",
    },
}


def _patch_contact_context(monkeypatch) -> None:
    """Patch onboarding contact context for pets routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return admin_context(org_id="org-123"), {
            "id": CONTACT_ID,
            "roles": [{"role_type": "Owner", "status": "active"}],
        }

    monkeypatch.setattr(
        "apps.user_service.app.api.pets.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


@pytest.mark.asyncio
async def test_get_pet_catalog(monkeypatch, client):
    """GET /pets/catalog returns static catalog."""
    _patch_contact_context(monkeypatch)

    async def fake_get_catalog(_self, *, pet_type_id=None, search=None):
        del _self, pet_type_id, search
        return {"pet_types": [{"id": "dog", "name": "Dog", "breeds": []}]}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.get_catalog",
        fake_get_catalog,
    )

    response = await client.get("/v1/pets/catalog")
    payload = assert_success(response)
    assert payload["data"]["pet_types"][0]["id"] == "dog"


@pytest.mark.asyncio
async def test_list_pets(monkeypatch, client):
    """GET /pets lists pets for a unit."""
    _patch_contact_context(monkeypatch)

    async def fake_list_pets(_self, *, contact_id, unit_id, page=1, page_size=20):
        del _self
        assert contact_id == CONTACT_ID
        assert unit_id == UNIT_ID
        assert page == 1
        assert page_size == 20
        return [_FAKE_PET], 1

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.list_pets",
        fake_list_pets,
    )

    response = await client.get("/v1/pets", params={"unit_id": UNIT_ID})
    payload = assert_success(response)
    assert payload["data"][0]["name"] == "Romeo"
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 20


@pytest.mark.asyncio
async def test_create_pet(monkeypatch, client):
    """POST /pets creates a pet profile."""
    _patch_contact_context(monkeypatch)

    async def fake_create_pet(_self, *, contact_id, body):
        del _self, body
        assert contact_id == CONTACT_ID
        return _FAKE_PET

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.create_pet",
        fake_create_pet,
    )

    response = await client.post(
        "/v1/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "Romeo",
            "pet_type": "Dog",
            "breed": "Golden Retriever",
            "vaccination_status": "completely",
        },
    )
    payload = assert_success(response, status_code=201)
    assert payload["data"]["id"] == "pet-1"


@pytest.mark.asyncio
async def test_remove_pet(monkeypatch, client):
    """POST /pets/{id}/remove soft-deletes with reason."""
    _patch_contact_context(monkeypatch)

    async def fake_remove_pet(_self, *, contact_id, pet_id, unit_id, body):
        del _self, body
        assert contact_id == CONTACT_ID
        assert pet_id == "pet-1"
        assert unit_id == UNIT_ID
        return {**_FAKE_PET, "status": "removed"}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.remove_pet",
        fake_remove_pet,
    )

    response = await client.post(
        "/v1/pets/pet-1/remove",
        params={"unit_id": UNIT_ID},
        json={"reason": "Adopted by another family"},
    )
    payload = assert_success(response)
    assert payload["data"]["status"] == "removed"


@pytest.mark.asyncio
async def test_get_pet_detail(monkeypatch, client):
    """GET /pets/{id} returns pet detail."""
    _patch_contact_context(monkeypatch)

    async def fake_get_pet_detail(_self, *, contact_id, pet_id, unit_id):
        del _self
        assert contact_id == CONTACT_ID
        assert pet_id == "pet-1"
        assert unit_id == UNIT_ID
        return {**_FAKE_PET, "household_members": []}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.get_pet_detail",
        fake_get_pet_detail,
    )

    response = await client.get(
        "/v1/pets/pet-1",
        params={"unit_id": UNIT_ID},
    )
    payload = assert_success(response)
    assert payload["data"]["id"] == "pet-1"
    assert payload["data"]["household_members"] == []


@pytest.mark.asyncio
async def test_update_pet(monkeypatch, client):
    """PATCH /pets/{id} updates a pet profile."""
    _patch_contact_context(monkeypatch)

    async def fake_update_pet(_self, *, contact_id, pet_id, unit_id, body):
        del _self, body
        assert contact_id == CONTACT_ID
        assert pet_id == "pet-1"
        assert unit_id == UNIT_ID
        return {**_FAKE_PET, "name": "Romeo Jr."}

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.update_pet",
        fake_update_pet,
    )

    response = await client.patch(
        "/v1/pets/pet-1",
        params={"unit_id": UNIT_ID},
        json={"name": "Romeo Jr."},
    )
    payload = assert_success(response)
    assert payload["data"]["name"] == "Romeo Jr."


@pytest.mark.asyncio
async def test_create_pet_validation_error(monkeypatch, client):
    """POST /pets returns 422 when required fields are missing."""
    _patch_contact_context(monkeypatch)

    response = await client.post(
        "/v1/pets",
        json={"unit_id": UNIT_ID, "name": "Romeo"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_pet_invalid_catalog(monkeypatch, client):
    """POST /pets returns 422 when catalog validation fails."""
    _patch_contact_context(monkeypatch)

    async def fake_create_pet(_self, *, contact_id, body):
        del _self, contact_id, body
        raise ValidationException(
            message_key="pets.errors.invalid_pet_type",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.create_pet",
        fake_create_pet,
    )

    response = await client.post(
        "/v1/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "Romeo",
            "pet_type": "Dragon",
            "breed": "Fire",
            "vaccination_status": "completely",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_pet_detail_not_found(monkeypatch, client):
    """GET /pets/{id} returns 404 when pet is missing."""
    _patch_contact_context(monkeypatch)

    async def fake_get_pet_detail(_self, *, contact_id, pet_id, unit_id):
        del _self, contact_id, pet_id, unit_id
        raise NotFoundException(
            message_key="pets.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.get_pet_detail",
        fake_get_pet_detail,
    )

    response = await client.get(
        "/v1/pets/missing-pet",
        params={"unit_id": UNIT_ID},
    )
    assert_error(response, status_code=404)


@pytest.mark.asyncio
async def test_list_pets_unit_not_assigned(monkeypatch, client):
    """GET /pets returns 422 when contact lacks unit access."""
    _patch_contact_context(monkeypatch)

    async def fake_list_pets(_self, *, contact_id, unit_id, page=1, page_size=20):
        del _self, contact_id, unit_id, page, page_size
        raise ValidationException(
            message_key="contact_onboarding.errors.unit_not_assigned",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(
        "apps.user_service.app.services.pets_service.PetsService.list_pets",
        fake_list_pets,
    )

    response = await client.get("/v1/pets", params={"unit_id": UNIT_ID})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_pet_whitespace_name_rejected(monkeypatch, client):
    """POST /pets returns 422 when name is whitespace only."""
    _patch_contact_context(monkeypatch)

    response = await client.post(
        "/v1/pets",
        json={
            "unit_id": UNIT_ID,
            "name": "   ",
            "pet_type": "Dog",
            "breed": "Golden Retriever",
            "vaccination_status": "completely",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_remove_pet_reason_too_short(monkeypatch, client):
    """POST /pets/{id}/remove returns 422 when reason is too short."""
    _patch_contact_context(monkeypatch)

    response = await client.post(
        "/v1/pets/pet-1/remove",
        params={"unit_id": UNIT_ID},
        json={"reason": "No"},
    )
    assert response.status_code == 422
