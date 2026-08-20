"""Unit tests for unit detail helpers and service."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.units_service import (
    UnitsService,
    build_location_label,
    build_plot_unit_code,
    pick_unit_owner,
    plot_item_status_to_unit_status,
    resolve_carpet_area_sqft,
    resolve_occupancy_label,
    resolve_unit_facing,
    resolve_unit_property_type,
    serialize_unit_list_item,
)
from apps.user_service.app.utils.unit_list_serialization import (
    format_contact_display_name,
    format_primary_contact_email,
    format_primary_contact_phone,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def test_format_contact_display_name_with_prefix():
    """Display name includes prefix when present."""
    assert (
        format_contact_display_name(prefix="Mr.", first_name="Rajesh", last_name="Kapoor")
        == "Mr. Rajesh Kapoor"
    )


def test_build_plot_unit_code():
    """Plot unit codes combine config code and plot number."""
    assert build_plot_unit_code(config_code="P1", plot_no="101") == "P1-101"


def test_plot_item_status_to_unit_status():
    """Plot construction status maps to inventory unit status."""
    assert plot_item_status_to_unit_status("empty") == "vacant"
    assert plot_item_status_to_unit_status("constructed") == "vacant"
    assert plot_item_status_to_unit_status("under_construction") == "under_maintenance"


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


def test_pick_unit_owner_prefers_owner_role():
    """Owner role holder wins even when a tenant is the primary occupant."""
    residents = [
        {
            "contact_id": "owner-1",
            "contact_unit_id": "cu-owner",
            "contact_type": "Owner",
            "is_primary": False,
            "relationship": "self",
        },
        {
            "contact_id": "tenant-1",
            "contact_unit_id": "cu-tenant",
            "contact_type": "Tenant",
            "is_primary": True,
            "relationship": "self",
        },
    ]
    assert pick_unit_owner(residents)["contact_id"] == "owner-1"


def test_pick_unit_owner_prefers_primary():
    """Primary occupant selection prefers is_primary, then relationship=self."""
    residents = [
        {
            "contact_id": "c1",
            "contact_unit_id": "cu1",
            "is_primary": False,
            "relationship": "self",
        },
        {
            "contact_id": "c2",
            "contact_unit_id": "cu2",
            "is_primary": True,
            "relationship": "self",
        },
    ]
    assert pick_unit_owner(residents)["contact_id"] == "c2"


def test_pick_unit_owner_prefers_self_relationship():
    """When no primary flag, prefer relationship=self over household links."""
    residents = [
        {
            "contact_id": "c1",
            "contact_unit_id": "cu1",
            "is_primary": False,
            "relationship": "spouse",
        },
        {
            "contact_id": "c2",
            "contact_unit_id": "cu2",
            "is_primary": False,
            "relationship": "self",
        },
    ]
    assert pick_unit_owner(residents)["contact_id"] == "c2"


def test_resolve_unit_property_type_from_config_kind():
    """Property type follows unit config kind mapping."""
    assert resolve_unit_property_type({"config_kind": "apartment"}) == "residential"
    assert resolve_unit_property_type({"config_kind": "commercial"}) == "commercial"
    assert resolve_unit_property_type({"config_kind": "plot"}) == "plots"
    assert resolve_unit_property_type({"plot_item_id": "plot-1"}) == "plots"


def test_build_unit_list_owner_from_join_columns():
    """Owner summary helper maps joined owner columns."""
    from apps.user_service.app.utils.unit_list_serialization import (
        build_unit_list_owner,
    )

    owner = build_unit_list_owner(
        {
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
            "owner_emails": [{"email": "rajesh@example.com", "is_primary": True}],
        }
    )

    assert owner is not None
    assert owner["display_name"] == "Mr. Rajesh Kapoor"
    assert owner["phone"] == "+919876543210"


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
            "parking_entitlement": 2,
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
    assert item["is_sold"] is True
    assert item["parking_entitlement"] == 2


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


def test_list_item_shows_owner_and_is_sold_when_vacant_but_allotted():
    """Vacant units with an active Owner allotment remain sold in the registry."""
    from datetime import datetime, timezone

    item = serialize_unit_list_item(
        {
            "id": "unit-1",
            "code": "A-1001",
            "status": "vacant",
            "sort_order": 0,
            "owner_contact_id": "c-1",
            "owner_first_name": "Ajay",
            "owner_assigned_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
        }
    )

    assert item["status"] == "vacant"
    assert item["is_sold"] is True
    assert item["owner"]["contact_id"] == "c-1"
    assert item["assign_date"] == "2026-07-15"


def test_list_item_plot_with_non_owner_assignee():
    """Plot list rows include assigned contact even when contact_type is not Owner."""
    item = serialize_unit_list_item(
        {
            "id": "plot-unit-1",
            "code": "PLT2-V-01",
            "status": "occupied",
            "sort_order": 0,
            "resolved_property_type": "plots",
            "resolved_config_kind": "plot",
            "config_display_label": "30×50 Corner Villa Plot",
            "owner_contact_id": "c-plot",
            "owner_prefix": "Mr.",
            "owner_first_name": "Ravi",
            "owner_last_name": "Sharma",
            "owner_phones": [],
            "owner_emails": [],
        }
    )

    assert item["property_type"] == "plots"
    assert item["owner"]["contact_id"] == "c-plot"
    assert item["owner"]["display_name"] == "Mr. Ravi Sharma"


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
    assert item["is_sold"] is True
    assert item["owner"] is None
    assert item["assign_date"] is None
    assert item["parking_entitlement"] == 0


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
        "assigned_at": "2026-07-15T00:00:00+00:00",
    }
    service.units_repo.count_unit_vehicles.return_value = (1, 1)
    service.invoices_repo = AsyncMock()
    service.invoices_repo.sum_outstanding_by_unit.return_value = 0
    service.invoices_repo.latest_monthly_fee_by_unit.return_value = 300000

    mock_docs_service = MagicMock()
    mock_docs_service.list_documents_for_owner_contact_unit = AsyncMock(return_value=[])

    with patch(
        "apps.user_service.app.services.units_service.ContactUnitDocumentsService",
        return_value=mock_docs_service,
    ):
        data = await service.get_unit_detail(project_id="proj-1", unit_id="unit-1")

    assert data["code"] == "A-1802"
    assert data["documents"] == []
    assert data["occupancy_label"] == "sold"
    assert data["owner"]["display_name"] == "Mr. Rajesh Kapoor"
    assert data["owner"]["phone"] == "+919876543210"
    assert data["owner"]["email"] == "rajesh@example.com"
    assert data["owner"]["assign_date"] == "2026-07-15"
    assert data["assign_date"] == "2026-07-15"
    assert data["location_label"] == "Tower A · F18"
    assert data["carpet_area_sqft"] == 1080.0
    assert data["parking_entitlement"] == 2
    assert data["vehicles_count"] == 1
    assert data["financials"]["base_fee_monthly"] == 3000.0
    assert data["financials"]["outstanding_amount"] == 0.0


class _FakeUnitsRepo:
    """In-memory fake UnitsRepository."""

    def __init__(self):
        self.units: list[dict[str, Any]] = []
        self.zones: list[dict[str, Any]] = []

    async def insert_unit(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a unit row."""
        row = {"id": f"unit-{len(self.units) + 1}", **data}
        self.units.append(row)
        return row

    async def bulk_insert_units(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert many unit rows."""
        inserted: list[dict[str, Any]] = []
        for data in rows:
            row = {"id": f"unit-{len(self.units) + 1}", **data}
            self.units.append(row)
            inserted.append(row)
        return inserted

    async def list_units(self, **_kwargs) -> tuple[list[dict[str, Any]], int]:
        """Return all units and total count."""
        return self.units, len(self.units)

    async def get_unit(self, **_kwargs) -> dict[str, Any] | None:
        """Return first unit."""
        return self.units[0] if self.units else None

    async def update_unit(self, **_kwargs) -> dict[str, Any]:
        """Return updated unit."""
        return self.units[0] if self.units else {}

    async def delete_unit(self, **_kwargs) -> None:
        """Remove all units."""
        self.units = []

    async def insert_parking_zone(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert parking zone."""
        row = {"id": "zone-1", **data}
        self.zones.append(row)
        return row

    async def list_parking_zones(self, **_kwargs) -> list[dict[str, Any]]:
        """Return parking zones."""
        return self.zones

    async def delete_parking_zone(self, **_kwargs) -> dict[str, Any] | None:
        """Delete first zone."""
        if not self.zones:
            return None
        zone = self.zones.pop(0)
        return zone


class _FakeProjectsRepo:
    """Minimal fake for units_count recompute."""

    async def recompute_units_count(self, **_kwargs) -> None:
        """No-op recompute."""


def _units_service(*, units_repo: _FakeUnitsRepo | None = None) -> UnitsService:
    """Build UnitsService with fake repos."""
    service = UnitsService.__new__(UnitsService)
    service.db_connection = MagicMock()
    service.user_context = MagicMock()
    service.user_context.organization_id = "org-1"
    service.units_repo = units_repo or _FakeUnitsRepo()
    service.projects_repo = _FakeProjectsRepo()
    service.invoices_repo = AsyncMock()
    service.setup_service = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_create_unit_recounts_project():
    """Create unit inserts row and triggers recount."""
    from apps.user_service.app.schemas.enums import UnitStatus
    from apps.user_service.app.schemas.project_inventory import CreateUnitRequest

    repo = _FakeUnitsRepo()
    service = _units_service(units_repo=repo)
    body = CreateUnitRequest(code="A-101", status=UnitStatus.VACANT)

    result = await service.create_unit(project_id="proj-1", body=body)

    assert result["code"] == "A-101"
    assert len(repo.units) == 1


@pytest.mark.asyncio
async def test_create_units_bulk_inserts_all_and_recounts_once():
    """Bulk create inserts all rows and recomputes units_count once."""
    from apps.user_service.app.schemas.enums import UnitStatus
    from apps.user_service.app.schemas.project_inventory import (
        BulkCreateUnitsRequest,
        CreateUnitRequest,
    )

    repo = _FakeUnitsRepo()
    service = _units_service(units_repo=repo)
    body = BulkCreateUnitsRequest(
        units=[
            CreateUnitRequest(code="A-101", status=UnitStatus.VACANT),
            CreateUnitRequest(code="A-102", status=UnitStatus.VACANT),
        ]
    )

    result = await service.create_units_bulk(project_id="proj-1", body=body)

    assert result["created_count"] == 2
    assert len(result["items"]) == 2
    assert len(repo.units) == 2


def test_bulk_create_units_request_rejects_duplicate_codes():
    """Bulk payload rejects duplicate codes before hitting the database."""
    from apps.user_service.app.schemas.project_inventory import (
        BulkCreateUnitsRequest,
        CreateUnitRequest,
    )

    with pytest.raises(ValueError, match="unique"):
        BulkCreateUnitsRequest(
            units=[
                CreateUnitRequest(code="A-101"),
                CreateUnitRequest(code="a-101"),
            ]
        )


@pytest.mark.asyncio
async def test_list_units_returns_serialized_rows():
    """List units maps rows through serializer."""
    from apps.user_service.app.schemas.enums import UnitStatus

    repo = _FakeUnitsRepo()
    repo.units = [
        {
            "id": "unit-1",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "code": "A-101",
            "status": UnitStatus.VACANT.value,
            "sort_order": 0,
            "is_parking": False,
            "created_at": "2026-07-16T09:00:00+00:00",
            "updated_at": "2026-07-16T10:00:00+00:00",
        }
    ]
    service = _units_service(units_repo=repo)

    result = await service.list_units(project_id="proj-1")

    assert result["items"][0]["code"] == "A-101"
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_update_unit_not_found():
    """Update raises when unit is missing."""
    from apps.user_service.app.schemas.project_inventory import UpdateUnitRequest

    service = _units_service()
    service.units_repo.get_unit = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await service.update_unit(
            project_id="proj-1",
            unit_id="missing",
            body=UpdateUnitRequest(code="B-202"),
        )


@pytest.mark.asyncio
async def test_update_unit_rejects_occupied_without_active_owner():
    """PATCH status=occupied requires an active Owner role on the unit."""
    from apps.user_service.app.schemas.enums import UnitStatus
    from apps.user_service.app.schemas.project_inventory import UpdateUnitRequest

    service = _units_service()
    service.units_repo.get_unit = AsyncMock(return_value={"id": "unit-1", "project_id": "proj-1"})
    service.units_repo.has_active_owner = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await service.update_unit(
            project_id="proj-1",
            unit_id="unit-1",
            body=UpdateUnitRequest(status=UnitStatus.OCCUPIED),
        )


@pytest.mark.asyncio
async def test_delete_unit_returns_old_data():
    """Delete unit returns serialized old_data."""
    from apps.user_service.app.schemas.enums import UnitStatus

    repo = _FakeUnitsRepo()
    repo.units = [
        {
            "id": "unit-1",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "code": "A-101",
            "status": UnitStatus.VACANT.value,
            "sort_order": 0,
            "is_parking": False,
            "created_at": "2026-07-16T09:00:00+00:00",
            "updated_at": "2026-07-16T10:00:00+00:00",
        }
    ]
    service = _units_service(units_repo=repo)

    result = await service.delete_unit(project_id="proj-1", unit_id="unit-1")

    assert result["old_data"]["code"] == "A-101"
    assert result["new_data"] is None


@pytest.mark.asyncio
async def test_create_parking_zone():
    """Create parking zone persists row."""
    from apps.user_service.app.schemas.project_inventory import CreateParkingZoneRequest

    repo = _FakeUnitsRepo()
    service = _units_service(units_repo=repo)
    body = CreateParkingZoneRequest(
        tower_id="tower-1",
        floor_id="floor-1",
        name="Basement P1",
    )

    result = await service.create_parking_zone(project_id="proj-1", body=body)

    assert result["name"] == "Basement P1"
    assert len(repo.zones) == 1


@pytest.mark.asyncio
async def test_delete_parking_zone_not_found():
    """Delete parking zone raises when missing."""
    service = _units_service()

    with pytest.raises(NotFoundException):
        await service.delete_parking_zone(project_id="proj-1", zone_id="zone-1")


@pytest.mark.asyncio
async def test_complete_floor_plans_delegates():
    """Complete step delegates to setup service."""
    service = _units_service()
    service.setup_service.complete_step = AsyncMock(return_value={"step": "done"})

    result = await service.complete_floor_plans(project_id="proj-1")

    assert result["step"] == "done"
    service.setup_service.complete_step.assert_awaited_once()
