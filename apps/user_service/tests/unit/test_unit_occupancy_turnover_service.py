"""Unit tests for UnitOccupancyTurnoverService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.unit_occupancy_turnover_service import (
    UnitOccupancyTurnoverService,
)
from apps.user_service.app.utils.common_utils import UserContext


def _service() -> UnitOccupancyTurnoverService:
    """Build turnover service with mocked repositories."""
    svc = UnitOccupancyTurnoverService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="admin-1",
            email="admin@example.com",
            organization_id="org-1",
        ),
    )
    svc.units_repo = MagicMock()
    svc.units_repo.get_unit_owner_contact = AsyncMock(return_value={"contact_id": "owner-1"})
    svc.units_repo.reconcile_unit_inventory_status = AsyncMock(return_value="vacant")
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.release_occupant_links_excluding_contacts = AsyncMock(
        return_value=[
            {
                "id": "cu-tenant",
                "contact_id": "tenant-1",
                "relationship": "self",
            },
            {
                "id": "cu-family",
                "contact_id": "family-1",
                "relationship": "spouse",
            },
        ]
    )
    svc.contact_units_repo.release_open_links_for_contact_on_unit = AsyncMock(
        return_value=[{"id": "cu-family", "contact_id": "family-1", "relationship": "child"}]
    )
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(return_value=[])
    svc.contact_roles_repo = MagicMock()
    svc.contact_roles_repo.end_active_roles_for_unit = AsyncMock(return_value=[])
    svc.household_invitations_repo = MagicMock()
    svc.household_invitations_repo.cancel_by_contact_unit = AsyncMock()
    svc.passes_repo = MagicMock()
    svc.passes_repo.list_active_for_unit = AsyncMock(
        return_value=[{"id": "pass-1", "host_contact_id": "tenant-1"}]
    )
    svc.passes_repo.list_active_ids_for_host = AsyncMock(return_value=["pass-2"])
    svc.passes_repo.cancel = AsyncMock(return_value={"id": "pass-1"})
    svc.pass_events_repo = MagicMock()
    svc.pass_events_repo.insert_event = AsyncMock()
    svc.daily_help_repo = MagicMock()
    svc.daily_help_repo.remove_all_active_links_for_unit = AsyncMock(return_value=[])
    svc.tenant_requests_repo = MagicMock()
    svc.tenant_requests_repo.find_active_approved_for_unit = AsyncMock(return_value=None)
    svc.tenant_requests_repo.update_request_status = AsyncMock()
    svc.tenant_requests_repo.insert_event = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_for_update = AsyncMock(
        return_value={"user_id": "user-family-1", "status": "active"}
    )
    svc.contacts_repo.soft_delete_contact = AsyncMock()
    return svc


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.WalkInService")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
async def test_vacate_unit_completely_skips_tenant_cleanup_when_already_moved_out(
    mock_vehicles_cls: MagicMock,
    mock_walk_in_cls: MagicMock,
    _mock_revoke: AsyncMock,
) -> None:
    """When tenant move-out already ran, vacate only releases the owner."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover = AsyncMock()
    svc = _service()
    svc.contact_units_repo.release_all_open_links_for_unit = AsyncMock(
        return_value=[{"id": "cu-owner", "contact_id": "owner-1", "relationship": "self"}]
    )
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(return_value=[])

    await svc.vacate_unit_completely(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Unit owner unassigned",
        supersede_reason="owner_unassigned",
        tenant_already_moved_out=True,
    )

    svc.tenant_requests_repo.find_active_approved_for_unit.assert_not_awaited()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover.assert_not_awaited()
    svc.contact_units_repo.release_all_open_links_for_unit.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.purge_contact_notice_likes",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.WalkInService")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
