"""Unit tests for unit detail helpers and service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.units_service import (
    UnitsService,
    build_location_label,
    format_contact_display_name,
    format_primary_contact_email,
    format_primary_contact_phone,
    pick_unit_owner,
    resolve_carpet_area_sqft,
    resolve_occupancy_label,
    resolve_unit_facing,
    resolve_unit_property_type,
    serialize_unit_list_item,
)
from libs.shared_utils.http_exceptions import NotFoundException


def test_format_contact_display_name_with_prefix():
    """Display name includes prefix when present."""
    assert (
        format_contact_display_name(prefix="Mr.", first_name="Rajesh", last_name="Kapoor")
        == "Mr. Rajesh Kapoor"
    )


def test_resolve_occupancy_label_mapping():
    """Occupancy labels follow inventory sold/unsold rules."""
    assert resolve_occupancy_label("occupied") == "sold"
    assert resolve_occupancy_label("blocked") == "sold"
    assert resolve_occupancy_label("vacant") == "unsold"
    assert resolve_occupancy_label("under_maintenance") == "under_maintenance"


def test_build_location_label():
    """Location label combines tower and floor."""
    assert (
        build_location_label(
            tower_name="Tower A",
            floor_display_name="F18",
            floor_level_number=18,
        )
        == "Tower A · F18"
    )


def test_resolve_carpet_area_prefers_config_fields():
    """Area resolution prefers carpet, then apartment area, then plot size."""
    assert resolve_carpet_area_sqft({"carpet_area_sqft": 1080}) == 1080.0
    assert resolve_carpet_area_sqft({"area_sqft": 900}) == 900.0
    assert resolve_carpet_area_sqft({"plot_size_sqft": 1500}) == 1500.0


def test_resolve_unit_facing_by_kind():
    """Facing resolution depends on config kind."""
    assert resolve_unit_facing({"config_kind": "apartment", "default_facing": "east"}) == "east"
    assert resolve_unit_facing({"config_kind": "plot", "config_facing": "north"}) == "north"


def test_pick_unit_owner_prefers_primary():
    """Owner selection prefers primary, then Owner type."""
    residents = [
        {
            "contact_id": "c1",
            "contact_unit_id": "cu1",
            "is_primary": False,
            "contact_type": "Owner",
        },
        {
            "contact_id": "c2",
            "contact_unit_id": "cu2",
            "is_primary": True,
            "contact_type": "Tenant",
        },
    ]
    assert pick_unit_owner(residents)["contact_id"] == "c2"


def test_resolve_unit_property_type_from_config_kind():
    """Property type follows unit config kind mapping."""
    assert resolve_unit_property_type({"config_kind": "apartment"}) == "residential"
    assert resolve_unit_property_type({"config_kind": "commercial"}) == "commercial"
    assert resolve_unit_property_type({"config_kind": "plot"}) == "plots"
    assert resolve_unit_property_type({"plot_item_id": "plot-1"}) == "plots"


def test_serialize_unit_list_item_builds_registry_row():
    """Registry list row includes UI fields and owner summary."""
    item = serialize_unit_list_item(
        {
            "id": "unit-1",
            "project_id": "proj-1",
            "tower_id": "tower-1",
            "config_id": "cfg-1",
            "code": "A-1802",
            "unit_label": None,
            "status": "occupied",
            "sort_order": 1,
            "tower_name": "Tower A",
            "floor_display_name": "F18",
            "floor_level_number": 18,
            "resolved_property_type": "residential",
            "resolved_config_kind": "apartment",
            "config_display_label": "2BHK Standard",
            "owner_contact_id": "c-1",
            "owner_prefix": "Mr.",
            "owner_first_name": "Rajesh",
            "owner_last_name": "Kapoor",
            "owner_phones": [
                {
                    "phone_isd_code": "+91",
                    "phone_number": "9876543210",
                    "is_primary": True,
                }
            ],
            "owner_emails": [
                {"email": "rajesh@example.com", "is_primary": True},
            ],
        }
    )

    assert item["code"] == "A-1802"
    assert item["location_label"] == "Tower A · F18"
    assert item["property_type"] == "residential"
    assert item["config_kind"] == "apartment"
    assert item["config_display_label"] == "2BHK Standard"
    assert item["floor_level_number"] == 18
    assert item["status"] == "occupied"
    assert item["owner"]["display_name"] == "Mr. Rajesh Kapoor"
    assert item["owner"]["phone"] == "+919876543210"
    assert item["owner"]["email"] == "rajesh@example.com"


def test_format_primary_contact_phone_and_email():
    """Primary phone/email helpers prefer is_primary entries."""
    phones = [
        {"phone_isd_code": "+1", "phone_number": "1111111111", "is_primary": False},
        {"phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": True},
    ]
    emails = [
        {"email": "other@example.com", "is_primary": False},
        {"email": "owner@example.com", "is_primary": True},
    ]

    assert format_primary_contact_phone(phones) == "+919876543210"
    assert format_primary_contact_email(emails) == "owner@example.com"


def test_list_item_hides_owner_when_vacant():
    """Vacant units do not expose owner even when an Owner link exists in DB."""
    item = serialize_unit_list_item(
        {
            "id": "unit-1",
            "code": "A-1001",
            "status": "vacant",
            "sort_order": 0,
            "owner_contact_id": "c-1",
            "owner_first_name": "Ajay",
        }
    )

    assert item["status"] == "vacant"
    assert item["owner"] is None


def test_list_item_occupied_without_owner():
    """Sold units without an Owner contact return a null owner."""
    item = serialize_unit_list_item(
        {
            "id": "unit-1",
            "code": "A-1004",
            "status": "occupied",
            "sort_order": 3,
        }
    )

    assert item["status"] == "occupied"
    assert item["owner"] is None


@pytest.mark.asyncio
async def test_list_units_returns_paginated_payload():
    """List units returns items, total, and summary counts."""
    service = UnitsService(db_connection=MagicMock(), user_context=MagicMock())
    service.user_context.organization_id = "org-1"
    service.setup_service = AsyncMock()
    service.units_repo = AsyncMock()
    service.units_repo.list_units.return_value = (
        [
            {
                "id": "unit-1",
                "project_id": "proj-1",
                "tower_id": "tower-1",
                "config_id": "cfg-1",
                "code": "A-1802",
                "unit_label": None,
                "status": "vacant",
                "sort_order": 1,
                "tower_name": "Tower A",
                "floor_display_name": "F18",
                "floor_level_number": 18,
                "resolved_property_type": "residential",
                "resolved_config_kind": "apartment",
                "config_display_label": "2BHK Standard",
                "owner_contact_id": None,
            }
        ],
        1,
    )

    result = await service.list_units(project_id="proj-1", page=1, page_size=20)

    assert result["total"] == 1
    assert result["items"][0]["property_type"] == "residential"
    assert "summary" not in result
    service.units_repo.list_units.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_units_registry_summary():
    """Summary endpoint delegates to repository aggregate query."""
    service = UnitsService(db_connection=MagicMock(), user_context=MagicMock())
    service.user_context.organization_id = "org-1"
    service.setup_service = AsyncMock()
    service.units_repo = AsyncMock()
    service.units_repo.get_units_registry_summary.return_value = {
        "total": 75,
        "sold_count": 51,
        "unsold_count": 24,
    }

    summary = await service.get_units_registry_summary(project_id="proj-1")

    assert summary["sold_count"] == 51
    service.units_repo.get_units_registry_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_unit_detail_not_found():
    """Missing unit raises not found."""
    service = UnitsService(db_connection=MagicMock(), user_context=MagicMock())
    service.user_context.organization_id = "org-1"
    service.setup_service = AsyncMock()
    service.units_repo = AsyncMock()
    service.units_repo.get_unit_detail_base.return_value = None

    with pytest.raises(NotFoundException):
        await service.get_unit_detail(project_id="proj-1", unit_id="unit-1")


@pytest.mark.asyncio
async def test_get_unit_detail_builds_payload():
    """Service assembles unit detail from repository rows."""
    service = UnitsService(db_connection=MagicMock(), user_context=MagicMock())
    service.user_context.organization_id = "org-1"
    service.setup_service = AsyncMock()
    service.units_repo = AsyncMock()
    service.units_repo.get_unit_detail_base.return_value = {
        "id": "unit-1",
        "project_id": "proj-1",
        "tower_id": "tower-1",
        "floor_id": "floor-1",
        "config_id": "cfg-1",
        "code": "A-1802",
        "unit_label": None,
        "status": "occupied",
        "sort_order": 1,
        "is_parking": False,
        "plot_item_id": None,
        "created_at": "2026-07-16T09:00:00+00:00",
        "updated_at": "2026-07-16T10:00:00+00:00",
        "tower_name": "Tower A",
        "tower_code": "A",
        "tower_type": "residential",
        "floor_display_name": "F18",
        "floor_level_number": 18,
        "config_kind": "apartment",
        "config_name": "2BHK Standard",
        "config_code": "2BHK_STD",
        "config_display_label": "2BHK Standard",
        "bedrooms": 2,
        "bathrooms": 2,
        "area_sqft": 1080,
        "carpet_area_sqft": None,
        "parking_entitlement": 2,
        "default_facing": "east",
        "config_facing": None,
        "commercial_unit_type": None,
    }
    service.units_repo.list_unit_residents.return_value = [
        {
            "contact_unit_id": "cu-1",
            "contact_id": "c-1",
            "is_primary": True,
            "relationship": "self",
            "status": "active",
            "contact_type": "Owner",
            "prefix": "Mr.",
            "first_name": "Rajesh",
            "last_name": "Kapoor",
        }
    ]
    service.units_repo.get_unit_owner_contact.return_value = {
        "contact_unit_id": "cu-1",
        "contact_id": "c-1",
        "is_primary": True,
        "relationship": "self",
        "status": "active",
        "contact_type": "Owner",
        "prefix": "Mr.",
        "first_name": "Rajesh",
        "last_name": "Kapoor",
        "phones": [
            {
                "phone_isd_code": "+91",
                "phone_number": "9876543210",
                "is_primary": True,
            }
        ],
        "emails": [{"email": "rajesh@example.com", "is_primary": True}],
        "primary_phone": "+919876543210",
        "primary_email": "rajesh@example.com",
    }
    service.units_repo.count_unit_vehicles.return_value = (1, 1)
    service.invoices_repo = AsyncMock()
    service.invoices_repo.sum_outstanding_by_unit.return_value = 0
    service.invoices_repo.latest_monthly_fee_by_unit.return_value = 300000

    data = await service.get_unit_detail(project_id="proj-1", unit_id="unit-1")

    assert data["code"] == "A-1802"
    assert data["occupancy_label"] == "sold"
    assert data["owner"]["display_name"] == "Mr. Rajesh Kapoor"
    assert data["owner"]["phone"] == "+919876543210"
    assert data["owner"]["email"] == "rajesh@example.com"
    assert data["location_label"] == "Tower A · F18"
    assert data["carpet_area_sqft"] == 1080.0
    assert data["parking_entitlement"] == 2
    assert data["vehicles_count"] == 1
    assert data["financials"]["base_fee_monthly"] == 3000.0
    assert data["financials"]["outstanding_amount"] == 0.0
