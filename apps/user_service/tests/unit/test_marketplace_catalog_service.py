"""Unit tests for the marketplace catalog."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.marketplace_catalog_service import (
    MarketplaceCatalogService,
)
from apps.user_service.app.services.marketplace_geo import haversine_km
from libs.shared_utils.http_exceptions import ValidationException


def test_catalog_has_eight_categories_and_furniture_subtypes():
    catalog = MarketplaceCatalogService.get_catalog()
    names = [item["name"] for item in catalog["categories"]]
    assert names == [
        "Furniture",
        "Electronics",
        "Home decor",
        "Appliances",
        "Kids & toys",
        "Vehicles",
        "Services",
        "Others",
    ]
    furniture = catalog["categories"][0]
    assert [item["name"] for item in furniture["subtypes"]] == [
        "Tables & desks",
        "Sofas & seating",
        "Beds & mattresses",
        "Storage",
        "Outdoor",
    ]


def test_resolve_furniture_subtype():
    category, subtype = MarketplaceCatalogService.resolve("furniture", "tables & desks")
    assert category == "Furniture"
    assert subtype == "Tables & desks"


def test_electronics_rejects_a_subtype():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve("Electronics", "Phones")
    assert raised.value.message_key == "marketplace.errors.invalid_subtype"


def test_furniture_requires_a_subtype():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve("Furniture", None)
    assert raised.value.message_key == "marketplace.errors.subtype_required"


def test_haversine_is_zero_for_the_same_point():
    assert haversine_km(28.6, 77.2, 28.6, 77.2) == 0


def test_haversine_is_none_without_coordinates():
    assert haversine_km(None, 77.2, 28.6, 77.2) is None
