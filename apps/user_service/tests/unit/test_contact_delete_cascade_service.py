"""Unit tests for ContactDeleteCascadeService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_primary_occupant_delete_vacates_units_and_cancels_open_requests(
    mock_vehicles_cls,
):
    """Primary occupant delete releases occupants, vacates units, and cancels in-flight requests."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-owner",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "status": "active",
            "relationship": "self",
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
        contact={},
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
    mock_vehicles.release_for_move_out.assert_awaited_once_with(unit_id="unit-1")
    svc.passes_repo.list_active_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
    )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_primary_occupant_delete_cleans_all_unit_vehicles_and_passes(mock_vehicles_cls):
    """Primary occupant delete removes vehicles and passes created by any household member."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-owner",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "status": "active",
            "relationship": "self",
        }
    ]
    svc.passes_repo.list_active_for_unit.return_value = [
        {"id": "pass-1", "host_contact_id": "family-abc"}
    ]
    svc.passes_repo.cancel.return_value = {"id": "pass-1", "status": "cancelled"}

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    mock_vehicles.release_for_move_out.assert_awaited_once_with(unit_id="unit-1")
    svc.passes_repo.cancel.assert_awaited_once_with(
        organization_id="org-1",
        host_contact_id="family-abc",
        pass_id="pass-1",
    )
    svc.pass_events_repo.insert_event.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_primary_occupant_delete_soft_deletes_household_without_remaining_links(
    mock_vehicles_cls,
):
    """Primary occupant delete soft-deletes household contacts with no other unit links."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
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
                    "relationship": "self",
                }
            ],
            [],
        ]
    )
    svc.contacts_repo.get_contact_for_update.return_value = {
        "id": "family-abc",
        "status": "active",
        "user_id": "family-user-1",
    }

    with patch(
        "apps.user_service.app.services.contact_delete_cascade_service.revoke_contact_portal_sessions",
        new=AsyncMock(),
    ) as revoke_sessions:
        await svc.cascade_before_soft_delete(
            contact_id="owner-1",
            contact={},
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
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_primary_occupant_delete_keeps_household_with_remaining_unit_links(
    mock_vehicles_cls,
):
    """Primary occupant delete skips household contacts that still belong to another unit."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
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
                    "relationship": "self",
                }
            ],
            [
                {
                    "id": "cu-family-other",
                    "unit_id": "unit-2",
                    "project_id": "project-2",
                    "status": "active",
                    "relationship": "spouse",
                }
            ],
        ]
    )
    svc.contacts_repo.get_contact_for_update.return_value = {
        "id": "family-abc",
        "status": "active",
    }

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    svc.contacts_repo.soft_delete_contact.assert_not_awaited()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_household_delete_releases_link_without_vehicle_or_pass_cleanup(
    mock_vehicles_cls,
):
    """Household delete moves out the member but keeps unit vehicles and passes."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-family",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "family-abc",
            "relationship": "spouse",
        }
    ]
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
        contact={},
    )

    svc.contact_units_repo.release_open_links_for_contact.assert_awaited_once()
    svc.household_invitations_repo.cancel_by_contact_unit.assert_awaited_once_with(
        organization_id="org-1",
        contact_unit_id="cu-family",
    )
    mock_vehicles.release_for_move_out.assert_not_awaited()
    svc.passes_repo.list_active_ids_for_host.assert_not_awaited()
    svc.passes_repo.list_active_for_unit.assert_not_awaited()
    svc.units_repo.mark_unit_vacant.assert_not_awaited()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_tenant_delete_releases_links_and_vehicles(mock_vehicles_cls):
    """Approved tenant delete moves out contact_units and removes approved vehicles."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-tenant",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "tenant-1",
            "relationship": "self",
        }
    ]
    svc.tenant_requests_repo.find_active_approved_for_unit_by_tenant.return_value = {
        "id": "req-1",
        "contact_unit_id": "cu-tenant",
    }
    svc.contact_units_repo.release_open_links_for_contact.return_value = [
        {
            "id": "cu-tenant",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "tenant-1",
        }
    ]

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={},
    )

    svc.contact_units_repo.release_open_links_for_contact.assert_awaited_once()
    mock_vehicles.release_for_move_out.assert_awaited_once_with(contact_id="tenant-1")
    svc.units_repo.mark_unit_vacant.assert_not_awaited()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_tenant_delete_cancels_active_passes(mock_vehicles_cls):
    """Active visitor passes hosted by an approved tenant are cancelled."""
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    svc = _service()
    svc.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-tenant",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "relationship": "self",
        }
    ]
    svc.tenant_requests_repo.find_active_approved_for_unit_by_tenant.return_value = {
        "id": "req-1",
        "contact_unit_id": "cu-tenant",
    }
    svc.passes_repo.list_active_ids_for_host.return_value = ["pass-1"]
    svc.passes_repo.cancel.return_value = {"id": "pass-1", "status": "cancelled"}

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={},
    )

    svc.passes_repo.cancel.assert_awaited_once()
    svc.pass_events_repo.insert_event.assert_awaited_once()
