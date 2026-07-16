"""Unit tests for unit detail helpers and service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.units_service import (
    UnitsService,
    build_location_label,
    format_contact_display_name,
    pick_unit_owner,
    resolve_carpet_area_sqft,
    resolve_occupancy_label,
    resolve_unit_facing,
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
    service.units_repo.count_unit_vehicles.return_value = (1, 1)

    data = await service.get_unit_detail(project_id="proj-1", unit_id="unit-1")

    assert data["code"] == "A-1802"
    assert data["occupancy_label"] == "sold"
    assert data["owner"]["display_name"] == "Mr. Rajesh Kapoor"
    assert data["location_label"] == "Tower A · F18"
    assert data["carpet_area_sqft"] == 1080.0
    assert data["parking_entitlement"] == 2
    assert data["vehicles_count"] == 1
