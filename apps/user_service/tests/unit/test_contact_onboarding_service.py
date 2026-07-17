"""Unit tests for ContactOnboardingService and related services."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    CONTACT_LEVEL_STEP_KEYS,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactType,
    SetupStepStatus,
)
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


def _contact_steps(
    *,
    overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build contact-level onboarding step rows with optional status overrides."""
    overrides = overrides or {}
    return [
        {
            "step_key": step_key,
            "status": overrides.get(step_key, SetupStepStatus.NOT_STARTED.value),
            "completed_at": None,
        }
        for step_key in CONTACT_LEVEL_STEP_KEYS
    ]


class _FakeOnboardingRepo:
    """In-memory fake ContactOnboardingRepository."""

    def __init__(
        self,
        steps: list[dict[str, Any]] | None = None,
        *,
        profile_steps: list[dict[str, Any]] | None = None,
        completed: bool = False,
    ):
        self.steps = steps or _contact_steps()
        self.profile_steps = profile_steps
        self.completed = completed
        self.ensure_calls = 0
        self.complete_step_calls: list[str] = []
        self.skip_step_calls: list[str] = []

    async def ensure_steps(self, **_kwargs):
        """Record ensure_steps call."""
        self.ensure_calls += 1

    async def ensure_profile_step(self, **_kwargs):
        """Record ensure_profile_step call."""
        self.ensure_calls += 1

    async def list_steps(self, **_kwargs):
        """Return configured step rows."""
        return self.steps

    async def list_profile_step(self, **_kwargs):
        """Return configured profile step rows for family contacts."""
        if self.profile_steps is not None:
            return self.profile_steps
        matches = [
            row
            for row in self.steps
            if row.get("step_key") == ContactOnboardingStep.COMPLETE_PROFILE.value
        ]
        if matches:
            return matches
        return [
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            }
        ]

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


class _FakeUnitOnboardingRepo:
    """In-memory fake ContactUnitOnboardingRepository."""

    def __init__(
        self,
        *,
        unit_step_rows: list[dict[str, Any]] | None = None,
        all_terminal: bool = True,
    ):
        self.unit_step_rows = unit_step_rows or []
        self.all_terminal = all_terminal
        self.skip_step_calls: list[tuple[str, str]] = []
        self.complete_step_calls: list[tuple[str, str]] = []

    async def list_steps_for_contact(self, **_kwargs):
        """Return configured unit step rows."""
        return self.unit_step_rows

    async def all_unit_steps_terminal(self, **_kwargs):
        """Return configured terminal flag."""
        return self.all_terminal

    async def skip_step(self, **kwargs):
        """Record skip_step call."""
        self.skip_step_calls.append((kwargs["contact_unit_id"], kwargs["step_key"]))
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.SKIPPED.value}

    async def complete_step(self, **kwargs):
        """Record complete_step call."""
        self.complete_step_calls.append((kwargs["contact_unit_id"], kwargs["step_key"]))
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.COMPLETED.value}


class _FakeContactUnitsRepo:
    """In-memory fake ContactUnitsRepository."""

    def __init__(
        self,
        *,
        active_count: int = 1,
        has_default: bool = True,
        confirm_result: list[dict[str, Any]] | None = None,
        owned_unit: dict[str, Any] | None = None,
    ):
        self.active_count = active_count
        self.has_default = has_default
        self.confirm_result = confirm_result or [{"id": "cu-1", "status": "active"}]
        self.owned_unit = owned_unit or {"id": "cu-1"}
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

    async def get_owned_by_contact(self, **_kwargs):
        """Return configured owned unit row."""
        return self.owned_unit

    async def confirm_selection(self, **_kwargs):
        """Return configured confirm result."""
        return self.confirm_result


def _service(
    onboarding_repo: _FakeOnboardingRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
    unit_onboarding_repo: _FakeUnitOnboardingRepo | None = None,
) -> ContactOnboardingService:
    """Build ContactOnboardingService with fake repositories."""
    svc = ContactOnboardingService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.onboarding_repo = onboarding_repo or _FakeOnboardingRepo()
    svc.unit_onboarding_repo = unit_onboarding_repo or _FakeUnitOnboardingRepo()
    svc.contact_units_repo = contact_units_repo or _FakeContactUnitsRepo()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(
        return_value={"contact_type": ContactType.OWNER.value},
    )
    svc.contact_units_service = MagicMock()
    svc.vehicles_service = MagicMock()
    return svc


