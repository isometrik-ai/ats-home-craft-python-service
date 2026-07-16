"""Unit tests for static vehicle catalog service."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.vehicle_catalog_service import VehicleCatalogService
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    """Ensure each test reads a fresh catalog."""
    VehicleCatalogService.clear_cache()
    yield
    VehicleCatalogService.clear_cache()


def test_get_catalog_four_wheeler_brands_and_colors():
    """Four-wheeler catalog includes car brands and colors."""
    data = VehicleCatalogService.get_catalog(vehicle_type="four_wheeler")

    assert data["vehicle_type"] == "four_wheeler"
    assert len(data["brands"]) >= 4
    tata_brand = next(brand for brand in data["brands"] if brand["id"] == "tata")
    assert tata_brand["name"] == "Tata"
    assert any(model["name"] == "Nexon" for model in tata_brand["models"])
    assert any(color["name"] == "Silver" for color in data["colors"])


def test_get_catalog_two_wheeler_brands_and_colors():
    """Two-wheeler catalog includes bike/scooter brands and colors."""
    data = VehicleCatalogService.get_catalog(vehicle_type="two_wheeler")

    assert data["vehicle_type"] == "two_wheeler"
    assert len(data["brands"]) >= 4
    hero_brand = next(brand for brand in data["brands"] if brand["id"] == "hero")
    assert hero_brand["name"] == "Hero"
    assert any(model["name"] == "Splendor" for model in hero_brand["models"])
    assert any(color["name"] == "Black" for color in data["colors"])


def test_get_catalog_filters_by_brand_id():
    """brand_id returns only the requested brand."""
    data = VehicleCatalogService.get_catalog(
        vehicle_type="four_wheeler",
        brand_id="tata",
    )

    assert len(data["brands"]) == 1
    assert data["brands"][0]["id"] == "tata"
    assert any(model["name"] == "Safari" for model in data["brands"][0]["models"])


def test_get_catalog_unknown_brand_raises():
    """Unknown brand_id returns 404."""
    with pytest.raises(NotFoundException):
        VehicleCatalogService.get_catalog(vehicle_type="four_wheeler", brand_id="missing-brand")


def test_get_catalog_invalid_vehicle_type_raises():
    """Unknown vehicle_type returns validation error."""
    with pytest.raises(ValidationException):
        VehicleCatalogService.get_catalog(vehicle_type="three_wheeler")


def test_get_catalog_search_filters_names():
    """Search narrows brands, models, and colors by substring."""
    data = VehicleCatalogService.get_catalog(vehicle_type="four_wheeler", search="nex")

    assert len(data["brands"]) == 1
    assert data["brands"][0]["id"] == "tata"
    assert data["brands"][0]["models"] == [{"id": "nexon", "name": "Nexon"}]
    assert data["colors"] == []


def test_get_catalog_search_matches_two_wheeler_models():
    """Search works for two-wheeler catalog entries."""
    data = VehicleCatalogService.get_catalog(vehicle_type="two_wheeler", search="pulsar")

    assert len(data["brands"]) == 1
    assert data["brands"][0]["id"] == "bajaj"
    assert data["brands"][0]["models"] == [{"id": "pulsar", "name": "Pulsar"}]


def test_get_catalog_search_matches_colors():
    """Search can return only matching colors."""
    data = VehicleCatalogService.get_catalog(vehicle_type="four_wheeler", search="white")

    assert data["brands"] == []
    assert data["colors"] == [{"id": "white", "name": "White"}]
