"""Unit tests for vehicle withdraw and soft-remove rules."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import VehicleStatus
from apps.user_service.app.services.vehicles_service import VehiclesService
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


class _FakePushDispatcher:
    def __init__(self) -> None:
        self.org_calls: list[dict[str, Any]] = []
        self.contact_calls: list[dict[str, Any]] = []

    async def send_to_org_members(self, **kwargs):
        self.org_calls.append(kwargs)
        return 1

    async def send_to_contact(self, **kwargs):
        self.contact_calls.append(kwargs)
        return None


def _service(*, push: _FakePushDispatcher | None = None) -> VehiclesService:
    """Build VehiclesService with mocked dependencies."""
    svc = VehiclesService(db_connection=MagicMock(), user_context=MagicMock())
    svc.user_context.organization_id = "org-1"
    svc.repo = AsyncMock()
    svc.parking_slots_repo = AsyncMock()
    svc._push_dispatcher = push or _FakePushDispatcher()
    return svc


@pytest.mark.asyncio
async def test_withdraw_pending_hard_deletes():
    """Pending vehicles can be withdrawn (hard-deleted)."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.PENDING.value,
        "project_id": "p1",
    }
    svc.repo.delete.return_value = {"id": "v1"}

    await svc.withdraw_vehicle(contact_id="c1", vehicle_id="v1")

    svc.repo.delete.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="c1",
        vehicle_id="v1",
    )


@pytest.mark.asyncio
async def test_withdraw_approved_rejected():
    """Approved vehicles cannot be withdrawn."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.APPROVED.value,
    }

    with pytest.raises(ValidationException):
        await svc.withdraw_vehicle(contact_id="c1", vehicle_id="v1")

    svc.repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_pending_use_withdraw():
    """Pending vehicles must use withdraw, not DELETE remove."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.PENDING.value,
    }

    with pytest.raises(ValidationException):
        await svc.remove_vehicle(contact_id="c1", vehicle_id="v1")


