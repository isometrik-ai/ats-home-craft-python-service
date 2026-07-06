"""Unit tests for ProjectSetupService step-gating and completion."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    ProjectSetupStep,
    PropertyProjectStatus,
    SetupStepStatus,
)
from apps.user_service.app.services.project_setup_service import (
    ProjectSetupService,
    compute_visible_steps,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id="org-1")


class _FakeSetupRepo:
    """In-memory fake ProjectSetupRepository."""

    def __init__(self, *, steps: list[dict[str, Any]] | None = None, completed: bool = False):
        self.steps = steps or []
        self.completed = completed
        self.ensure_keys: list[str] = []
        self.skip_keys: list[str] = []
        self.status_calls: list[tuple[str, str]] = []

    async def ensure_steps(self, **kwargs):
        """Record ensure_steps call."""
        self.ensure_keys = list(kwargs["step_keys"])

    async def skip_steps(self, **kwargs):
        """Record skip_steps call."""
        self.skip_keys = list(kwargs["step_keys"])

    async def set_step_status(self, **kwargs):
        """Record set_step_status call and return a row."""
        self.status_calls.append((kwargs["step_key"], kwargs["status"]))
        return {
            "step_key": kwargs["step_key"],
            "status": kwargs["status"],
            "completed_at": None,
            "updated_at": None,
        }

    async def list_steps(self, **_kwargs):
        """Return configured step rows."""
        return self.steps

    async def is_completed(self, **_kwargs):
        """Return configured completion flag."""
        return self.completed


class _FakeProjectsRepo:
    """In-memory fake ProjectsRepository."""

    def __init__(self, project: dict[str, Any] | None = None):
        self.project = project
        self.current_step: str | None = None
        self.status_set: str | None = None

    async def get_project(self, **_kwargs):
        """Return configured project row."""
        return self.project

    async def set_setup_current_step(self, **kwargs):
        """Record the current step pointer update."""
        self.current_step = kwargs["step_key"]

    async def set_status(self, **kwargs):
        """Record status update and return an updated row."""
        self.status_set = kwargs["status"]
        return {**(self.project or {}), "status": kwargs["status"]}


def _service(
    *,
    setup_repo: _FakeSetupRepo | None = None,
    projects_repo: _FakeProjectsRepo | None = None,
) -> ProjectSetupService:
    """Build ProjectSetupService with fake repositories."""
    svc = ProjectSetupService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_repo = setup_repo or _FakeSetupRepo()
    svc.projects_repo = projects_repo or _FakeProjectsRepo()
    return svc


def _steps(overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Build canonical step rows with optional status overrides."""
    overrides = overrides or {}
    return [
        {
            "step_key": step.value,
            "status": overrides.get(step.value, SetupStepStatus.NOT_STARTED.value),
            "completed_at": None,
            "updated_at": None,
        }
        for step in ProjectSetupStep
    ]


def test_visible_steps_empty_property_types():
    """No property types => only always-on steps."""
    assert compute_visible_steps([]) == [
        ProjectSetupStep.PROJECT_BASICS.value,
        ProjectSetupStep.SITE_MAP.value,
    ]


def test_visible_steps_residential():
    """Residential includes tower/inventory/facilities/floor_plans + apartment_config."""
    visible = compute_visible_steps(["residential"])
    assert ProjectSetupStep.APARTMENT_CONFIG.value in visible
    assert ProjectSetupStep.COMMERCIAL_CONFIG.value not in visible
    assert ProjectSetupStep.PLOT_CONFIG.value not in visible
    assert ProjectSetupStep.TOWER_BUILDER.value in visible


def test_visible_steps_plots_only():
    """Plots-only excludes tower/inventory/facilities/floor_plans."""
    visible = compute_visible_steps(["plots"])
    assert visible == [
        ProjectSetupStep.PROJECT_BASICS.value,
        ProjectSetupStep.PLOT_CONFIG.value,
        ProjectSetupStep.SITE_MAP.value,
    ]


def test_visible_steps_all_types():
    """All property types => all nine steps."""
    visible = compute_visible_steps(["residential", "commercial", "plots"])
    assert len(visible) == len(list(ProjectSetupStep))


def test_visible_steps_are_ordered():
    """Visible steps preserve canonical wizard order."""
    visible = compute_visible_steps(["residential", "plots"])
    order = [step.value for step in ProjectSetupStep]
    assert visible == [key for key in order if key in visible]


