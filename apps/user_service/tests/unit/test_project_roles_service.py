"""Unit tests for ProjectRolesService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.project_roles import CreateProjectRoleRequest
from apps.user_service.app.services.project_roles_service import ProjectRolesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.common_query import (
    NOTICES_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
)
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
ROLE_ID = "33333333-3333-4333-8333-333333333333"


def _ctx() -> UserContext:
    return UserContext(
        user_id="44444444-4444-4444-8444-444444444444",
        organization_id=ORG_ID,
        email="admin@example.com",
    )


def _service(*, repo: MagicMock | None = None) -> ProjectRolesService:
    svc = ProjectRolesService(db_connection=MagicMock(), user_context=_ctx())
    svc.repo = repo or MagicMock()
    svc.projects_repo = MagicMock()
    svc._ensure_project = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_delete_role_rejects_system_role() -> None:
    """Default roles with is_system=true cannot be deleted."""
    repo = MagicMock()
    repo.count_members_with_role = AsyncMock(return_value=0)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "is_system": True,
            "slug": "community_admin",
        }
    )

    with pytest.raises(ForbiddenException):
        await svc.delete_role(project_id=PROJECT_ID, project_role_id=ROLE_ID)

    repo.delete_role.assert_not_called()


@pytest.mark.asyncio
async def test_delete_role_rejects_role_in_use() -> None:
    """Custom roles assigned to members cannot be deleted."""
    repo = MagicMock()
    repo.count_members_with_role = AsyncMock(return_value=2)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "is_system": False,
            "slug": "custom_role",
        }
    )

    with pytest.raises(ForbiddenException):
        await svc.delete_role(project_id=PROJECT_ID, project_role_id=ROLE_ID)

    repo.delete_role.assert_not_called()


@pytest.mark.asyncio
async def test_delete_role_success_for_custom_role() -> None:
    """Unused custom roles can be deleted."""
    repo = MagicMock()
    repo.count_members_with_role = AsyncMock(return_value=0)
    repo.delete_role = AsyncMock(return_value=True)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "is_system": False,
            "slug": "custom_role",
        }
    )

    await svc.delete_role(project_id=PROJECT_ID, project_role_id=ROLE_ID)

    repo.delete_role.assert_awaited_once_with(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )


@pytest.mark.asyncio
async def test_delete_role_not_found_after_guard() -> None:
    """Missing custom role surfaces not found after checks."""
    repo = MagicMock()
    repo.count_members_with_role = AsyncMock(return_value=0)
    repo.delete_role = AsyncMock(return_value=False)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "is_system": False,
            "slug": "custom_role",
        }
    )

    with pytest.raises(NotFoundException):
        await svc.delete_role(project_id=PROJECT_ID, project_role_id=ROLE_ID)


@pytest.mark.asyncio
async def test_create_role_generates_slug_from_name() -> None:
    """Custom roles can use auto-generated slugs."""
    repo = MagicMock()
    repo.get_role_by_slug = AsyncMock(return_value=None)
    repo.get_role_by_id = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "maintenance_lead",
            "name": "Maintenance Lead",
            "description": None,
            "is_system": False,
        }
    )
    repo.create_role = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "maintenance_lead",
            "name": "Maintenance Lead",
            "description": None,
            "is_system": False,
        }
    )
    repo.get_permissions_for_role = AsyncMock(return_value=[])
    repo.count_members_with_role = AsyncMock(return_value=0)
    svc = _service(repo=repo)

    role = await svc.create_role(
        project_id=PROJECT_ID,
        body=CreateProjectRoleRequest(name="Maintenance Lead"),
    )

    assert role.slug == "maintenance_lead"
    assert role.is_system is False
    repo.create_role.assert_awaited_once()
    assert repo.create_role.await_args.kwargs["slug"] == "maintenance_lead"


@pytest.mark.asyncio
async def test_create_role_rejects_reserved_slug() -> None:
    """Reserved system slugs cannot be used for custom roles."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.create_role(
            project_id=PROJECT_ID,
            body=CreateProjectRoleRequest(name="Security Copy", slug="security"),
        )


@pytest.mark.asyncio
async def test_list_assignable_permissions_reads_project_catalog() -> None:
    """Assignable permission catalog comes from project_permissions."""
    repo = MagicMock()
    svc = _service(repo=repo)
    svc.project_permissions_repo.get_all_permissions = AsyncMock(
        return_value=[
            {
                "id": "perm-1",
                "code": "notices_management.view",
                "name": "View Notices",
                "category": "notices",
                "description": "View notices",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
    )

    items = await svc.list_assignable_permissions(project_id=PROJECT_ID)

    assert len(items) == 1
    assert items[0].code == "notices_management.view"
    assert items[0].id == "perm-1"


@pytest.mark.asyncio
async def test_get_my_permissions_denies_without_org_project_gate(monkeypatch) -> None:
    """Users without org project view or view_assigned cannot read my-permissions."""
    svc = _service()
    svc.project_permissions_repo = MagicMock()
    svc.project_permissions_repo.get_all_permissions = AsyncMock(return_value=[])

    monkeypatch.setattr(
        "apps.user_service.app.services.project_roles_service.check_user_access_async",
        AsyncMock(return_value=False),
    )

    with pytest.raises(ForbiddenException):
        await svc.get_my_permissions(project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_get_my_permissions_denies_view_assigned_without_membership(monkeypatch) -> None:
    """Assigned-project gate still requires membership on the requested project."""
    svc = _service()
    svc.project_permissions_repo = MagicMock()
    svc.project_permissions_repo.get_all_permissions = AsyncMock(return_value=[])
    svc.projects_repo.get_active_member_with_role = AsyncMock(return_value=None)
    svc._fetch_org_permission_codes = AsyncMock(return_value=set())  # pylint: disable=protected-access

    async def _access(**kwargs):
        return kwargs["permission_code"] == [PROJECTS_MANAGEMENT_VIEW_ASSIGNED]

    monkeypatch.setattr(
        "apps.user_service.app.services.project_roles_service.check_user_access_async",
        AsyncMock(side_effect=_access),
    )

    with pytest.raises(ForbiddenException):
        await svc.get_my_permissions(project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_get_my_permissions_hq_uses_org_scopable_not_full_catalog(monkeypatch) -> None:
    """HQ users get org scopable codes, not every row in project_permissions."""
    svc = _service()
    svc.project_permissions_repo = MagicMock()
    svc.project_permissions_repo.get_all_permissions = AsyncMock(
        return_value=[
            {"code": NOTICES_MANAGEMENT_VIEW},
            {"code": "projects_management.view_assigned"},
            {"code": "legacy_stale.permission"},
        ]
    )
    svc._fetch_org_permission_codes = AsyncMock(  # pylint: disable=protected-access
        return_value={PROJECTS_MANAGEMENT_VIEW}
    )

    async def _access(**kwargs):
        return kwargs["permission_code"] == [PROJECTS_MANAGEMENT_VIEW]

    monkeypatch.setattr(
        "apps.user_service.app.services.project_roles_service.check_user_access_async",
        AsyncMock(side_effect=_access),
    )

    result = await svc.get_my_permissions(project_id=PROJECT_ID)

    assert result.is_org_wide is True
    assert result.effective_permissions == []
    assert result.project_permissions == [NOTICES_MANAGEMENT_VIEW]
    assert "projects_management.view_assigned" not in result.project_permissions
    assert "legacy_stale.permission" not in result.project_permissions
