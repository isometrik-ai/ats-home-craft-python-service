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
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_primary_occupant_delete_vacates_units_and_cancels_open_requests(
    mock_turnover_cls,
    mock_move_events_cls,
):
    """Primary occupant delete vacates each owned unit via turnover service."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value=None
    )
    mock_turnover = MagicMock()
    mock_turnover.vacate_unit_completely = AsyncMock()
    mock_turnover_cls.return_value = mock_turnover
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
    svc.tenant_requests_repo.list_inflight_ids_for_submitter.return_value = ["req-1"]

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    svc.tenant_requests_repo.update_request_status.assert_awaited_once()
    mock_turnover.vacate_unit_completely.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Cancelled because the unit owner was deleted.",
        supersede_reason="owner_contact_deleted",
        tenant_already_moved_out=False,
    )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
async def test_primary_occupant_delete_records_tenant_move_out_before_vacate(
    mock_move_events_cls,
    mock_turnover_cls,
):
    """Owner delete records tenant move-out before vacating when a tenant is active."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value="move-tenant-1"
    )
    mock_turnover_cls.return_value.vacate_unit_completely = AsyncMock()
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

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change.assert_awaited_once_with(
        unit_id="unit-1",
        project_id="project-1",
        notes="Tenant move-out recorded because the unit owner was deleted.",
    )
    mock_turnover_cls.return_value.vacate_unit_completely.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Cancelled because the unit owner was deleted.",
        supersede_reason="owner_contact_deleted",
        tenant_already_moved_out=True,
    )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_primary_occupant_delete_cancels_open_walk_ins(
    mock_turnover_cls, mock_move_events_cls
):
    """Primary occupant delete vacates units through turnover service."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value=None
    )
    mock_turnover_cls.return_value.vacate_unit_completely = AsyncMock()
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

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    mock_turnover_cls.return_value.vacate_unit_completely.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_primary_occupant_delete_cleans_all_unit_vehicles_and_passes(
    mock_turnover_cls,
    mock_move_events_cls,
):
    """Primary occupant delete delegates full unit cleanup to turnover service."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value=None
    )
    mock_turnover_cls.return_value.vacate_unit_completely = AsyncMock()
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

    await svc.cascade_before_soft_delete(
        contact_id="owner-1",
        contact={},
    )

    mock_turnover_cls.return_value.vacate_unit_completely.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.VehiclesService")