@pytest.mark.asyncio
async def test_remove_approved_soft_deletes_and_releases_slot():
    """Approved vehicles are soft-removed and parking slot is released."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.APPROVED.value,
        "project_id": "p1",
        "parking_slot_id": "slot-1",
        "organization_id": "org-1",
        "contact_id": "c1",
        "unit_id": "u1",
        "vehicle_type": "four_wheeler",
        "registration_number": "ABC123",
        "photo_paths": [],
        "status_updated_at": "2026-07-16T10:00:00Z",
        "created_at": "2026-07-16T09:00:00Z",
        "updated_at": "2026-07-16T10:00:00Z",
        "sort_order": 0,
    }
    svc.repo.soft_remove.return_value = {
        **svc.repo.get_by_id.return_value,
        "status": VehicleStatus.REMOVED.value,
        "parking_slot_id": None,
    }

    result = await svc.remove_vehicle(contact_id="c1", vehicle_id="v1")

    svc.parking_slots_repo.release_slot.assert_awaited_once_with(
        organization_id="org-1",
        project_id="p1",
        slot_id="slot-1",
    )
    svc.repo.soft_remove.assert_awaited_once()
    assert result["status"] == VehicleStatus.REMOVED.value


@pytest.mark.asyncio
async def test_list_project_vehicles_includes_owner_and_unit():
    """Admin project vehicle list includes nested owner and unit summaries."""
    svc = _service()
    svc.repo.list_by_project.return_value = [
        {
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "ABC123",
            "photo_paths": [],
            "status": VehicleStatus.PENDING.value,
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
            "owner_contact_id": "owner-1",
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
            "owner_profile_photo_url": "https://cdn.example.com/owners/rajesh.jpg",
            "unit_code": "A-1802",
            "unit_label": None,
            "unit_status": "occupied",
            "unit_tower_id": "tower-1",
            "unit_config_id": "cfg-1",
            "unit_plot_item_id": None,
            "unit_sort_order": 1,
            "unit_tower_name": "Tower A",
            "unit_tower_type": "residential",
            "unit_floor_display_name": "F18",
            "unit_floor_level_number": 18,
            "unit_config_kind": "apartment",
            "unit_config_display_label": "2BHK Standard",
            "unit_config_name": "2BHK Standard",
            "unit_plot_description": None,
            "unit_resolved_property_type": "residential",
            "unit_resolved_config_kind": "apartment",
            "parking_slot_id": "slot-1",
            "parking_slot_row_id": "slot-1",
            "parking_slot_number": 12,
            "parking_slot_status": "assigned",
            "parking_facility_id": "fac-1",
            "parking_facility_name": "Basement Parking",
            "parking_facility_location_type": "tower",
            "parking_facility_floor_level": "B1",
            "parking_facility_wing": "A",
            "parking_facility_tower_id": "tower-1",
        }
    ]

    items = await svc.list_project_vehicles(project_id="p1", status=VehicleStatus.PENDING)

    assert items[0]["owner"]["contact_id"] == "owner-1"
    assert items[0]["owner"]["display_name"] == "Mr. Rajesh Kapoor"
    assert items[0]["owner"]["phone"] == "+919876543210"
    assert items[0]["owner"]["email"] == "rajesh@example.com"
    assert items[0]["owner"]["profile_photo_url"] == "https://cdn.example.com/owners/rajesh.jpg"
    assert items[0]["unit"]["id"] == "u1"
    assert items[0]["unit"]["code"] == "A-1802"
    assert items[0]["unit"]["location_label"] == "Tower A · F18"
    assert items[0]["unit"]["property_type"] == "residential"
    assert items[0]["unit"]["config_display_label"] == "2BHK Standard"
    assert items[0]["unit"]["status"] == "occupied"
    assert "owner" in items[0]
    assert "unit" in items[0]
    assert items[0]["parking_allotment"]["id"] == "slot-1"
    assert items[0]["parking_allotment"]["slot_number"] == 12
    assert items[0]["parking_allotment"]["status"] == "assigned"
    assert items[0]["parking_allotment"]["facility"]["name"] == "Basement Parking"
    assert items[0]["parking_allotment"]["facility"]["floor_level"] == "B1"
    assert "unit_code" not in items[0]
    assert "owner_contact_id" not in items[0]
    assert "parking_slot_number" not in items[0]


@pytest.mark.asyncio
async def test_vehicle_owner_unit_keys_missing():
    """Owner and unit keys are present even when join data is absent."""
    svc = _service()
    svc.repo.list_by_project.return_value = [
        {
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "ABC123",
            "photo_paths": [],
            "status": VehicleStatus.PENDING.value,
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
        }
    ]

    items = await svc.list_project_vehicles(project_id="p1")

    assert items[0]["owner"] is None
    assert items[0]["unit"]["id"] == "u1"
    assert items[0]["unit"]["code"] == ""
    assert items[0]["parking_allotment"] is None


@pytest.mark.asyncio
async def test_vehicle_owner_phone_from_json_string():
    """Owner phone/email resolve when JSONB arrives as a string from the driver."""
    svc = _service()
    svc.repo.list_by_project.return_value = [
        {
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "ABC123",
            "photo_paths": [],
            "status": VehicleStatus.PENDING.value,
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
            "owner_contact_id": "owner-1",
            "owner_prefix": "Mr.",
            "owner_first_name": "Rajesh",
            "owner_last_name": "Kapoor",
            "owner_phones": (
                '[{"phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": true}]'
            ),
            "owner_emails": '[{"email": "rajesh@example.com", "is_primary": true}]',
            "owner_primary_phone": None,
            "owner_primary_email": None,
        }
    ]

    items = await svc.list_project_vehicles(project_id="p1")

    assert items[0]["owner"]["phone"] == "+919876543210"
    assert items[0]["owner"]["email"] == "rajesh@example.com"


@pytest.mark.asyncio
async def test_vehicle_owner_phone_from_sql_extract():
    """Prefer SQL-extracted primary phone/email when present."""
    svc = _service()
    svc.repo.list_by_project.return_value = [
        {
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "ABC123",
            "photo_paths": [],
            "status": VehicleStatus.PENDING.value,
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
            "owner_contact_id": "owner-1",
            "owner_prefix": "Mr.",
            "owner_first_name": "Rajesh",
            "owner_last_name": "Kapoor",
            "owner_phones": [],
            "owner_emails": [],
            "owner_primary_phone": "+919876543210",
            "owner_primary_email": "rajesh@example.com",
        }
    ]

    items = await svc.list_project_vehicles(project_id="p1")

    assert items[0]["owner"]["phone"] == "+919876543210"
    assert items[0]["owner"]["email"] == "rajesh@example.com"


@pytest.mark.asyncio
async def test_remove_not_found():
    """Missing vehicle raises not found."""
    svc = _service()
    svc.repo.get_by_id.return_value = None

    with pytest.raises(NotFoundException):
        await svc.remove_vehicle(contact_id="c1", vehicle_id="v1")


@pytest.mark.asyncio
async def test_list_vehicles_for_contact():
    """List vehicles normalizes repository rows."""
    svc = _service()
    svc.repo.list_by_contact.return_value = [
        {
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "MH12AB1234",
            "photo_paths": [],
            "status": VehicleStatus.APPROVED.value,
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
        }
    ]

    rows = await svc.list_vehicles(contact_id="c1")

    assert rows[0]["registration_number"] == "MH12AB1234"


@pytest.mark.asyncio
async def test_create_vehicle_validates_unit():
    """Create vehicle requires active unit assignment."""
    from apps.user_service.app.schemas.contact_onboarding import CreateVehicleRequest
    from apps.user_service.app.schemas.enums import VehicleType

    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=False)

    body = CreateVehicleRequest(
        unit_id="u1",
        vehicle_type=VehicleType.FOUR_WHEELER,
        registration_number="mh12ab1234",
    )

    with pytest.raises(ValidationException):
        await svc.create_vehicle(contact_id="c1", body=body)


@pytest.mark.asyncio
async def test_create_vehicle_success():
    """Create vehicle uppercases registration and links project."""
    from apps.user_service.app.schemas.contact_onboarding import CreateVehicleRequest
    from apps.user_service.app.schemas.enums import VehicleType

    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "p1"})
    svc.repo.create.return_value = {
        "id": "v1",
        "organization_id": "org-1",
        "project_id": "p1",
        "contact_id": "c1",
        "unit_id": "u1",
        "vehicle_type": VehicleType.FOUR_WHEELER.value,
        "registration_number": "MH12AB1234",
        "photo_paths": [],
        "status": VehicleStatus.PENDING.value,
        "status_updated_at": "2026-07-16T10:00:00Z",
        "created_at": "2026-07-16T09:00:00Z",
        "updated_at": "2026-07-16T10:00:00Z",
        "sort_order": 0,
    }

    body = CreateVehicleRequest(
        unit_id="u1",
        vehicle_type=VehicleType.FOUR_WHEELER,
        registration_number="mh12ab1234",
    )
    result = await svc.create_vehicle(contact_id="c1", body=body)

    create_kwargs = svc.repo.create.await_args.kwargs
    assert create_kwargs["registration_number"] == "MH12AB1234"
    assert result["project_id"] == "p1"
    push = svc._push_dispatcher
    assert len(push.org_calls) == 1
    assert push.org_calls[0]["message_key"] == "notifications.push.vehicle.submitted"


@pytest.mark.asyncio
async def test_review_vehicle_approves_and_assigns_slot():
    """Approve assigns parking slot and updates vehicle."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project.return_value = {
        "id": "v1",
        "status": VehicleStatus.PENDING.value,
        "project_id": "p1",
        "contact_id": "c1",
    }
    svc.parking_slots_repo.get_slot.return_value = {"id": "slot-1", "status": "available"}
    svc.parking_slots_repo.assign_slot.return_value = True
    svc.repo.update_by_project.return_value = {
        "id": "v1",
        "organization_id": "org-1",
        "project_id": "p1",
        "contact_id": "c1",
        "unit_id": "u1",
        "vehicle_type": "four_wheeler",
        "registration_number": "MH12AB1234",
        "photo_paths": [],
        "status": VehicleStatus.APPROVED.value,
        "parking_slot_id": "slot-1",
        "status_updated_at": "2026-07-16T10:00:00Z",
        "created_at": "2026-07-16T09:00:00Z",
        "updated_at": "2026-07-16T10:00:00Z",
        "sort_order": 0,
    }

    body = ReviewVehicleRequest(status=VehicleStatus.APPROVED, parking_slot_id="slot-1")
    result = await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)

    svc.parking_slots_repo.assign_slot.assert_awaited_once()
    assert result["status"] == VehicleStatus.APPROVED.value
    push = svc._push_dispatcher
    assert len(push.contact_calls) == 1
    assert push.contact_calls[0]["message_key"] == "notifications.push.vehicle.approved"


