"""Unit tests for ContactDeleteCascadeService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import ContactType, VehicleStatus
from apps.user_service.app.services.contact_delete_cascade_service import (
    ContactDeleteCascadeService,
)
from apps.user_service.app.utils.common_utils import UserContext


def _service() -> ContactDeleteCascadeService:
    """Build cascade service with mocked repositories."""
    svc = ContactDeleteCascadeService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="admin-1",
            email="admin@example.com",
            organization_id="org-1",
        ),
    )
    svc.vehicles_repo = MagicMock()
    svc.vehicles_repo.list_by_contact = AsyncMock(return_value=[])
    svc.vehicles_repo.list_by_unit = AsyncMock(return_value=[])
    svc.vehicles_repo.delete = AsyncMock()
    svc.vehicles_repo.soft_remove = AsyncMock()
    svc.parking_slots_repo = MagicMock()
    svc.parking_slots_repo.release_slot = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.list_active_ids_for_host = AsyncMock(return_value=[])
    svc.passes_repo.list_active_for_unit = AsyncMock(return_value=[])
    svc.passes_repo.cancel = AsyncMock()
    svc.pass_events_repo = MagicMock()
    svc.pass_events_repo.insert_event = AsyncMock()
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(return_value=[])
    svc.contact_units_repo.list_household_by_primary = AsyncMock(return_value=[])
    svc.contact_units_repo.release_open_links_for_contact = AsyncMock(return_value=[])
    svc.contact_units_repo.release_all_open_links_for_unit = AsyncMock(return_value=[])
    svc.contact_units_repo.sync_move_out = AsyncMock()
    svc.units_repo = MagicMock()
    svc.units_repo.mark_unit_vacant = AsyncMock()
    svc.tenant_requests_repo = MagicMock()
    svc.tenant_requests_repo.list_inflight_ids_for_submitter = AsyncMock(return_value=[])
    svc.tenant_requests_repo.find_active_approved_for_unit = AsyncMock(return_value=None)
    svc.tenant_requests_repo.find_active_approved_for_unit_by_tenant = AsyncMock(return_value=None)
    svc.tenant_requests_repo.update_request_status = AsyncMock()
    svc.tenant_requests_repo.insert_event = AsyncMock()
    svc.household_invitations_repo = MagicMock()
    svc.household_invitations_repo.cancel_by_contact_unit = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_for_update = AsyncMock(return_value=None)
    svc.contacts_repo.soft_delete_contact = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_owner_delete_vacates_units_and_cancels_open_requests():
    """Owner delete releases occupants, vacates units, and cancels in-flight requests."""
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-owner",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "status": "active",
        }
    ]
    svc.contact_units_repo.release_all_open_links_for_unit.return_value = [
        {
            "id": "cu-owner",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "owner-1",
        }
    ]
    svc.tenant_requests_repo.list_inflight_ids_for_submitter.return_value = ["req-1"]

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={"contact_type": ContactType.OWNER.value},
    )

    svc.tenant_requests_repo.update_request_status.assert_awaited_once()
    svc.contact_units_repo.release_all_open_links_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
    )
    svc.units_repo.mark_unit_vacant.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
    )
    svc.household_invitations_repo.cancel_by_contact_unit.assert_awaited_once_with(
        organization_id="org-1",
        contact_unit_id="cu-owner",
    )
    svc.vehicles_repo.list_by_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
    )
    svc.passes_repo.list_active_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
    )
    svc.vehicles_repo.list_by_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_delete_cleans_all_unit_vehicles_and_passes():
    """Owner delete removes vehicles and passes created by any household member."""
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-owner",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "status": "active",
        }
    ]
    svc.vehicles_repo.list_by_unit.return_value = [
        {
            "id": "veh-family",
            "contact_id": "family-abc",
            "project_id": "project-1",
            "status": VehicleStatus.APPROVED.value,
            "parking_slot_id": "slot-1",
        }
    ]
    svc.passes_repo.list_active_for_unit.return_value = [
        {"id": "pass-1", "host_contact_id": "family-abc"}
    ]
    svc.passes_repo.cancel.return_value = {"id": "pass-1", "status": "cancelled"}

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={"contact_type": ContactType.OWNER.value},
    )

    svc.vehicles_repo.soft_remove.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="family-abc",
        vehicle_id="veh-family",
    )
    svc.passes_repo.cancel.assert_awaited_once_with(
        organization_id="org-1",
        host_contact_id="family-abc",
        pass_id="pass-1",
    )
    svc.pass_events_repo.insert_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_delete_soft_deletes_family_without_remaining_links():
    """Owner delete soft-deletes household family contacts with no other unit links."""
    svc = _service()
    svc.contact_units_repo.list_household_by_primary.return_value = [{"contact_id": "family-abc"}]
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(
        side_effect=[
            [
                {
                    "id": "cu-owner",
                    "unit_id": "unit-1",
                    "project_id": "project-1",
                    "status": "active",
                }
            ],
            [],
        ]
    )
    svc.contacts_repo.get_contact_for_update.return_value = {
        "id": "family-abc",
        "contact_type": ContactType.FAMILY.value,
        "status": "active",
        "user_id": "family-user-1",
    }

    with patch(
        "apps.user_service.app.services.contact_delete_cascade_service.revoke_contact_portal_sessions",
        new=AsyncMock(),
    ) as revoke_sessions:
        await svc.cascade_before_soft_delete(
            contact_id="owner-1",
            contact={"contact_type": ContactType.OWNER.value},
        )

    svc.contacts_repo.soft_delete_contact.assert_awaited_once_with(
        contact_id="family-abc",
        organization_id="org-1",
    )
    revoke_sessions.assert_awaited_once_with(
        db_connection=svc.db_connection,
        organization_id="org-1",
        user_id="family-user-1",
    )


@pytest.mark.asyncio
async def test_owner_delete_keeps_family_with_remaining_unit_links():
    """Owner delete skips family contacts that still belong to another unit."""
    svc = _service()
    svc.contact_units_repo.list_household_by_primary.return_value = [{"contact_id": "family-abc"}]
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(
        side_effect=[
            [
                {
                    "id": "cu-owner",
                    "unit_id": "unit-1",
                    "project_id": "project-1",
                    "status": "active",
                }
            ],
            [
                {
                    "id": "cu-family-other",
                    "unit_id": "unit-2",
                    "project_id": "project-2",
                    "status": "active",
                }
            ],
        ]
    )
    svc.contacts_repo.get_contact_for_update.return_value = {
        "id": "family-abc",
        "contact_type": ContactType.FAMILY.value,
        "status": "active",
    }

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={"contact_type": ContactType.OWNER.value},
    )

    svc.contacts_repo.soft_delete_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_family_delete_releases_link_without_vehicle_or_pass_cleanup():
    """Family delete moves out the member but keeps unit vehicles and passes."""
    svc = _service()
    svc.contact_units_repo.release_open_links_for_contact.return_value = [
        {
            "id": "cu-family",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "family-abc",
        }
    ]

    await svc.cascade_before_soft_delete(
        contact_id="family-abc",
        contact={"contact_type": ContactType.FAMILY.value},
    )

    svc.contact_units_repo.release_open_links_for_contact.assert_awaited_once()
    svc.household_invitations_repo.cancel_by_contact_unit.assert_awaited_once_with(
        organization_id="org-1",
        contact_unit_id="cu-family",
    )
    svc.vehicles_repo.list_by_contact.assert_not_awaited()
    svc.vehicles_repo.list_by_unit.assert_not_awaited()
    svc.passes_repo.list_active_ids_for_host.assert_not_awaited()
    svc.passes_repo.list_active_for_unit.assert_not_awaited()
    svc.units_repo.mark_unit_vacant.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_delete_releases_links_and_vehicles():
    """Tenant delete moves out contact_units and removes approved vehicles."""
    svc = _service()
    svc.contact_units_repo.release_open_links_for_contact.return_value = [
        {
            "id": "cu-tenant",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "tenant-1",
        }
    ]
    svc.vehicles_repo.list_by_contact.return_value = [
        {
            "id": "veh-1",
            "contact_id": "tenant-1",
            "project_id": "project-1",
            "status": VehicleStatus.APPROVED.value,
            "parking_slot_id": "slot-1",
        }
    ]

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={"contact_type": ContactType.TENANT.value},
    )

    svc.contact_units_repo.release_open_links_for_contact.assert_awaited_once()
    svc.parking_slots_repo.release_slot.assert_awaited_once()
    svc.vehicles_repo.soft_remove.assert_awaited_once()
    svc.units_repo.mark_unit_vacant.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_delete_cancels_active_passes():
    """Active visitor passes hosted by a tenant are cancelled."""
    svc = _service()
    svc.passes_repo.list_active_ids_for_host.return_value = ["pass-1"]
    svc.passes_repo.cancel.return_value = {"id": "pass-1", "status": "cancelled"}

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={"contact_type": ContactType.TENANT.value},
    )

    svc.passes_repo.cancel.assert_awaited_once()
    svc.pass_events_repo.insert_event.assert_awaited_once()
