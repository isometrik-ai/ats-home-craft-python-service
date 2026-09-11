"""Unit tests for household pets service."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums.pets import PetGender, PetVaccinationStatus
from apps.user_service.app.schemas.pets import (
    CreatePetRequest,
    RemovePetRequest,
    UpdatePetRequest,
)
from apps.user_service.app.services.pets_service import PetsService
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _service() -> PetsService:
    """Build PetsService with mocked dependencies."""
    svc = PetsService(db_connection=MagicMock(), user_context=MagicMock())
    svc.user_context.organization_id = "org-1"
    svc.repo = AsyncMock()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(
        return_value={
            "id": "unit-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "unit_code": "A-101",
            "unit_label": "Tower A - 101",
        }
    )
    return svc


def _pet_row(**overrides):
    base = {
        "id": "pet-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "unit_id": "unit-1",
        "created_by_contact_id": "contact-1",
        "name": "Romeo",
        "pet_type": "Dog",
        "breed": "Golden Retriever",
        "gender": "male",
        "date_of_birth": date(2020, 2, 1),
        "vaccination_status": "completely",
        "photo_paths": ["org/pets/romeo.jpg"],
        "status": "active",
        "sort_order": 0,
        "created_at": "2026-09-10T12:00:00Z",
        "updated_at": "2026-09-10T12:00:00Z",
        "creator_prefix": "Mr.",
        "creator_first_name": "Ajay",
        "creator_last_name": "Thakur",
        "creator_profile_photo_url": "org/contacts/ajay.jpg",
        "creator_relationship": "self",
        "unit_code": "A-101",
        "unit_label": "Tower A - 101",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_pet_sets_created_by_and_validates_catalog():
    """Create stores canonical catalog names and creator contact."""
    svc = _service()
    svc.repo.create.return_value = _pet_row()
    svc.repo.get_by_id.return_value = _pet_row()

    body = CreatePetRequest(
        unit_id="unit-1",
        name="Romeo",
        pet_type="dog",
        breed="golden retriever",
        vaccination_status=PetVaccinationStatus.COMPLETELY,
        gender=PetGender.MALE,
        photo_paths=["org/pets/romeo.jpg"],
    )
    result = await svc.create_pet(contact_id="contact-1", body=body)

    svc.repo.create.assert_awaited_once()
    kwargs = svc.repo.create.await_args.kwargs
    assert kwargs["created_by_contact_id"] == "contact-1"
    assert kwargs["pet_type"] == "Dog"
    assert kwargs["breed"] == "Golden Retriever"
    assert result["created_by"]["display_name"] == "Mr. Ajay Thakur"
    assert result["primary_photo_path"] == "org/pets/romeo.jpg"


@pytest.mark.asyncio
async def test_create_pet_rejects_future_dob():
    """Date of birth cannot be in the future."""
    svc = _service()
    body = CreatePetRequest(
        unit_id="unit-1",
        name="Romeo",
        pet_type="Dog",
        breed="Golden Retriever",
        vaccination_status=PetVaccinationStatus.COMPLETELY,
        date_of_birth=date(2099, 1, 1),
    )

    with pytest.raises(ValidationException):
        await svc.create_pet(contact_id="contact-1", body=body)


@pytest.mark.asyncio
async def test_list_pets_requires_unit_access():
    """List rejects units the contact does not occupy."""
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc.list_pets(contact_id="contact-1", unit_id="unit-1")


@pytest.mark.asyncio
async def test_get_pet_detail_includes_household_members():
    """Detail attaches household members from onboarding service."""
    svc = _service()
    svc.repo.get_by_id.return_value = _pet_row()

    with patch(
        "apps.user_service.app.services.pets_service.ContactOnboardingService"
    ) as onboarding_cls:
        onboarding = onboarding_cls.return_value
        onboarding.list_household = AsyncMock(return_value=[{"contact_id": "contact-2"}])
        result = await svc.get_pet_detail(
            contact_id="contact-1",
            pet_id="pet-1",
            unit_id="unit-1",
        )

    assert result["household_members"] == [{"contact_id": "contact-2"}]


@pytest.mark.asyncio
async def test_remove_pet_soft_deletes_with_reason():
    """Remove calls repository soft_remove."""
    svc = _service()
    svc.repo.get_by_id.return_value = _pet_row()
    svc.repo.soft_remove.return_value = _pet_row(status="removed")

    body = RemovePetRequest(reason="Adopted by another family")
    await svc.remove_pet(
        contact_id="contact-1",
        pet_id="pet-1",
        unit_id="unit-1",
        body=body,
    )

    svc.repo.soft_remove.assert_awaited_once_with(
        organization_id="org-1",
        pet_id="pet-1",
        reason="Adopted by another family",
        removed_by_contact_id="contact-1",
    )


@pytest.mark.asyncio
async def test_update_pet_not_found():
    """Update raises when pet is missing or on another unit."""
    svc = _service()
    svc.repo.get_by_id.return_value = _pet_row(unit_id="other-unit")

    with pytest.raises(NotFoundException):
        await svc.update_pet(
            contact_id="contact-1",
            pet_id="pet-1",
            unit_id="unit-1",
            body=UpdatePetRequest(name="New Name"),
        )


@pytest.mark.asyncio
async def test_create_pet_rejects_invalid_catalog():
    """Create rejects pet type/breed not in catalog."""
    svc = _service()
    body = CreatePetRequest(
        unit_id="unit-1",
        name="Romeo",
        pet_type="Dragon",
        breed="Fire",
        vaccination_status=PetVaccinationStatus.COMPLETELY,
    )

    with pytest.raises(ValidationException):
        await svc.create_pet(contact_id="contact-1", body=body)

    svc.repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_pet_detail_not_found():
    """Detail raises when pet is missing or on another unit."""
    svc = _service()
    svc.repo.get_by_id.return_value = None

    with pytest.raises(NotFoundException):
        await svc.get_pet_detail(
            contact_id="contact-1",
            pet_id="missing-pet",
            unit_id="unit-1",
        )


@pytest.mark.asyncio
async def test_remove_pet_not_found():
    """Remove raises when pet is missing or on another unit."""
    svc = _service()
    svc.repo.get_by_id.return_value = _pet_row(unit_id="other-unit")

    with pytest.raises(NotFoundException):
        await svc.remove_pet(
            contact_id="contact-1",
            pet_id="pet-1",
            unit_id="unit-1",
            body=RemovePetRequest(reason="Adopted by another family"),
        )

    svc.repo.soft_remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_pet_rejects_future_dob():
    """Update rejects a date of birth in the future."""
    svc = _service()
    svc.repo.get_by_id.return_value = _pet_row()

    with pytest.raises(ValidationException):
        await svc.update_pet(
            contact_id="contact-1",
            pet_id="pet-1",
            unit_id="unit-1",
            body=UpdatePetRequest(date_of_birth=date(2099, 1, 1)),
        )

    svc.repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_pets_returns_serialized_rows():
    """List returns active pets for an accessible unit."""
    svc = _service()
    svc.repo.list_for_unit.return_value = [_pet_row(), _pet_row(id="pet-2", name="Luna")]

    result = await svc.list_pets(contact_id="contact-1", unit_id="unit-1")

    assert len(result) == 2
    assert result[0]["name"] == "Romeo"
    assert result[0]["created_by"]["display_name"] == "Mr. Ajay Thakur"
    assert result[1]["name"] == "Luna"
