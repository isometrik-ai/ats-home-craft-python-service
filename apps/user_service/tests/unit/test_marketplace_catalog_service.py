"""Unit tests for the marketplace catalog."""

from __future__ import annotations

import pytest

from apps.user_service.app.services.marketplace_catalog_service import (
    MarketplaceCatalogService,
)
from apps.user_service.app.services.marketplace_geo import haversine_km
from libs.shared_utils.http_exceptions import ValidationException


def test_catalog_has_seven_categories_and_furniture_subtypes():
    catalog = MarketplaceCatalogService.get_catalog()
    names = [item["name"] for item in catalog["categories"]]
    assert names == [
        "Furniture",
        "Electronics",
        "Home decor",
        "Appliances",
        "Kids & toys",
        "Vehicles",
        "Others",
    ]
    furniture = catalog["categories"][0]
    assert furniture["slug"] == "furniture"
    assert [item["name"] for item in furniture["subtypes"]] == [
        "Tables & desks",
        "Sofas & seating",
        "Beds & mattresses",
        "Storage",
        "Outdoor",
        "Other",
    ]
    electronics = next(item for item in catalog["categories"] if item["slug"] == "electronics")
    assert [item["slug"] for item in electronics["subtypes"]] == [
        "mobiles_tablets",
        "laptops_computers",
        "tv_audio",
        "cameras",
        "other",
    ]
    assert all(item["subtypes"][-1]["slug"] == "other" for item in catalog["categories"])


def test_resolve_stores_catalog_slugs():
    category, subtype = MarketplaceCatalogService.resolve("furniture", "tables_desks")
    assert category == "furniture"
    assert subtype == "tables_desks"


def test_resolve_rejects_a_display_name():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve("Home decor", None)
    assert raised.value.message_key == "marketplace.errors.invalid_category"


def test_unknown_subtype_is_rejected():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve("electronics", "phones")
    assert raised.value.message_key == "marketplace.errors.invalid_subtype"


def test_furniture_requires_a_subtype():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve("furniture", None)
    assert raised.value.message_key == "marketplace.errors.subtype_required"


def test_labels_follow_the_catalog_name():
    category_name, subtype_name = MarketplaceCatalogService.labels("furniture", "tables_desks")
    assert category_name == "Furniture"
    assert subtype_name == "Tables & desks"


def test_parse_filter_accepts_slugs():
    category, subtype = MarketplaceCatalogService.parse_filter("electronics", None)
    assert category == "electronics"
    assert subtype is None


def test_haversine_is_zero_for_the_same_point():
    assert haversine_km(28.6, 77.2, 28.6, 77.2) == 0


def test_haversine_is_none_without_coordinates():
    assert haversine_km(None, 77.2, 28.6, 77.2) is None


def test_report_reason_catalog_matches_the_resident_sheet():
    catalog = MarketplaceCatalogService.get_report_reasons()
    assert catalog["sheet_title"] == "Report this listing"
    assert "committee" in (catalog.get("sheet_subtitle") or "").lower()
    slugs = [item["slug"] for item in catalog["reasons"]]
    assert slugs == [
        "not_allowed",
        "business_or_broker",
        "sold_but_listed",
        "something_else",
    ]
    assert catalog["reasons"][1]["name"] == "Looks like a business or broker"


def test_resolve_report_reason_accepts_catalog_slugs():
    assert MarketplaceCatalogService.resolve_report_reason("sold_but_listed") == "sold_but_listed"


def test_resolve_report_reason_rejects_unknown_values():
    with pytest.raises(ValidationException) as raised:
        MarketplaceCatalogService.resolve_report_reason("spam")
    assert raised.value.message_key == "marketplace.errors.invalid_report_reason"
