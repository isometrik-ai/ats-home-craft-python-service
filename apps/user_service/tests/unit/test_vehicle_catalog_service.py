"""Unit tests for static vehicle catalog service."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.vehicle_catalog_service import VehicleCatalogService
from libs.shared_utils.http_exceptions import NotFoundException


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    """Ensure each test reads a fresh catalog."""
    VehicleCatalogService.clear_cache()
    yield
    VehicleCatalogService.clear_cache()


def test_get_catalog_returns_brands_and_colors():
    """Catalog includes brands with models and standalone colors."""
    data = VehicleCatalogService.get_catalog()

    assert len(data["brands"]) >= 4
    tata_brand = next(brand for brand in data["brands"] if brand["id"] == "tata")
    assert tata_brand["name"] == "Tata"
    assert any(model["name"] == "Nexon" for model in tata_brand["models"])
    assert any(color["name"] == "Black" for color in data["colors"])


def test_get_catalog_filters_by_brand_id():
    """brand_id returns only the requested brand."""
    data = VehicleCatalogService.get_catalog(brand_id="tata")

    assert len(data["brands"]) == 1
    assert data["brands"][0]["id"] == "tata"
    assert any(model["name"] == "Safari" for model in data["brands"][0]["models"])


def test_get_catalog_unknown_brand_raises():
    """Unknown brand_id returns 404."""
    with pytest.raises(NotFoundException):
        VehicleCatalogService.get_catalog(brand_id="missing-brand")


def test_get_catalog_search_filters_names():
    """Search narrows brands, models, and colors by substring."""
    data = VehicleCatalogService.get_catalog(search="nex")

    assert len(data["brands"]) == 1
    assert data["brands"][0]["id"] == "tata"
    assert data["brands"][0]["models"] == [{"id": "nexon", "name": "Nexon"}]
    assert data["colors"] == []


def test_get_catalog_search_matches_colors():
    """Search can return only matching colors."""
    data = VehicleCatalogService.get_catalog(search="white")

    assert data["brands"] == []
    assert data["colors"] == [{"id": "white", "name": "White"}]
