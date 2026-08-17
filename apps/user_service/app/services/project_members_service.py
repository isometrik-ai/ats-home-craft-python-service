"""Business logic for project member assignments."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.schemas.enums import ProjectMemberRole, ProjectMemberStatus
from apps.user_service.app.schemas.project_members import (
    AssignProjectMemberRequest,
    ProjectMemberResponse,
    UpdateProjectMemberRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    ensure_staff_project_access_for_context,
    format_iso_datetime,
)
from libs.shared_utils.common_query import (
    PROJECT_MEMBERS_MANAGE,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
)
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class ProjectMembersService:
    """Manage staff assignments to projects."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.projects_repo = ProjectsRepository(db_connection)
        self.org_member_repo = OrganizationMemberRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection,
            user_context=user_context,
        )

    async def _ensure_can_view_members(self, *, project_id: str) -> None:
        await ensure_staff_project_access_for_context(
            user_context=self.user_context,
            db_connection=self.db_connection,
            project_id=project_id,
            permission_codes=[
                PROJECTS_MANAGEMENT_VIEW,
                PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
            ],
        )

    async def _ensure_can_manage_members(self, *, project_id: str) -> None:
        await ensure_staff_project_access_for_context(
            user_context=self.user_context,
            db_connection=self.db_connection,
            project_id=project_id,
            permission_codes=PROJECT_MEMBERS_MANAGE,
        )

    async def _ensure_assignee_is_org_member(self, *, user_id: str) -> None:
        org_id = self.user_context.organization_id
        assert org_id
        is_member = await self.org_member_repo.check_user_membership_by_user_id(
            user_id=user_id,
            organization_id=org_id,
            disallow_suspended=True,
        )
        if not is_member:
            raise ValidationException(
                message_key="project_setup.errors.community_admin_not_org_member",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    @staticmethod
    def _to_response(row: dict[str, Any]) -> ProjectMemberResponse:
        payload = dict(row)
        if payload.get("joined_at") is not None:
            payload["joined_at"] = format_iso_datetime(payload["joined_at"])
        return ProjectMemberResponse.model_validate(payload)

    async def list_members(
        self,
        *,
        project_id: str,
        role: ProjectMemberRole | None = None,
        status: ProjectMemberStatus | None = None,
        search: str | None = None,
    ) -> list[ProjectMemberResponse]:
        """List staff assigned to a project."""
        await self._ensure_can_view_members(project_id=project_id)
        await self.setup_service.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.projects_repo.list_members_with_profiles(
            organization_id=org_id,
            project_id=project_id,
            role=role.value if role else None,
            status=status.value if status else None,
            search=search,
        )
        return [self._to_response(row) for row in rows]

    async def assign_member(
        self,
        *,
        project_id: str,
        body: AssignProjectMemberRequest,
    ) -> ProjectMemberResponse:
        """Assign an org member to a project."""
        await self._ensure_can_manage_members(project_id=project_id)
        await self.setup_service.ensure_project(project_id=project_id)
        await self._ensure_assignee_is_org_member(user_id=body.user_id)

        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.projects_repo.get_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=body.user_id,
        )
        if existing and existing.get("status") == ProjectMemberStatus.ACTIVE.value:
            raise ConflictException(
                message_key="project_members.errors.already_assigned",
                custom_code=CustomStatusCode.CONFLICT,
            )

        row = await self.projects_repo.upsert_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=body.user_id,
            role=body.role.value,
        )
        if existing and existing.get("status") != ProjectMemberStatus.ACTIVE.value:
            updated = await self.projects_repo.update_member(
                organization_id=org_id,
                project_id=project_id,
                user_id=body.user_id,
                role=body.role.value,
                status=ProjectMemberStatus.ACTIVE.value,
            )
            row = updated or row

        profiles = await self.projects_repo.list_members_with_profiles(
            organization_id=org_id,
            project_id=project_id,
        )
        match = next((p for p in profiles if p["user_id"] == body.user_id), row)
        return self._to_response(match)

    async def update_member(
        self,
        *,
        project_id: str,
        user_id: str,
        body: UpdateProjectMemberRequest,
    ) -> ProjectMemberResponse:
        """Update role or status for a project member."""
        await self._ensure_can_manage_members(project_id=project_id)
        await self.setup_service.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        assert org_id

        current = await self.projects_repo.get_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
        )
        if not current:
            raise NotFoundException(
                message_key="project_members.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        next_role = body.role.value if body.role else str(current.get("role"))
        next_status = body.status.value if body.status else str(current.get("status"))
        await self._ensure_not_last_community_admin(
            project_id=project_id,
            user_id=user_id,
            current=current,
            next_role=next_role,
            next_status=next_status,
        )

        updated = await self.projects_repo.update_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
            role=body.role.value if body.role else None,
            status=body.status.value if body.status else None,
        )
        if not updated:
            raise NotFoundException(
                message_key="project_members.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        profiles = await self.projects_repo.list_members_with_profiles(
            organization_id=org_id,
            project_id=project_id,
        )
        match = next((p for p in profiles if p["user_id"] == user_id), updated)
        return self._to_response(match)

    async def remove_member(self, *, project_id: str, user_id: str) -> ProjectMemberResponse:
        """Suspend a user's project assignment."""
        await self._ensure_can_manage_members(project_id=project_id)
        await self.setup_service.ensure_project(project_id=project_id)
        org_id = self.user_context.organization_id
        assert org_id

        current = await self.projects_repo.get_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
        )
        if not current or current.get("status") == ProjectMemberStatus.SUSPENDED.value:
            raise NotFoundException(
                message_key="project_members.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        await self._ensure_not_last_community_admin(
            project_id=project_id,
            user_id=user_id,
            current=current,
            next_role=str(current.get("role")),
            next_status=ProjectMemberStatus.SUSPENDED.value,
        )

        updated = await self.projects_repo.remove_member(
            organization_id=org_id,
            project_id=project_id,
            user_id=user_id,
        )
        assert updated
        return self._to_response(updated)

    async def _ensure_not_last_community_admin(
        self,
        *,
        project_id: str,
        user_id: str,
        current: dict[str, Any],
        next_role: str,
        next_status: str,
    ) -> None:
        """Prevent removing or demoting the last active community admin."""
        if (
            current.get("role") != ProjectMemberRole.COMMUNITY_ADMIN.value
            or current.get("status") != ProjectMemberStatus.ACTIVE.value
        ):
            return
        if (
            next_role == ProjectMemberRole.COMMUNITY_ADMIN.value
            and next_status == ProjectMemberStatus.ACTIVE.value
        ):
            return

        org_id = self.user_context.organization_id
        assert org_id
        admin_count = await self.projects_repo.count_active_members_by_role(
            organization_id=org_id,
            project_id=project_id,
            role=ProjectMemberRole.COMMUNITY_ADMIN.value,
        )
        if admin_count <= 1:
            raise ValidationException(
                message_key="project_members.errors.last_community_admin",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