@pytest.mark.asyncio
async def test_review_vehicle_rejects_pending_only():
    """Review rejects when vehicle is not pending."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project.return_value = {
        "id": "v1",
        "status": VehicleStatus.APPROVED.value,
    }
    body = ReviewVehicleRequest(status=VehicleStatus.REJECTED, rejection_reason="Invalid docs")

    with pytest.raises(ValidationException):
        await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)


@pytest.mark.asyncio
async def test_list_project_vehicles_passes_search():
    """Admin list passes search filter to repository."""
    svc = _service()
    svc.repo.list_by_project.return_value = []

    await svc.list_project_vehicles(project_id="p1", search="  A-101  ")

    svc.repo.list_by_project.assert_awaited_once_with(
        organization_id="org-1",
        project_id="p1",
        status=None,
        vehicle_type=None,
        fuel_type=None,
        search="A-101",
    )


@pytest.mark.asyncio
async def test_list_project_vehicles_filters_status():
    """Admin list passes status filter to repository."""
    svc = _service()
    svc.repo.list_by_project.return_value = []

    await svc.list_project_vehicles(project_id="p1", status=VehicleStatus.PENDING)

    svc.repo.list_by_project.assert_awaited_once_with(
        organization_id="org-1",
        project_id="p1",
        status=VehicleStatus.PENDING.value,
        vehicle_type=None,
        fuel_type=None,
        search=None,
    )


@pytest.mark.asyncio
async def test_list_project_vehicles_passes_vehicle_type_and_fuel_type():
    """Admin list passes vehicle type and fuel type filters to repository."""
    from apps.user_service.app.schemas.enums import VehicleFuelType, VehicleType

    svc = _service()
    svc.repo.list_by_project.return_value = []

    await svc.list_project_vehicles(
        project_id="p1",
        vehicle_type=VehicleType.FOUR_WHEELER,
        fuel_type=VehicleFuelType.EV,
    )

    svc.repo.list_by_project.assert_awaited_once_with(
        organization_id="org-1",
        project_id="p1",
        status=None,
        vehicle_type=VehicleType.FOUR_WHEELER.value,
        fuel_type=VehicleFuelType.EV.value,
        search=None,
    )


@pytest.mark.asyncio
async def test_complete_vehicles_step():
    """Complete vehicles step marks onboarding step done."""
    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.unit_onboarding_repo = AsyncMock()
    svc.contact_units_repo.get_owned_by_contact.return_value = {"id": "cu-1"}

    await svc.complete_vehicles_step(contact_id="c1", contact_unit_id="cu-1")

    svc.unit_onboarding_repo.complete_step.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_parking_allotment_returns_none_without_slot():
    """Rows without an assigned parking slot skip nested parking summary."""
    svc = _service()
    assert svc._build_parking_allotment({"parking_slot_id": None}) is None


@pytest.mark.asyncio
async def test_build_vehicle_unit_returns_none_without_unit_id():
    """Rows without unit_id skip nested unit serialization."""
    svc = _service()
    assert svc._build_vehicle_unit({"unit_id": None}) is None


@pytest.mark.asyncio
async def test_create_vehicle_unit_not_found():
    """Create vehicle fails when unit project lookup misses."""
    from apps.user_service.app.schemas.contact_onboarding import CreateVehicleRequest
    from apps.user_service.app.schemas.enums import VehicleType

    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value=None)

    body = CreateVehicleRequest(
        unit_id="u1",
        vehicle_type=VehicleType.FOUR_WHEELER,
        registration_number="mh12ab1234",
    )

    with pytest.raises(NotFoundException):
        await svc.create_vehicle(contact_id="c1", body=body)


@pytest.mark.asyncio
async def test_create_vehicle_duplicate_registration():
    """Duplicate registration raises conflict."""
    from asyncpg.exceptions import UniqueViolationError

    from apps.user_service.app.schemas.contact_onboarding import CreateVehicleRequest
    from apps.user_service.app.schemas.enums import VehicleType

    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "p1"})
    svc.repo.create = AsyncMock(side_effect=UniqueViolationError("duplicate"))

    body = CreateVehicleRequest(
        unit_id="u1",
        vehicle_type=VehicleType.FOUR_WHEELER,
        registration_number="mh12ab1234",
    )

    with pytest.raises(ConflictException):
        await svc.create_vehicle(contact_id="c1", body=body)


@pytest.mark.asyncio
async def test_list_vehicles_with_unit_filter():
    """Listing by unit validates assignment first."""
    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": "p1"})
    svc.repo.list_by_contact = AsyncMock(return_value=[])

    await svc.list_vehicles(contact_id="c1", unit_id="u1")

    svc.contact_units_repo.contact_has_active_unit.assert_awaited_once()
    svc.repo.list_by_contact.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="c1",
        unit_id="u1",
    )


@pytest.mark.asyncio
async def test_update_vehicle_success_and_duplicate():
    """Update vehicle normalizes enums and maps duplicate registration."""
    from asyncpg.exceptions import UniqueViolationError

    from apps.user_service.app.schemas.contact_onboarding import UpdateVehicleRequest
    from apps.user_service.app.schemas.enums import VehicleFuelType, VehicleType

    svc = _service()
    svc.repo.update = AsyncMock(
        side_effect=[
            UniqueViolationError("duplicate"),
            {
                "id": "v1",
                "organization_id": "org-1",
                "project_id": "p1",
                "contact_id": "c1",
                "unit_id": "u1",
                "vehicle_type": VehicleType.FOUR_WHEELER.value,
                "registration_number": "MH12AB1234",
                "photo_paths": [],
                "status": VehicleStatus.PENDING.value,
                "status_updated_at": "2026-07-16T10:00:00Z",
                "created_at": "2026-07-16T09:00:00Z",
                "updated_at": "2026-07-16T10:00:00Z",
                "sort_order": 0,
            },
        ]
    )
    body = UpdateVehicleRequest(
        vehicle_type=VehicleType.FOUR_WHEELER,
        fuel_type=VehicleFuelType.NON_EV,
        registration_number=" mh12ab1234 ",
    )

    with pytest.raises(ConflictException):
        await svc.update_vehicle(contact_id="c1", vehicle_id="v1", body=body)

    result = await svc.update_vehicle(contact_id="c1", vehicle_id="v1", body=body)
    assert result["registration_number"] == "MH12AB1234"


@pytest.mark.asyncio
async def test_update_vehicle_not_found():
    """Update returns not found when repository misses row."""
    from apps.user_service.app.schemas.contact_onboarding import UpdateVehicleRequest

    svc = _service()
    svc.repo.update = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.update_vehicle(
            contact_id="c1",
            vehicle_id="v1",
            body=UpdateVehicleRequest(make="Honda"),
        )


@pytest.mark.asyncio
async def test_withdraw_not_found():
    """Withdraw missing vehicle raises not found."""
    svc = _service()
    svc.repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.withdraw_vehicle(contact_id="c1", vehicle_id="v1")


@pytest.mark.asyncio
async def test_remove_rejected_not_allowed():
    """Rejected vehicles cannot be soft-removed."""
    svc = _service()
    svc.repo.get_by_id = AsyncMock(
        return_value={"id": "v1", "status": VehicleStatus.REJECTED.value}
    )

    with pytest.raises(ValidationException):
        await svc.remove_vehicle(contact_id="c1", vehicle_id="v1")


@pytest.mark.asyncio
async def test_remove_soft_remove_not_found():
    """Soft remove not found after release raises."""
    svc = _service()
    svc.repo.get_by_id = AsyncMock(
        return_value={
            "id": "v1",
            "status": VehicleStatus.APPROVED.value,
            "project_id": "p1",
            "parking_slot_id": None,
        }
    )
    svc.repo.soft_remove = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.remove_vehicle(contact_id="c1", vehicle_id="v1")


@pytest.mark.asyncio
async def test_review_vehicle_not_found():
    """Review missing vehicle raises not found."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project = AsyncMock(return_value=None)
    body = ReviewVehicleRequest(status=VehicleStatus.REJECTED, rejection_reason="bad")

    with pytest.raises(NotFoundException):
        await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)


