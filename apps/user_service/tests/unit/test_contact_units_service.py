"""Unit tests for ContactUnitsService property confirm/claim."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.contact_onboarding import AdminAssignUnitRequest
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactUnitRelationship,
    SetupStepStatus,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(
        user_id="user-1",
        email="owner@example.com",
        organization_id="org-1",
    )


def _completed_profile_steps() -> list[dict[str, str]]:
    """Onboarding step rows with profile completed."""
    return [
        {
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    ]


def _service(*, onboarding_repo: AsyncMock | None = None) -> ContactUnitsService:
    """Build ContactUnitsService with mocked repositories."""
    svc = ContactUnitsService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = AsyncMock()
    svc.units_repo = AsyncMock()
    svc.units_repo.mark_unit_occupied = AsyncMock()
    svc.repo.find_active_primary_conflicts = AsyncMock(return_value=[])
    svc.onboarding_repo = onboarding_repo or AsyncMock()
    svc.unit_onboarding_repo = AsyncMock()
    svc.onboarding_repo.list_steps = AsyncMock(return_value=_completed_profile_steps())
    svc.unit_onboarding_repo.ensure_steps_for_units = AsyncMock()
    svc.repo.set_default_login = AsyncMock(return_value={"id": "cu-1"})
    return svc


@pytest.mark.asyncio
async def test_list_contact_units_returns_all_by_default():
    """Admin list returns all statuses when no filter is passed."""
    svc = _service()
    svc.repo.list_by_contact = AsyncMock(
        return_value=[
            {
                "id": "cu-1",
                "unit_id": "unit-1",
                "project_id": "proj-1",
                "contact_id": "contact-1",
                "code": "A-101",
                "status": "active",
                "is_primary": True,
                "is_default_login": True,
                "relationship": "self",
                "created_at": None,
            }
        ]
    )

    items = await svc.list_contact_units(contact_id="contact-1")

    svc.repo.list_by_contact.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        statuses=None,
    )
    assert items[0]["id"] == "cu-1"
    assert items[0]["code"] == "A-101"
    assert "created_at" in items[0]


@pytest.mark.asyncio
async def test_list_contact_units_filters_by_status():
    """Admin list can filter to one contact_unit status."""
    svc = _service()
    svc.repo.list_by_contact = AsyncMock(return_value=[])

    await svc.list_contact_units(
        contact_id="contact-1",
        statuses=["pending"],
    )

    svc.repo.list_by_contact.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        statuses=["pending"],
    )


@pytest.mark.asyncio
async def test_my_properties_pending_active_only():
    """Resident property list excludes moved_out units."""
    svc = _service()
    svc.repo.list_by_contact = AsyncMock(return_value=[])

    await svc.list_my_properties(contact_id="contact-1")

    svc.repo.list_by_contact.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        statuses=["pending", "active"],
    )


@pytest.mark.asyncio
async def test_claim_properties_requires_completed_onboarding():
    """Claim is rejected when onboarding is not yet complete."""
    svc = _service()
    svc.onboarding_repo.is_wizard_completed = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc.claim_properties(contact_id="contact-1", contact_unit_ids=["cu-2"])

    svc.repo.confirm_selection.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_properties_activates_and_flags_default():
    """Claim activates units and signals when default login is needed."""
    svc = _service()
    svc.onboarding_repo.is_wizard_completed = AsyncMock(return_value=True)
    svc.repo.confirm_selection = AsyncMock(return_value=[{"id": "cu-2", "status": "active"}])
    svc.repo.count_active_units = AsyncMock(return_value=2)
    svc.repo.has_default_login = AsyncMock(return_value=False)

    result = await svc.claim_properties(contact_id="contact-1", contact_unit_ids=["cu-2"])

    svc.unit_onboarding_repo.ensure_steps_for_units.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        contact_unit_ids=["cu-2"],
    )
    svc.repo.activate_units_by_ids.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        contact_unit_ids=["cu-2"],
    )
    assert result["items"] == [{"id": "cu-2", "status": "active"}]
    assert result["requires_default_unit"] is True


@pytest.mark.asyncio
async def test_admin_assign_rejects_other_contact():
    """Admin assign fails when another contact already holds the unit."""
    svc = _service()
    svc.repo.get_unit_project = AsyncMock(return_value={"project_id": "proj-1"})
    svc.repo.get_by_unit_and_contact = AsyncMock(return_value=None)
    svc.repo.unit_has_primary_occupant = AsyncMock(return_value=True)
    svc.repo.insert_allotment = AsyncMock()
    body = AdminAssignUnitRequest(unit_id="unit-1", is_primary=True)

    with pytest.raises(ValidationException):
        await svc.admin_assign_unit(contact_id="contact-1", body=body)

    svc.repo.insert_allotment.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_assign_rejects_same_contact():
    """Admin assign fails when the contact already has a pending or active link."""
    svc = _service()
    svc.repo.get_unit_project = AsyncMock(return_value={"project_id": "proj-1"})
    svc.repo.get_by_unit_and_contact = AsyncMock(return_value={"id": "cu-1", "status": "pending"})
    svc.repo.unit_has_primary_occupant = AsyncMock(return_value=False)
    svc.repo.insert_allotment = AsyncMock()
    body = AdminAssignUnitRequest(unit_id="unit-1", is_primary=True)

    with pytest.raises(ValidationException):
        await svc.admin_assign_unit(contact_id="contact-1", body=body)

    svc.repo.insert_allotment.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_assign_unit_creates_pending_allotment():
    """Admin assign inserts a pending contact_units row when the unit is free."""
    svc = _service()
    svc.repo.get_unit_project = AsyncMock(return_value={"project_id": "proj-1"})
    svc.repo.get_by_unit_and_contact = AsyncMock(return_value=None)
    svc.repo.unit_has_primary_occupant = AsyncMock(return_value=False)
    svc.repo.insert_allotment = AsyncMock(return_value={"id": "cu-1", "status": "pending"})
    body = AdminAssignUnitRequest(
        unit_id="unit-1",
        is_primary=True,
        relationship=ContactUnitRelationship.SELF,
    )

    result = await svc.admin_assign_unit(contact_id="contact-1", body=body)

    svc.repo.insert_allotment.assert_awaited_once_with(
        organization_id="org-1",
        project_id="proj-1",
        unit_id="unit-1",
        contact_id="contact-1",
        is_primary=True,
        relationship="self",
    )
    svc.units_repo.mark_unit_occupied.assert_awaited_once_with(
        organization_id="org-1",
        project_id="proj-1",
        unit_id="unit-1",
    )
    assert result == {"id": "cu-1", "status": "pending"}


@pytest.mark.asyncio
async def test_confirm_properties_rejects_primary_conflict():
    """Confirm fails when another contact is already the active primary occupant."""
    svc = _service()
    svc.repo.find_active_primary_conflicts = AsyncMock(return_value=["unit-1"])
    svc.repo.confirm_selection = AsyncMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(contact_id="contact-1", contact_unit_ids=["cu-1"])

    svc.repo.confirm_selection.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_activates_if_onboarding_done():
    """Confirm also sets activated_at if wizard is already complete."""
    svc = _service()
    svc.onboarding_repo.is_wizard_completed = AsyncMock(return_value=True)
    svc.repo.confirm_selection = AsyncMock(return_value=[{"id": "cu-1", "status": "active"}])
    svc.onboarding_repo.complete_step = AsyncMock()

    items = await svc.confirm_properties(contact_id="contact-1", contact_unit_ids=["cu-1"])

    svc.unit_onboarding_repo.ensure_steps_for_units.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        contact_unit_ids=["cu-1"],
    )
    svc.repo.set_default_login.assert_awaited_once_with(
        organization_id="org-1",
        contact_id="contact-1",
        contact_unit_id="cu-1",
    )
    svc.repo.activate_units_by_ids.assert_awaited_once()
    assert svc.onboarding_repo.complete_step.await_count == 2
    svc.onboarding_repo.complete_step.assert_any_await(
        organization_id="org-1",
        contact_id="contact-1",
        step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
    )
    svc.onboarding_repo.complete_step.assert_any_await(
        organization_id="org-1",
        contact_id="contact-1",
        step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
    )
    assert items == [{"id": "cu-1", "status": "active"}]