async def test_primary_occupant_delete_soft_deletes_household_without_remaining_links(
    mock_vehicles_cls,
    mock_turnover_cls,
    mock_move_events_cls,
):
    """Primary occupant delete soft-deletes household contacts with no other unit links."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value=None
    )
    mock_vehicles = MagicMock()
    mock_vehicles.release_for_move_out = AsyncMock()
    mock_vehicles_cls.return_value = mock_vehicles
    mock_turnover_cls.return_value.vacate_unit_completely = AsyncMock()
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

    with (
        patch(
            "apps.user_service.app.services.contact_delete_cascade_service.revoke_contact_portal_sessions",
            new=AsyncMock(),
        ) as revoke_sessions,
        patch(
            "apps.user_service.app.services.contact_delete_cascade_service.purge_contact_notice_likes",
            new=AsyncMock(),
        ) as purge_notice_likes,
    ):
        await svc.cascade_before_soft_delete(
            contact_id="owner-1",
            contact={},
        )

    svc.contacts_repo.soft_delete_contact.assert_awaited_once_with(
        contact_id="family-abc",
        organization_id="org-1",
    )
    purge_notice_likes.assert_awaited_once_with(
        db_connection=svc.db_connection,
        organization_id="org-1",
        contact_id="family-abc",
    )
    revoke_sessions.assert_awaited_once_with(
        db_connection=svc.db_connection,
        organization_id="org-1",
        user_id="family-user-1",
    )


@pytest.mark.asyncio
@patch("apps.user_service.app.services.move_events_service.MoveEventsService")
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_primary_occupant_delete_keeps_household_with_remaining_unit_links(
    mock_turnover_cls,
    mock_move_events_cls,
):
    """Primary occupant delete skips household contacts that still belong to another unit."""
    mock_move_events_cls.return_value.record_tenant_move_out_for_owner_change = AsyncMock(
        return_value=None
    )
    mock_turnover_cls.return_value.vacate_unit_completely = AsyncMock()
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
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_household_delete_uses_single_occupant_turnover(mock_turnover_cls):
    """Household delete mirrors admin single-occupant move-out turnover."""
    mock_turnover = MagicMock()
    mock_turnover.release_single_occupant = AsyncMock()
    mock_turnover_cls.return_value = mock_turnover
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

    await svc.cascade_before_soft_delete(
        contact_id="family-abc",
        contact={},
    )

    mock_turnover.release_single_occupant.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        contact_id="family-abc",
        reason="Contact deleted; clearing occupant.",
    )
    svc.contact_units_repo.release_open_links_for_contact.assert_not_awaited()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_tenant_delete_uses_outgoing_household_turnover(mock_turnover_cls):
    """Approved tenant delete mirrors admin tenant move-out turnover."""
    mock_turnover = MagicMock()
    mock_turnover.release_outgoing_tenant_household = AsyncMock(return_value=[])
    mock_turnover_cls.return_value = mock_turnover
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

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={},
    )

    mock_turnover.release_outgoing_tenant_household.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Contact deleted; clearing outgoing household.",
    )
    svc.contact_units_repo.release_open_links_for_contact.assert_not_awaited()
    svc.tenant_requests_repo.update_request_status.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.contact_delete_cascade_service.UnitOccupancyTurnoverService")
async def test_tenant_delete_supersedes_tenant_request(mock_turnover_cls):
    """Approved tenant delete still supersedes the active tenant request."""
    mock_turnover = MagicMock()
    mock_turnover.release_outgoing_tenant_household = AsyncMock(return_value=[])
    mock_turnover_cls.return_value = mock_turnover
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

    await svc.cascade_before_soft_delete(
        contact_id="tenant-1",
        contact={},
    )

    svc.tenant_requests_repo.update_request_status.assert_awaited_once()
    svc.tenant_requests_repo.insert_event.assert_awaited_once()


def _cascade_with_turnover_repo_mocks() -> tuple[ContactDeleteCascadeService, dict[str, MagicMock]]:
    """Build cascade service whose turnover path uses shared repository mocks."""
    cascade = _service()
    turnover_repos = {
        "contact_units": MagicMock(),
        "contact_roles": MagicMock(),
        "units": MagicMock(),
        "passes": MagicMock(),
        "pass_events": MagicMock(),
        "household_invitations": MagicMock(),
        "daily_help": MagicMock(),
        "contacts": MagicMock(),
    }
    turnover_repos["units"].get_unit_owner_contact = AsyncMock(
        return_value={"contact_id": "owner-1"}
    )
    turnover_repos["units"].reconcile_unit_inventory_status = AsyncMock(return_value="vacant")
    turnover_repos["contact_units"].release_occupant_links_excluding_contacts = AsyncMock(
        return_value=[
            {"id": "cu-tenant", "contact_id": "tenant-1", "relationship": "self"},
            {"id": "cu-family", "contact_id": "family-1", "relationship": "spouse"},
        ]
    )
    turnover_repos["contact_units"].release_open_links_for_contact_on_unit = AsyncMock(
        return_value=[{"id": "cu-family", "contact_id": "family-abc", "relationship": "spouse"}]
    )
    turnover_repos["contact_units"].list_open_links_for_contact = AsyncMock(return_value=[])
    turnover_repos["contact_roles"].end_active_roles_for_unit = AsyncMock(return_value=[])
    turnover_repos["household_invitations"].cancel_by_contact_unit = AsyncMock()
    turnover_repos["passes"].list_active_for_unit = AsyncMock(
        return_value=[{"id": "pass-1", "host_contact_id": "tenant-1"}]
    )
    turnover_repos["passes"].list_active_ids_for_host = AsyncMock(return_value=["pass-2"])
    turnover_repos["passes"].cancel = AsyncMock(return_value={"id": "pass-1"})
    turnover_repos["pass_events"].insert_event = AsyncMock()
    turnover_repos["daily_help"].remove_all_active_links_for_unit = AsyncMock(return_value=[])
    turnover_repos["contacts"].get_contact_for_update = AsyncMock(
        return_value={"user_id": "family-user-1", "status": "active"}
    )
    turnover_repos["contacts"].soft_delete_contact = AsyncMock()
    return cascade, turnover_repos


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.purge_contact_notice_likes",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.WalkInService")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.DailyHelpRepository")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.HouseholdInvitationsRepository"
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.PassEventsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.PassesRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.UnitsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactRolesRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactUnitsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
async def test_tenant_delete_executes_real_turnover_household_cleanup(
    _mock_revoke: AsyncMock,
    mock_vehicles_cls: MagicMock,
    mock_contact_units_repo_cls: MagicMock,
    mock_contact_roles_repo_cls: MagicMock,
    mock_units_repo_cls: MagicMock,
    mock_passes_repo_cls: MagicMock,
    mock_pass_events_repo_cls: MagicMock,
    mock_household_invitations_repo_cls: MagicMock,
    mock_daily_help_repo_cls: MagicMock,
    mock_contacts_repo_cls: MagicMock,
    mock_walk_in_cls: MagicMock,
    _mock_purge_likes: AsyncMock,
) -> None:
    """Tenant delete runs the real turnover service and clears unit household artifacts."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover = AsyncMock()
    cascade, turnover_repos = _cascade_with_turnover_repo_mocks()
    mock_contact_units_repo_cls.return_value = turnover_repos["contact_units"]
    mock_contact_roles_repo_cls.return_value = turnover_repos["contact_roles"]
    mock_units_repo_cls.return_value = turnover_repos["units"]
    mock_passes_repo_cls.return_value = turnover_repos["passes"]
    mock_pass_events_repo_cls.return_value = turnover_repos["pass_events"]
    mock_household_invitations_repo_cls.return_value = turnover_repos["household_invitations"]
    mock_daily_help_repo_cls.return_value = turnover_repos["daily_help"]
    mock_contacts_repo_cls.return_value = turnover_repos["contacts"]

    cascade.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-tenant",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "tenant-1",
            "relationship": "self",
        }
    ]
    cascade.tenant_requests_repo.find_active_approved_for_unit_by_tenant.return_value = {
        "id": "req-1",
        "contact_unit_id": "cu-tenant",
    }

    await cascade.cascade_before_soft_delete(contact_id="tenant-1", contact={})

    turnover_repos[
        "contact_units"
    ].release_occupant_links_excluding_contacts.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
        exclude_contact_ids=["owner-1"],
    )
    turnover_repos["contact_roles"].end_active_roles_for_unit.assert_awaited_once()
    turnover_repos["daily_help"].remove_all_active_links_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
        removal_reason="Contact deleted; clearing outgoing household.",
    )
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover.assert_awaited_once_with(
        unit_id="unit-1",
        reason="Contact deleted; clearing outgoing household.",
    )
    turnover_repos["contacts"].soft_delete_contact.assert_awaited_once_with(
        contact_id="family-1",
        organization_id="org-1",
    )
    cascade.tenant_requests_repo.update_request_status.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.purge_contact_notice_likes",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.DailyHelpRepository")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.HouseholdInvitationsRepository"
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.PassEventsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.PassesRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.UnitsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactRolesRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.ContactUnitsRepository")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
async def test_household_delete_executes_real_turnover_single_occupant_cleanup(
    _mock_revoke: AsyncMock,
    mock_vehicles_cls: MagicMock,
    mock_contact_units_repo_cls: MagicMock,
    mock_contact_roles_repo_cls: MagicMock,
    mock_units_repo_cls: MagicMock,
    mock_passes_repo_cls: MagicMock,
    mock_pass_events_repo_cls: MagicMock,
    mock_household_invitations_repo_cls: MagicMock,
    mock_daily_help_repo_cls: MagicMock,
    mock_contacts_repo_cls: MagicMock,
    _mock_purge_likes: AsyncMock,
) -> None:
    """Household delete runs the real turnover service for a single occupant move-out."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    cascade, turnover_repos = _cascade_with_turnover_repo_mocks()
    mock_contact_units_repo_cls.return_value = turnover_repos["contact_units"]
    mock_contact_roles_repo_cls.return_value = turnover_repos["contact_roles"]
    mock_units_repo_cls.return_value = turnover_repos["units"]
    mock_passes_repo_cls.return_value = turnover_repos["passes"]
    mock_pass_events_repo_cls.return_value = turnover_repos["pass_events"]
    mock_household_invitations_repo_cls.return_value = turnover_repos["household_invitations"]
    mock_daily_help_repo_cls.return_value = turnover_repos["daily_help"]
    mock_contacts_repo_cls.return_value = turnover_repos["contacts"]

    cascade.contact_units_repo.list_open_links_for_contact.return_value = [
        {
            "id": "cu-family",
            "unit_id": "unit-1",
            "project_id": "project-1",
            "contact_id": "family-abc",
            "relationship": "spouse",
        }
    ]

    await cascade.cascade_before_soft_delete(contact_id="family-abc", contact={})

    turnover_repos["contact_units"].release_open_links_for_contact_on_unit.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="family-abc",
        unit_id="unit-1",
    )
    turnover_repos["contact_roles"].end_active_roles_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
        role_types=["Family", "Tenant"],
        contact_id="family-abc",
    )
    mock_vehicles_cls.return_value.release_for_move_out.assert_awaited_once_with(
        contact_id="family-abc",
        unit_id="unit-1",
    )
    turnover_repos["daily_help"].remove_all_active_links_for_unit.assert_not_awaited()
    turnover_repos["units"].reconcile_unit_inventory_status.assert_awaited_once_with(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
    )