@pytest.mark.asyncio
async def test_review_vehicle_slot_unavailable():
    """Approve fails when parking slot is not available."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project = AsyncMock(
        return_value={"id": "v1", "status": VehicleStatus.PENDING.value}
    )
    svc.parking_slots_repo.get_slot = AsyncMock(return_value={"id": "slot-1", "status": "assigned"})
    body = ReviewVehicleRequest(status=VehicleStatus.APPROVED, parking_slot_id="slot-1")

    with pytest.raises(ValidationException):
        await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)


@pytest.mark.asyncio
async def test_review_vehicle_assign_slot_fails():
    """Approve fails when slot assignment race loses."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project = AsyncMock(
        return_value={"id": "v1", "status": VehicleStatus.PENDING.value}
    )
    svc.parking_slots_repo.get_slot = AsyncMock(
        return_value={"id": "slot-1", "status": "available"}
    )
    svc.parking_slots_repo.assign_slot = AsyncMock(return_value=False)
    body = ReviewVehicleRequest(status=VehicleStatus.APPROVED, parking_slot_id="slot-1")

    with pytest.raises(ValidationException):
        await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)


@pytest.mark.asyncio
async def test_review_vehicle_reject_success():
    """Reject path updates vehicle without assigning slot."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project = AsyncMock(
        return_value={"id": "v1", "status": VehicleStatus.PENDING.value}
    )
    svc.repo.update_by_project = AsyncMock(
        return_value={
            "id": "v1",
            "organization_id": "org-1",
            "project_id": "p1",
            "contact_id": "c1",
            "unit_id": "u1",
            "vehicle_type": "four_wheeler",
            "registration_number": "MH12AB1234",
            "photo_paths": [],
            "status": VehicleStatus.REJECTED.value,
            "rejection_reason": "Invalid docs",
            "status_updated_at": "2026-07-16T10:00:00Z",
            "created_at": "2026-07-16T09:00:00Z",
            "updated_at": "2026-07-16T10:00:00Z",
            "sort_order": 0,
        }
    )
    body = ReviewVehicleRequest(status=VehicleStatus.REJECTED, rejection_reason="Invalid docs")

    result = await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)

    assert result["status"] == VehicleStatus.REJECTED.value
    svc.parking_slots_repo.assign_slot.assert_not_called()
    push = svc._push_dispatcher
    assert len(push.contact_calls) == 1
    assert push.contact_calls[0]["message_key"] == "notifications.push.vehicle.rejected"


@pytest.mark.asyncio
async def test_review_vehicle_update_returns_none():
    """Review raises when update_by_project returns nothing."""
    from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest

    svc = _service()
    svc.repo.get_by_project = AsyncMock(
        return_value={"id": "v1", "status": VehicleStatus.PENDING.value}
    )
    svc.repo.update_by_project = AsyncMock(return_value=None)
    body = ReviewVehicleRequest(status=VehicleStatus.REJECTED, rejection_reason="bad")

    with pytest.raises(NotFoundException):
        await svc.review_vehicle(project_id="p1", vehicle_id="v1", body=body)


@pytest.mark.asyncio
async def test_admin_delete_vehicle_pending_withdraws():
    """Admin delete hard-deletes pending vehicle requests."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.PENDING.value,
        "project_id": "p1",
    }
    svc.repo.delete.return_value = {"id": "v1"}

    result = await svc.admin_delete_vehicle(contact_id="c1", vehicle_id="v1")

    assert result is None
    svc.repo.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_delete_vehicle_approved_soft_removes():
    """Admin delete soft-removes approved vehicles."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.APPROVED.value,
        "project_id": "p1",
        "parking_slot_id": None,
    }
    svc.repo.soft_remove.return_value = {
        "id": "v1",
        "organization_id": "org-1",
        "project_id": "p1",
        "contact_id": "c1",
        "unit_id": "u1",
        "vehicle_type": "four_wheeler",
        "registration_number": "MH12AB1234",
        "photo_paths": [],
        "status": VehicleStatus.REMOVED.value,
        "status_updated_at": "2026-07-16T10:00:00Z",
        "created_at": "2026-07-16T09:00:00Z",
        "updated_at": "2026-07-16T10:00:00Z",
        "sort_order": 0,
    }

    result = await svc.admin_delete_vehicle(contact_id="c1", vehicle_id="v1")

    assert result["status"] == VehicleStatus.REMOVED.value
    svc.repo.soft_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_delete_vehicle_rejected_hard_deletes():
    """Admin delete hard-deletes rejected vehicle records."""
    svc = _service()
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "status": VehicleStatus.REJECTED.value,
        "project_id": "p1",
    }
    svc.repo.delete.return_value = {"id": "v1"}

    result = await svc.admin_delete_vehicle(contact_id="c1", vehicle_id="v1")

    assert result is None
    svc.repo.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_delete_project_vehicle_delegates_to_contact_delete():
    """Project-scoped admin delete resolves contact_id then removes vehicle."""
    svc = _service()
    svc.repo.get_by_project.return_value = {
        "id": "v1",
        "contact_id": "c1",
        "status": VehicleStatus.APPROVED.value,
        "project_id": "p1",
        "parking_slot_id": None,
    }
    svc.repo.get_by_id.return_value = {
        "id": "v1",
        "contact_id": "c1",
        "status": VehicleStatus.APPROVED.value,
        "project_id": "p1",
        "parking_slot_id": None,
    }
    svc.repo.soft_remove.return_value = {
        "id": "v1",
        "organization_id": "org-1",
        "project_id": "p1",
        "contact_id": "c1",
        "unit_id": "u1",
        "vehicle_type": "four_wheeler",
        "registration_number": "MH12AB1234",
        "photo_paths": [],
        "status": VehicleStatus.REMOVED.value,
        "status_updated_at": "2026-07-16T10:00:00Z",
        "created_at": "2026-07-16T09:00:00Z",
        "updated_at": "2026-07-16T10:00:00Z",
        "sort_order": 0,
    }

    result = await svc.admin_delete_project_vehicle(project_id="p1", vehicle_id="v1")

    svc.repo.get_by_project.assert_awaited_once_with(
        organization_id="org-1",
        project_id="p1",
        vehicle_id="v1",
    )
    assert result["status"] == VehicleStatus.REMOVED.value


@pytest.mark.asyncio
async def test_complete_vehicles_step_not_found():
    """Complete step fails when contact unit is missing."""
    svc = _service()
    svc.contact_units_repo = AsyncMock()
    svc.unit_onboarding_repo = AsyncMock()
    svc.contact_units_repo.get_owned_by_contact = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.complete_vehicles_step(contact_id="c1", contact_unit_id="cu-1")
