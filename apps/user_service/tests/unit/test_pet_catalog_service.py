"""Unit tests for static pet catalog service."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.pet_catalog_service import PetCatalogService
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    """Ensure each test reads a fresh catalog."""
    PetCatalogService.clear_cache()
    yield
    PetCatalogService.clear_cache()


def test_get_catalog_includes_dog_and_breeds():
    """Catalog includes dog type with known breeds."""
    data = PetCatalogService.get_catalog()

    dog = next(item for item in data["pet_types"] if item["id"] == "dog")
    assert dog["name"] == "Dog"
    assert any(breed["name"] == "Golden Retriever" for breed in dog["breeds"])


def test_get_catalog_filters_by_pet_type_id():
    """pet_type_id returns only the requested type."""
    data = PetCatalogService.get_catalog(pet_type_id="cat")

    assert len(data["pet_types"]) == 1
    assert data["pet_types"][0]["id"] == "cat"


def test_get_catalog_unknown_type_raises():
    """Unknown pet_type_id returns 404."""
    with pytest.raises(NotFoundException):
        PetCatalogService.get_catalog(pet_type_id="missing-type")


def test_get_catalog_search_filters_breeds():
    """Search narrows breeds under matching types."""
    data = PetCatalogService.get_catalog(search="golden")

    assert len(data["pet_types"]) >= 1
    dog = next(item for item in data["pet_types"] if item["id"] == "dog")
    assert any("Golden" in breed["name"] for breed in dog["breeds"])


def test_resolve_type_and_breed_case_insensitive():
    """Validation accepts case-insensitive catalog names."""
    pet_type, breed = PetCatalogService.resolve_type_and_breed(
        pet_type="dog",
        breed="golden retriever",
    )

    assert pet_type == "Dog"
    assert breed == "Golden Retriever"


def test_resolve_invalid_type_raises():
    """Unknown pet type fails validation."""
    with pytest.raises(ValidationException):
        PetCatalogService.resolve_type_and_breed(pet_type="Dragon", breed="Fire")


def test_resolve_invalid_breed_raises():
    """Unknown breed for a valid type fails validation."""
    with pytest.raises(ValidationException):
        PetCatalogService.resolve_type_and_breed(pet_type="Dog", breed="Unknown Breed")
