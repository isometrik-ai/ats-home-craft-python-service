"""Unit tests for ContactOnboardingService and related services."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus
from apps.user_service.app.services.contact_onboarding_service import (
    ContactOnboardingService,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ValidationException,
)


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(
        user_id="user-1",
        email="owner@example.com",
        organization_id="org-1",
    )


def _steps(
    *,
    overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build default onboarding step rows with optional status overrides."""
    overrides = overrides or {}
    return [
        {
            "step_key": step.value,
            "status": overrides.get(step.value, SetupStepStatus.NOT_STARTED.value),
            "completed_at": None,
        }
        for step in ContactOnboardingStep
    ]


class _FakeOnboardingRepo:
    """In-memory fake ContactOnboardingRepository."""

    def __init__(self, steps: list[dict[str, Any]] | None = None, *, completed: bool = False):
        self.steps = steps or _steps()
        self.completed = completed
        self.ensure_calls = 0
        self.complete_step_calls: list[str] = []
        self.skip_step_calls: list[str] = []

    async def ensure_steps(self, **_kwargs):
        """Record ensure_steps call."""
        self.ensure_calls += 1

    async def list_steps(self, **_kwargs):
        """Return configured step rows."""
        return self.steps

    async def is_wizard_completed(self, **_kwargs):
        """Return configured completion flag."""
        return self.completed

    async def complete_step(self, **kwargs):
        """Record complete_step call."""
        self.complete_step_calls.append(kwargs["step_key"])
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.COMPLETED.value}

    async def skip_step(self, **kwargs):
        """Record skip_step call."""
        self.skip_step_calls.append(kwargs["step_key"])
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.SKIPPED.value}


class _FakeContactUnitsRepo:
    """In-memory fake ContactUnitsRepository."""

    def __init__(
        self,
        *,
        active_count: int = 1,
        has_default: bool = True,
        confirm_result: list[dict[str, Any]] | None = None,
    ):
        self.active_count = active_count
        self.has_default = has_default
        self.confirm_result = confirm_result or [{"id": "cu-1", "status": "active"}]
        self.activate_called = False

    async def count_active_units(self, **_kwargs):
        """Return configured active unit count."""
        return self.active_count

    async def has_default_login(self, **_kwargs):
        """Return configured default-login flag."""
        return self.has_default

    async def activate_for_contact(self, **_kwargs):
        """Record activate_for_contact call."""
        self.activate_called = True

    async def confirm_selection(self, **_kwargs):
        """Return configured confirm result."""
        return self.confirm_result


def _service(
    onboarding_repo: _FakeOnboardingRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
) -> ContactOnboardingService:
    """Build ContactOnboardingService with fake repositories."""
    svc = ContactOnboardingService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.onboarding_repo = onboarding_repo or _FakeOnboardingRepo()
    svc.contact_units_repo = contact_units_repo or _FakeContactUnitsRepo()
    svc.contact_units_service = MagicMock()
    svc.vehicles_service = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_get_status_returns_first_incomplete_step():
    """Current step is the first non-terminal step in order."""
    repo = _FakeOnboardingRepo(
        _steps(
            overrides={
                ContactOnboardingStep.SELECT_PROPERTIES.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert result["is_completed"] is False
    assert repo.ensure_calls == 1


@pytest.mark.asyncio
async def test_get_status_completed_wizard():
    """Completed wizard returns no current step."""
    repo = _FakeOnboardingRepo(completed=True)
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] is None
    assert result["is_completed"] is True


@pytest.mark.asyncio
async def test_skip_step_rejects_non_skippable():
    """Non-skippable steps raise ValidationException."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.skip_step(
            contact_id="contact-1",
            step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
        )


@pytest.mark.asyncio
async def test_skip_step_allows_vehicles():
    """Vehicles step may be skipped."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)

    await svc.skip_step(
        contact_id="contact-1",
        step_key=ContactOnboardingStep.VEHICLES.value,
    )

    assert repo.skip_step_calls == [ContactOnboardingStep.VEHICLES.value]


@pytest.mark.asyncio
async def test_complete_onboarding_rejects_already_completed():
    """Already completed onboarding raises ConflictException."""
    repo = _FakeOnboardingRepo(completed=True)
    svc = _service(onboarding_repo=repo)

    with pytest.raises(ConflictException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_active_units():
    """Completion requires at least one active unit."""
    repo = _FakeOnboardingRepo(
        _steps(
            overrides={
                step.value: SetupStepStatus.COMPLETED.value for step in ContactOnboardingStep
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=0)
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_default_unit():
    """Multi-unit contacts must set a default unit before completion."""
    repo = _FakeOnboardingRepo(
        _steps(
            overrides={
                step.value: SetupStepStatus.COMPLETED.value for step in ContactOnboardingStep
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=2, has_default=False)
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_happy_path():
    """Successful completion activates units and completes review step."""
    repo = _FakeOnboardingRepo(
        _steps(
            overrides={
                step.value: SetupStepStatus.COMPLETED.value for step in ContactOnboardingStep
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=1)
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.complete_onboarding(contact_id="contact-1")

    assert units_repo.activate_called is True
    assert ContactOnboardingStep.REVIEW.value in repo.complete_step_calls
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_confirm_properties_validates_selection_count():
    """Confirm rejects when not all selected units are found."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.repo.confirm_selection = AsyncMock(return_value=[{"id": "cu-1", "status": "active"}])
    svc.onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=["cu-1", "cu-2"],
        )