@pytest.mark.asyncio
async def test_sync_steps_seeds_visible_and_skips_excluded():
    """Syncing seeds visible steps and marks excluded ones skipped."""
    setup_repo = _FakeSetupRepo(steps=_steps())
    svc = _service(setup_repo=setup_repo)

    await svc.sync_steps_for_property_types(project_id="p1", property_types=["plots"])

    assert ProjectSetupStep.PLOT_CONFIG.value in setup_repo.ensure_keys
    assert ProjectSetupStep.TOWER_BUILDER.value in setup_repo.skip_keys
    assert ProjectSetupStep.APARTMENT_CONFIG.value in setup_repo.skip_keys


@pytest.mark.asyncio
async def test_complete_step_rejects_invalid_step():
    """Unknown step keys raise ValidationException."""
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["residential"]})
    svc = _service(projects_repo=projects_repo)

    with pytest.raises(ValidationException):
        await svc.complete_step(project_id="p1", step_key="not_a_step")


@pytest.mark.asyncio
async def test_complete_step_rejects_non_applicable_step():
    """Steps outside the project's property types are rejected."""
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["plots"]})
    svc = _service(
        setup_repo=_FakeSetupRepo(steps=_steps()),
        projects_repo=projects_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_step(project_id="p1", step_key=ProjectSetupStep.TOWER_BUILDER.value)


@pytest.mark.asyncio
async def test_complete_step_marks_completed():
    """Applicable steps are marked completed and advance the pointer."""
    setup_repo = _FakeSetupRepo(
        steps=_steps(
            overrides={ProjectSetupStep.PROJECT_BASICS.value: SetupStepStatus.COMPLETED.value}
        )
    )
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["plots"]})
    svc = _service(setup_repo=setup_repo, projects_repo=projects_repo)

    result = await svc.complete_step(project_id="p1", step_key=ProjectSetupStep.PLOT_CONFIG.value)

    assert result["status"] == SetupStepStatus.COMPLETED.value
    assert (
        ProjectSetupStep.PLOT_CONFIG.value,
        SetupStepStatus.COMPLETED.value,
    ) in setup_repo.status_calls


@pytest.mark.asyncio
async def test_recompute_current_step_picks_first_unfinished():
    """Current step pointer becomes the first non-terminal step."""
    setup_repo = _FakeSetupRepo(
        steps=_steps(
            overrides={
                ProjectSetupStep.PROJECT_BASICS.value: SetupStepStatus.COMPLETED.value,
                ProjectSetupStep.TOWER_BUILDER.value: SetupStepStatus.SKIPPED.value,
            }
        )
    )
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["residential"]})
    svc = _service(setup_repo=setup_repo, projects_repo=projects_repo)

    current = await svc._recompute_current_step(project_id="p1")  # pylint: disable=protected-access

    assert current == ProjectSetupStep.APARTMENT_CONFIG.value
    assert projects_repo.current_step == ProjectSetupStep.APARTMENT_CONFIG.value


@pytest.mark.asyncio
async def test_get_status_missing_project_raises():
    """Missing project raises NotFoundException."""
    svc = _service(projects_repo=_FakeProjectsRepo(None))

    with pytest.raises(NotFoundException):
        await svc.get_status(project_id="missing")


@pytest.mark.asyncio
async def test_complete_wizard_requires_all_steps_done():
    """Wizard completion fails when steps remain."""
    setup_repo = _FakeSetupRepo(completed=False)
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["plots"]})
    svc = _service(setup_repo=setup_repo, projects_repo=projects_repo)

    with pytest.raises(ValidationException):
        await svc.complete_wizard(project_id="p1")


@pytest.mark.asyncio
async def test_complete_wizard_sets_status_active():
    """Wizard completion sets project status to active."""
    setup_repo = _FakeSetupRepo(completed=True)
    projects_repo = _FakeProjectsRepo({"id": "p1", "property_types": ["plots"]})
    svc = _service(setup_repo=setup_repo, projects_repo=projects_repo)

    result = await svc.complete_wizard(project_id="p1")

    assert result["status"] == PropertyProjectStatus.ACTIVE.value
    assert projects_repo.status_set == PropertyProjectStatus.ACTIVE.value
