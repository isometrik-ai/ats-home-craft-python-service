"""Business logic for per-project role templates."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.project_permissions_repository import (
    ProjectPermissionsRepository,
)
from apps.user_service.app.db.repositories.project_roles_repository import (
    ProjectRolesRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.schemas.admin_access_management import PermissionItem
from apps.user_service.app.schemas.project_roles import (
    CreateProjectRoleRequest,
    ProjectMyPermissionsResponse,
    ProjectRoleDetailItem,
    ProjectRoleItem,
    UpdateProjectRoleRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_permissions_data,
)
from libs.shared_middleware.jwt_auth import check_user_access_async
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.project_permission_aliases import (
    PROJECT_SCOPABLE_PERMISSION_CODES,
    project_code_allowed_by_org_ceiling,
)
from libs.shared_utils.project_role_defaults import (
    COMMUNITY_ADMIN_SLUG,
    is_reserved_system_project_role_slug,
    is_valid_project_role_slug,
    slugify_project_role_name,
)
from libs.shared_utils.status_codes import CustomStatusCode


class ProjectRolesService:
    """Seed and manage project-scoped role templates."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = ProjectRolesRepository(db_connection)
        self.project_permissions_repo = ProjectPermissionsRepository(db_connection)
        self.projects_repo = ProjectsRepository(db_connection)

    async def seed_default_roles_for_project(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, str]:
        """Create default project roles and permissions for a new project."""
        return await self.repo.seed_default_roles_for_project(
            organization_id=organization_id,
            project_id=project_id,
        )

    async def get_community_admin_role_id(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> str:
        """Return the community_admin role id for a project."""
        role = await self.repo.get_role_by_slug(
            organization_id=organization_id,
            project_id=project_id,
            slug=COMMUNITY_ADMIN_SLUG,
        )
        if not role:
            raise NotFoundException(
                message_key="project_roles.errors.community_admin_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return str(role["id"])

    async def ensure_project_role_belongs_to_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        project_role_id: str,
    ) -> dict[str, Any]:
        """Validate project_role_id exists on the given project."""
        role = await self.repo.get_role_by_id(
            organization_id=organization_id,
            project_id=project_id,
            project_role_id=project_role_id,
        )
        if not role:
            raise ValidationException(
                message_key="project_roles.errors.not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return role

    async def resolve_role_id_for_slug(
        self,
        *,
        organization_id: str,
        project_id: str,
        slug: str,
    ) -> str:
        """Resolve a role slug to project_role_id for the given project."""
        role = await self.repo.get_role_by_slug(
            organization_id=organization_id,
            project_id=project_id,
            slug=slug,
        )
        if not role:
            raise NotFoundException(
                message_key="project_roles.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return str(role["id"])

    async def list_roles(self, *, project_id: str) -> list[ProjectRoleItem]:
        """List role templates for a project."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)
        rows = await self.repo.list_roles_for_project(
            organization_id=org_id,
            project_id=project_id,
        )
        items: list[ProjectRoleItem] = []
        for row in rows:
            role_id = str(row["id"])
            permission_rows = await self.repo.get_permissions_for_role(
                organization_id=org_id,
                project_role_id=role_id,
            )
            member_count = await self.repo.count_members_with_role(project_role_id=role_id)
            items.append(
                ProjectRoleItem(
                    id=role_id,
                    organization_id=str(row["organization_id"]),
                    project_id=str(row["project_id"]),
                    slug=str(row["slug"]),
                    name=str(row["name"]),
                    description=row.get("description"),
                    is_system=bool(row.get("is_system")),
                    permission_count=len(permission_rows),
                    member_count=member_count,
                )
            )
        return items

    async def get_role_detail(
        self,
        *,
        project_id: str,
        project_role_id: str,
    ) -> ProjectRoleDetailItem:
        """Return one project role with permissions."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)
        role = await self.ensure_project_role_belongs_to_project(
            organization_id=org_id,
            project_id=project_id,
            project_role_id=project_role_id,
        )
        permission_rows = await self.repo.get_permissions_for_role(
            organization_id=org_id,
            project_role_id=project_role_id,
        )
        permission_items = format_permissions_data(permission_rows)
        member_count = await self.repo.count_members_with_role(project_role_id=project_role_id)
        return ProjectRoleDetailItem(
            id=str(role["id"]),
            organization_id=str(role["organization_id"]),
            project_id=str(role["project_id"]),
            slug=str(role["slug"]),
            name=str(role["name"]),
            description=role.get("description"),
            is_system=bool(role.get("is_system")),
            permission_count=len(permission_items),
            member_count=member_count,
            permission_ids=[item.id for item in permission_items],
            permissions=permission_items,
        )

    async def update_role(
        self,
        *,
        project_id: str,
        project_role_id: str,
        body: UpdateProjectRoleRequest,
    ) -> ProjectRoleDetailItem:
        """Update project role metadata and/or permissions."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)
        await self.ensure_project_role_belongs_to_project(
            organization_id=org_id,
            project_id=project_id,
            project_role_id=project_role_id,
        )

        if body.name is not None or body.description is not None:
            updated = await self.repo.update_role_metadata(
                organization_id=org_id,
                project_id=project_id,
                project_role_id=project_role_id,
                name=body.name,
                description=body.description,
            )
            if not updated:
                raise NotFoundException(
                    message_key="project_roles.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if body.permission_ids is not None:
            project_permission_ids = await self._resolve_project_permission_ids(body.permission_ids)
            await self.repo.replace_role_permissions(
                organization_id=org_id,
                project_role_id=project_role_id,
                project_permission_ids=project_permission_ids,
            )

        return await self.get_role_detail(
            project_id=project_id,
            project_role_id=project_role_id,
        )

    async def create_role(
        self,
        *,
        project_id: str,
        body: CreateProjectRoleRequest,
    ) -> ProjectRoleDetailItem:
        """Create a custom project role template."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)

        slug = await self._resolve_unique_custom_slug(
            organization_id=org_id,
            project_id=project_id,
            requested_slug=body.slug,
            name=body.name,
        )
        project_permission_ids = await self._resolve_project_permission_ids(body.permission_ids)

        try:
            created = await self.repo.create_role(
                organization_id=org_id,
                project_id=project_id,
                slug=slug,
                name=body.name.strip(),
                description=body.description,
                is_system=False,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_roles.errors.slug_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
                params={"slug": slug},
            ) from exc

        project_role_id = str(created["id"])
        if project_permission_ids:
            await self.repo.replace_role_permissions(
                organization_id=org_id,
                project_role_id=project_role_id,
                project_permission_ids=project_permission_ids,
            )

        return await self.get_role_detail(
            project_id=project_id,
            project_role_id=project_role_id,
        )

    async def list_assignable_permissions(self, *, project_id: str) -> list[PermissionItem]:
        """Return project permission catalog rows assignable to a project role template."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)
        rows = await self.project_permissions_repo.get_all_permissions(org_id)
        return format_permissions_data(rows)

    async def delete_role(
        self,
        *,
        project_id: str,
        project_role_id: str,
    ) -> None:
        """Delete a custom project role template."""
        org_id = self._require_org_id()
        await self._ensure_project(project_id=project_id)
        role = await self.ensure_project_role_belongs_to_project(
            organization_id=org_id,
            project_id=project_id,
            project_role_id=project_role_id,
        )
        if bool(role.get("is_system")):
            raise ForbiddenException(
                message_key="project_roles.errors.system_role_not_deletable",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        member_count = await self.repo.count_members_with_role(project_role_id=project_role_id)
        if member_count > 0:
            raise ForbiddenException(
                message_key="project_roles.errors.role_in_use",
                custom_code=CustomStatusCode.FORBIDDEN,
                params={"member_count": member_count},
            )

        deleted = await self.repo.delete_role(
            organization_id=org_id,
            project_id=project_id,
            project_role_id=project_role_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_roles.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def get_my_permissions(self, *, project_id: str) -> ProjectMyPermissionsResponse:
        """Return effective project permissions for the current user."""
        org_id = self._require_org_id()
        assert self.user_context and self.user_context.user_id
        await self._ensure_project(project_id=project_id)

        has_org_wide = await check_user_access_async(
            permission_code=[PROJECTS_MANAGEMENT_VIEW],
            user_id=self.user_context.user_id,
            organization_id=org_id,
            db_connection=self.db_connection,
        )
        if has_org_wide:
            project_catalog = await self.project_permissions_repo.get_all_permissions(org_id)
            codes = sorted(str(row["code"]) for row in project_catalog)
            return ProjectMyPermissionsResponse(
                project_id=project_id,
                is_org_wide=True,
                project_permissions=codes,
                effective_permissions=codes,
            )

        member = await self.projects_repo.get_active_member_with_role(
            organization_id=org_id,
            project_id=project_id,
            user_id=self.user_context.user_id,
        )
        if not member:
            raise ForbiddenException(
                message_key="auth.errors.project_access_denied",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        role_id = str(member["project_role_id"])
        project_codes = sorted(
            await self.repo.get_permission_codes_for_role(project_role_id=role_id)
        )
        org_codes = await self._fetch_org_permission_codes(org_id=org_id)
        effective = sorted(
            code for code in project_codes if project_code_allowed_by_org_ceiling(org_codes, code)
        )

        return ProjectMyPermissionsResponse(
            project_id=project_id,
            project_role_id=role_id,
            role_slug=str(member.get("role_slug") or "") or None,
            role_name=member.get("role_name"),
            is_org_wide=False,
            project_permissions=project_codes,
            effective_permissions=sorted(effective),
        )

    async def _resolve_project_permission_ids(self, permission_ids: list[str]) -> list[str]:
        """Validate ids refer to project_permissions rows for the current org."""
        org_id = self._require_org_id()
        resolved: list[str] = []
        for permission_id in permission_ids:
            row = await self.project_permissions_repo.get_permission_by_id(permission_id, org_id)
            if not row:
                raise BadRequestException(
                    message_key="permissions.errors.permission_not_found",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            code = str(row.get("code") or "")
            if code not in PROJECT_SCOPABLE_PERMISSION_CODES:
                raise ValidationException(
                    message_key="project_roles.errors.permission_not_project_scopable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            resolved.append(str(row["id"]))
        return resolved

    async def _fetch_org_permission_codes(self, *, org_id: str) -> set[str]:
        """Return org-role permission codes for the current user."""
        assert self.user_context and self.user_context.user_id
        row = await self.db_connection.fetchrow(
            """
            SELECT ARRAY_AGG(DISTINCT p.code) AS user_permissions
            FROM organization_members om
            INNER JOIN role_permissions rp ON om.role_id = rp.role_id
            INNER JOIN permissions p ON rp.permission_id = p.id
            WHERE om.user_id = $1::uuid
              AND om.organization_id = $2::uuid
              AND om.status = 'active'
            """,
            self.user_context.user_id,
            org_id,
        )
        raw = row["user_permissions"] if row and row.get("user_permissions") else []
        return {str(code) for code in raw}

    async def _resolve_unique_custom_slug(
        self,
        *,
        organization_id: str,
        project_id: str,
        requested_slug: str | None,
        name: str,
    ) -> str:
        """Normalize and uniquify a custom role slug within the project."""
        base_slug = (requested_slug or "").strip().lower() or slugify_project_role_name(name)
        if not is_valid_project_role_slug(base_slug):
            raise ValidationException(
                message_key="project_roles.errors.slug_invalid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if is_reserved_system_project_role_slug(base_slug):
            raise ValidationException(
                message_key="project_roles.errors.slug_reserved",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"slug": base_slug},
            )

        candidate = base_slug
        suffix = 2
        while await self.repo.get_role_by_slug(
            organization_id=organization_id,
            project_id=project_id,
            slug=candidate,
        ):
            suffix_text = f"_{suffix}"
            trimmed = base_slug[: 64 - len(suffix_text)].rstrip("_") or "custom_role"
            candidate = f"{trimmed}{suffix_text}"
            suffix += 1
            if suffix > 100:
                raise ConflictException(
                    message_key="project_roles.errors.slug_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                    params={"slug": base_slug},
                )
        return candidate

    def _require_org_id(self) -> str:
        """Return the current session organization id or raise unauthorized."""
        org_id = self.user_context.organization_id if self.user_context else None
        if not org_id:
            raise ValidationException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        return org_id

    async def _ensure_project(self, *, project_id: str) -> None:
        """Verify the project exists in the current organization when context is set."""
        if not self.user_context:
            return
        setup_service = ProjectSetupService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        await setup_service.ensure_project(project_id=project_id)