def _completed_profile_steps() -> list[dict[str, str]]:
    """Onboarding step rows with profile completed (for confirm_properties tests)."""
    return [
        {
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    ]


def _terminal_contact_steps() -> list[dict[str, Any]]:
    """All contact-level steps completed."""
    return _contact_steps(
        overrides={
            step_key: SetupStepStatus.COMPLETED.value for step_key in CONTACT_LEVEL_STEP_KEYS
        }
    )


@pytest.mark.asyncio
async def test_get_status_family_contact_profile_only():
    """Family contacts only see the complete_profile onboarding step."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            }
        ]
    )
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(
        contact_id="contact-1",
        contact_type=ContactType.FAMILY.value,
    )

    assert result["setup_current_step"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert result["steps"] == [
        {
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": SetupStepStatus.NOT_STARTED.value,
            "completed_at": None,
        }
    ]
    assert result["unit_onboarding"] == []
    assert result["current_contact_unit_id"] is None
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_get_status_family_contact_completed():
    """Family onboarding is complete once profile is done."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.COMPLETED.value,
                "completed_at": None,
            }
        ]
    )
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(
        contact_id="contact-1",
        contact_type=ContactType.FAMILY.value,
    )

    assert result["setup_current_step"] is None
    assert result["is_completed"] is True


@pytest.mark.asyncio
async def test_get_status_returns_profile_first():
    """Current step is complete_profile when profile is not terminal."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert result["current_contact_unit_id"] is None
    assert result["is_completed"] is False
    assert repo.ensure_calls == 1


@pytest.mark.asyncio
async def test_get_status_returns_unit_step_after_properties():
    """After profile and properties, navigation points at unit vehicles step."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.SELECT_PROPERTIES.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    unit_repo = _FakeUnitOnboardingRepo(
        unit_step_rows=[
            {
                "contact_unit_id": "cu-1",
                "unit_id": "unit-1",
                "unit_code": "A-101",
                "step_key": ContactOnboardingStep.VEHICLES.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            },
            {
                "contact_unit_id": "cu-1",
                "unit_id": "unit-1",
                "unit_code": "A-101",
                "step_key": ContactOnboardingStep.HOUSEHOLD.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            },
        ],
        all_terminal=False,
    )
    svc = _service(onboarding_repo=repo, unit_onboarding_repo=unit_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] == ContactOnboardingStep.VEHICLES.value
    assert result["current_contact_unit_id"] == "cu-1"
    assert len(result["unit_onboarding"]) == 1


@pytest.mark.asyncio
async def test_get_status_completed_wizard():
    """Completed wizard returns no current step."""
    repo = _FakeOnboardingRepo(completed=True)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    units_repo = _FakeContactUnitsRepo(active_count=1)
    svc = _service(
        onboarding_repo=repo,
        unit_onboarding_repo=unit_repo,
        contact_units_repo=units_repo,
    )

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
async def test_skip_step_requires_contact_unit_id():
    """Unit steps require contact_unit_id."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.skip_step(
            contact_id="contact-1",
            step_key=ContactOnboardingStep.VEHICLES.value,
        )


@pytest.mark.asyncio
async def test_skip_step_allows_vehicles_for_unit():
    """Vehicles step may be skipped per unit."""
    unit_repo = _FakeUnitOnboardingRepo()
    units_repo = _FakeContactUnitsRepo()
    svc = _service(unit_onboarding_repo=unit_repo, contact_units_repo=units_repo)

    await svc.skip_step(
        contact_id="contact-1",
        step_key=ContactOnboardingStep.VEHICLES.value,
        contact_unit_id="cu-1",
    )

    assert unit_repo.skip_step_calls == [("cu-1", ContactOnboardingStep.VEHICLES.value)]


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
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=0)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_default_unit():
    """Multi-unit contacts must set a default unit before completion."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=2, has_default=False)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_unit_steps():
    """Completion requires all unit steps terminal."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=1)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=False)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_happy_path():
    """Successful completion activates units and completes review step."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=1)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

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
    svc.onboarding_repo.list_steps = AsyncMock(return_value=_completed_profile_steps())
    svc.unit_onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=["cu-1", "cu-2"],
        )


@pytest.mark.asyncio
async def test_confirm_properties_requires_profile_step():
    """Confirm rejects when profile step is not complete."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.onboarding_repo = MagicMock()
    svc.onboarding_repo.list_steps = AsyncMock(
        return_value=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
            }
        ]
    )
    svc.unit_onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=["cu-1"],
        )