async def test_release_outgoing_tenant_household_clears_unit_artifacts(
    mock_revoke: AsyncMock,
    mock_vehicles_cls: MagicMock,
    mock_walk_in_cls: MagicMock,
    _mock_purge_likes: AsyncMock,
) -> None:
    """Household turnover preserves owner and clears unit-scoped artifacts."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover = AsyncMock()
    svc = _service()

    released = await svc.release_outgoing_tenant_household(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Tenant turnover",
    )

    assert released == ["family-1", "tenant-1"]
    svc.contact_units_repo.release_occupant_links_excluding_contacts.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
        exclude_contact_ids=["owner-1"],
    )
    svc.contact_roles_repo.end_active_roles_for_unit.assert_awaited_once()
    mock_vehicles_cls.return_value.release_for_move_out.assert_awaited_once_with(unit_id="unit-1")
    svc.passes_repo.cancel.assert_awaited_once()
    svc.daily_help_repo.remove_all_active_links_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
        removal_reason="Tenant turnover",
    )
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover.assert_awaited_once_with(
        unit_id="unit-1",
        reason="Tenant turnover",
    )
    svc.contacts_repo.soft_delete_contact.assert_awaited_once_with(
        contact_id="family-1",
        organization_id="org-1",
    )
    svc.units_repo.reconcile_unit_inventory_status.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.purge_contact_notice_likes",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.WalkInService")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
async def test_vacate_unit_completely_clears_all_artifacts(
    _mock_revoke: AsyncMock,
    mock_vehicles_cls: MagicMock,
    mock_walk_in_cls: MagicMock,
    _mock_purge_likes: AsyncMock,
) -> None:
    """Full unit vacate clears occupants, roles, assets, and reconciles inventory."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover = AsyncMock()
    svc = _service()
    svc.units_repo.get_unit_owner_contact = AsyncMock(return_value={"contact_id": "owner-1"})
    svc.tenant_requests_repo.find_active_approved_for_unit = AsyncMock(return_value=None)
    svc.contact_units_repo.release_all_open_links_for_unit = AsyncMock(
        return_value=[
            {
                "id": "cu-owner",
                "contact_id": "owner-1",
                "relationship": "self",
            },
            {
                "id": "cu-family",
                "contact_id": "family-1",
                "relationship": "spouse",
            },
        ]
    )
    svc.contact_units_repo.list_open_links_for_contact = AsyncMock(return_value=[])
    svc.units_repo.reconcile_unit_inventory_status = AsyncMock(return_value="vacant")

    result = await svc.vacate_unit_completely(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        reason="Unit owner unassigned",
        supersede_reason="owner_unassigned",
        require_open_links=True,
    )

    assert result["previous_contact_id"] == "owner-1"
    assert result["unit_status"] == "vacant"
    svc.contact_roles_repo.end_active_roles_for_unit.assert_awaited_once_with(
        organization_id="org-1",
        unit_id="unit-1",
    )
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover.assert_awaited_once()
    svc.daily_help_repo.remove_all_active_links_for_unit.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.WalkInService")
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
async def test_vacate_unit_completely_requires_open_links(
    mock_vehicles_cls: MagicMock,
    mock_walk_in_cls: MagicMock,
) -> None:
    """Full unit vacate returns 404 when no open occupant links exist."""
    from libs.shared_utils.http_exceptions import NotFoundException

    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    mock_walk_in_cls.return_value.release_open_visit_units_for_unit_turnover = AsyncMock()
    svc = _service()
    svc.units_repo.get_unit_owner_contact = AsyncMock(return_value=None)
    svc.tenant_requests_repo.find_active_approved_for_unit = AsyncMock(return_value=None)
    svc.contact_units_repo.release_all_open_links_for_unit = AsyncMock(return_value=[])

    with pytest.raises(NotFoundException):
        await svc.vacate_unit_completely(
            organization_id="org-1",
            project_id="project-1",
            unit_id="unit-1",
            reason="Unit owner unassigned",
            supersede_reason="owner_unassigned",
            require_open_links=True,
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.purge_contact_notice_likes",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.services.unit_occupancy_turnover_service.VehiclesService")
@patch(
    "apps.user_service.app.services.unit_occupancy_turnover_service.revoke_contact_portal_sessions",
    new_callable=AsyncMock,
)
async def test_release_single_occupant_scopes_to_one_contact(
    mock_revoke: AsyncMock,
    mock_vehicles_cls: MagicMock,
    _mock_purge_likes: AsyncMock,
) -> None:
    """Single occupant move-out only clears that contact on the unit."""
    mock_vehicles_cls.return_value.release_for_move_out = AsyncMock()
    svc = _service()

    await svc.release_single_occupant(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        contact_id="family-1",
        reason="Family member move-out",
    )

    svc.contact_units_repo.release_open_links_for_contact_on_unit.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="family-1",
        unit_id="unit-1",
    )
    mock_vehicles_cls.return_value.release_for_move_out.assert_awaited_once_with(
        contact_id="family-1",
        unit_id="unit-1",
    )
    svc.passes_repo.list_active_ids_for_host.assert_awaited_once_with(
        organization_id="org-1",
        host_contact_id="family-1",
    )
    svc.daily_help_repo.remove_all_active_links_for_unit.assert_not_awaited()
