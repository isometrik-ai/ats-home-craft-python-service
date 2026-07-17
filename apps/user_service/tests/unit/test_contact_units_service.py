"""Unit tests for ContactUnitsService property confirm/claim."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus
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
    svc.onboarding_repo = onboarding_repo or AsyncMock()
    svc.unit_onboarding_repo = AsyncMock()
    svc.onboarding_repo.list_steps = AsyncMock(return_value=_completed_profile_steps())
    svc.unit_onboarding_repo.ensure_steps_for_units = AsyncMock()
    svc.repo.set_default_login = AsyncMock(return_value={"id": "cu-1"})
    return svc


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
