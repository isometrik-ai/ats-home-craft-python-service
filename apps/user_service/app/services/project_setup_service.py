"""Project setup orchestration: status, step-gating, and completion."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.project_setup_repository import (
    PROJECT_SETUP_STEP_KEYS,
    ProjectSetupRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.schemas.enums import (
    ProjectSetupStep,
    PropertyProjectStatus,
    PropertyType,
    SetupStepStatus,
)
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

# Steps that only apply when the project has residential OR commercial types.
_STRUCTURE_STEPS: frozenset[str] = frozenset(
    {
        ProjectSetupStep.TOWER_BUILDER.value,
        ProjectSetupStep.INVENTORIES.value,
        ProjectSetupStep.FACILITIES.value,
        ProjectSetupStep.FLOOR_PLANS.value,
    }
)


def compute_visible_steps(property_types: list[str]) -> list[str]:
    """Return ordered applicable step keys for the given property types."""
    types = set(property_types or [])
    has_residential = PropertyType.RESIDENTIAL.value in types
    has_commercial = PropertyType.COMMERCIAL.value in types
    has_plots = PropertyType.PLOTS.value in types

    visible: set[str] = {
        ProjectSetupStep.PROJECT_BASICS.value,
        ProjectSetupStep.SITE_MAP.value,
    }
    if has_residential or has_commercial:
        visible |= _STRUCTURE_STEPS
    if has_residential:
        visible.add(ProjectSetupStep.APARTMENT_CONFIG.value)
    if has_commercial:
        visible.add(ProjectSetupStep.COMMERCIAL_CONFIG.value)
    if has_plots:
        visible.add(ProjectSetupStep.PLOT_CONFIG.value)
    return [key for key in PROJECT_SETUP_STEP_KEYS if key in visible]


class ProjectSetupService:
    """Wizard orchestration for project setup."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.projects_repo = ProjectsRepository(db_connection)
        self.setup_repo = ProjectSetupRepository(db_connection)

    async def ensure_project(self, *, project_id: str) -> dict[str, Any]:
        """Return the org-scoped project row or raise 404."""
        project = await self.projects_repo.get_project(
            organization_id=self.user_context.organization_id,
            project_id=project_id,
        )
        if not project:
            raise NotFoundException(
                message_key="project_setup.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return project

    async def sync_steps_for_property_types(
        self,
        *,
        project_id: str,
        property_types: list[str],
    ) -> None:
        """(Re)seed setup steps: create visible ones, skip excluded ones."""
        org_id = self.user_context.organization_id
        visible = compute_visible_steps(property_types)
        excluded = [key for key in PROJECT_SETUP_STEP_KEYS if key not in visible]
        await self.setup_repo.ensure_steps(
            organization_id=org_id,
            project_id=project_id,
            step_keys=visible,
        )
        await self.setup_repo.skip_steps(
            organization_id=org_id,
            project_id=project_id,
            step_keys=excluded,
        )
        await self._recompute_current_step(project_id=project_id)

    async def _recompute_current_step(self, *, project_id: str) -> str:
        """Set setup_current_step to the first not-yet-finished step."""
        org_id = self.user_context.organization_id
        steps = await self.setup_repo.list_steps(organization_id=org_id, project_id=project_id)
        finished = {SetupStepStatus.COMPLETED.value, SetupStepStatus.SKIPPED.value}
        current = ProjectSetupStep.SITE_MAP.value
        for step in steps:
            if step["status"] not in finished:
                current = step["step_key"]
                break
        await self.projects_repo.set_setup_current_step(
            organization_id=org_id,
            project_id=project_id,
            step_key=current,
        )
        return current

    @staticmethod
    def _normalize_step(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a step row for the API response."""
        return {
            "step_key": row["step_key"],
            "status": row["status"],
            "completed_at": format_iso_datetime(row.get("completed_at")),
            "updated_at": format_iso_datetime(row.get("updated_at")),
        }

    async def get_status(self, *, project_id: str) -> dict[str, Any]:
        """Return the wizard status snapshot for a project."""
        project = await self.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        steps = await self.setup_repo.list_steps(organization_id=org_id, project_id=project_id)
        is_completed = await self.setup_repo.is_completed(
            organization_id=org_id, project_id=project_id
        )
        return {
            "project_id": str(project["id"]),
            "status": str(project["status"]),
            "setup_current_step": str(project["setup_current_step"]),
            "is_completed": is_completed,
            "steps": [self._normalize_step(step) for step in steps],
        }

    async def complete_step(
        self,
        *,
        project_id: str,
        step_key: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mark a step completed after validating it applies to the project."""
        project = await self.ensure_project(project_id=project_id)
        try:
            ProjectSetupStep(step_key)
        except ValueError as exc:
            raise ValidationException(
                message_key="project_setup.errors.invalid_step",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            ) from exc

        visible = compute_visible_steps(list(project.get("property_types") or []))
        if step_key not in visible:
            raise ValidationException(
                message_key="project_setup.errors.step_not_applicable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        row = await self.setup_repo.set_step_status(
            organization_id=self.user_context.organization_id,
            project_id=project_id,
            step_key=step_key,
            status=SetupStepStatus.COMPLETED.value,
            data=data,
        )
        await self._recompute_current_step(project_id=project_id)
        return self._normalize_step(row) if row else {"step_key": step_key}

    async def complete_wizard(self, *, project_id: str) -> dict[str, Any]:
        """Finalize setup: require all steps done, set project status active."""
        await self.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        is_completed = await self.setup_repo.is_completed(
            organization_id=org_id, project_id=project_id
        )
        if not is_completed:
            raise ValidationException(
                message_key="project_setup.errors.steps_incomplete",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        updated = await self.projects_repo.set_status(
            organization_id=org_id,
            project_id=project_id,
            status=PropertyProjectStatus.ACTIVE.value,
        )
        return {
            "project_id": project_id,
            "status": str(updated["status"]) if updated else PropertyProjectStatus.ACTIVE.value,
        }
