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
    """HQ users without project assignment get org scopable codes, not full catalog."""
    svc = _service()
    svc.projects_repo.get_active_member_with_role = AsyncMock(return_value=None)
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


@pytest.mark.asyncio
async def test_get_my_permissions_hq_assigned_uses_project_role(monkeypatch) -> None:
    """HQ users assigned to a project use project role permissions for effective UI gating."""
    svc = _service()
    svc.projects_repo.get_active_member_with_role = AsyncMock(
        return_value={
            "project_role_id": ROLE_ID,
            "role_slug": "community_admin",
            "role_name": "Community Admin",
        }
    )
    svc.repo.get_permission_codes_for_role = AsyncMock(
        return_value={NOTICES_MANAGEMENT_VIEW, "notices_management.edit"}
    )
    svc._fetch_org_permission_codes = AsyncMock(  # pylint: disable=protected-access
        return_value={
            PROJECTS_MANAGEMENT_VIEW,
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        }
    )

    async def _access(**kwargs):
        return kwargs["permission_code"] == [PROJECTS_MANAGEMENT_VIEW]

    monkeypatch.setattr(
        "apps.user_service.app.services.project_roles_service.check_user_access_async",
        AsyncMock(side_effect=_access),
    )

    result = await svc.get_my_permissions(project_id=PROJECT_ID)

    assert result.is_org_wide is True
    assert result.project_role_id == ROLE_ID
    assert result.role_slug == "community_admin"
    assert result.effective_permissions == ["notices_management.edit", NOTICES_MANAGEMENT_VIEW]
    assert result.project_permissions == ["notices_management.edit", NOTICES_MANAGEMENT_VIEW]


@pytest.mark.asyncio
async def test_seed_default_roles_delegates_to_repository() -> None:
    repo = MagicMock()
    repo.seed_default_roles_for_project = AsyncMock(return_value={"community_admin": ROLE_ID})
    svc = ProjectRolesService(db_connection=MagicMock(), user_context=None)
    svc.repo = repo

    result = await svc.seed_default_roles_for_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert result["community_admin"] == ROLE_ID
    repo.seed_default_roles_for_project.assert_awaited_once_with(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )


@pytest.mark.asyncio
async def test_get_community_admin_role_id() -> None:
    repo = MagicMock()
    repo.get_role_by_slug = AsyncMock(return_value={"id": ROLE_ID})
    svc = ProjectRolesService(db_connection=MagicMock(), user_context=None)
    svc.repo = repo

    role_id = await svc.get_community_admin_role_id(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )
    assert role_id == ROLE_ID

    repo.get_role_by_slug = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_community_admin_role_id(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
        )


@pytest.mark.asyncio
async def test_ensure_and_resolve_role_slug() -> None:
    repo = MagicMock()
    repo.get_role_by_id = AsyncMock(return_value={"id": ROLE_ID, "slug": "custom_role"})
    repo.get_role_by_slug = AsyncMock(return_value={"id": ROLE_ID})
    svc = _service(repo=repo)

    role = await svc.ensure_project_role_belongs_to_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )
    assert role["slug"] == "custom_role"

    repo.get_role_by_id = AsyncMock(return_value=None)
    with pytest.raises(ValidationException):
        await svc.ensure_project_role_belongs_to_project(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
        )

    resolved = await svc.resolve_role_id_for_slug(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slug="custom_role",
    )
    assert resolved == ROLE_ID

    repo.get_role_by_slug = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.resolve_role_id_for_slug(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            slug="missing",
        )


@pytest.mark.asyncio
async def test_list_roles_and_get_role_detail() -> None:
    repo = MagicMock()
    repo.list_roles_for_project = AsyncMock(
        return_value=[
            {
                "id": ROLE_ID,
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "slug": "custom_role",
                "name": "Custom Role",
                "description": "Desc",
                "is_system": False,
            }
        ]
    )
    repo.get_permissions_for_role = AsyncMock(
        return_value=[
            {
                "id": "perm-1",
                "code": NOTICES_MANAGEMENT_VIEW,
                "name": "View Notices",
                "category": "notices",
                "description": None,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    repo.count_members_with_role = AsyncMock(return_value=2)
    repo.get_role_by_id = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "custom_role",
            "name": "Custom Role",
            "description": "Desc",
            "is_system": False,
        }
    )
    svc = _service(repo=repo)

    items = await svc.list_roles(project_id=PROJECT_ID)
    assert len(items) == 1
    assert items[0].permission_count == 1
    assert items[0].member_count == 2

    detail = await svc.get_role_detail(project_id=PROJECT_ID, project_role_id=ROLE_ID)
    assert detail.id == ROLE_ID
    assert detail.permission_ids == ["perm-1"]
    assert detail.permissions[0].code == NOTICES_MANAGEMENT_VIEW


@pytest.mark.asyncio
async def test_update_role_metadata_and_permissions() -> None:
    from apps.user_service.app.schemas.project_roles import UpdateProjectRoleRequest

    repo = MagicMock()
    repo.update_role_metadata = AsyncMock(return_value=True)
    repo.replace_role_permissions = AsyncMock()
    repo.get_role_by_id = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "custom_role",
            "name": "Updated",
            "description": "New",
            "is_system": False,
        }
    )
    repo.get_permissions_for_role = AsyncMock(return_value=[])
    repo.count_members_with_role = AsyncMock(return_value=0)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "custom_role",
            "name": "Updated",
            "description": "New",
            "is_system": False,
        }
    )
    svc._resolve_project_permission_ids = AsyncMock(return_value=["perm-1"])  # pylint: disable=protected-access

    updated = await svc.update_role(
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
        body=UpdateProjectRoleRequest(
            name="Updated",
            description="New",
            permission_ids=["perm-1"],
        ),
    )

    assert updated.name == "Updated"
    repo.replace_role_permissions.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_role_metadata_not_found() -> None:
    from apps.user_service.app.schemas.project_roles import UpdateProjectRoleRequest

    repo = MagicMock()
    repo.update_role_metadata = AsyncMock(return_value=False)
    svc = _service(repo=repo)
    svc.ensure_project_role_belongs_to_project = AsyncMock(return_value={"id": ROLE_ID})

    with pytest.raises(NotFoundException):
        await svc.update_role(
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
            body=UpdateProjectRoleRequest(name="Updated"),
        )


@pytest.mark.asyncio
async def test_create_role_unique_violation_and_with_permissions() -> None:
    import asyncpg

    repo = MagicMock()
    repo.get_role_by_slug = AsyncMock(return_value=None)
    repo.create_role = AsyncMock(
        side_effect=asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
    )
    svc = _service(repo=repo)

    from libs.shared_utils.http_exceptions import ConflictException

    with pytest.raises(ConflictException):
        await svc.create_role(
            project_id=PROJECT_ID,
            body=CreateProjectRoleRequest(name="Duplicate Role"),
        )

    repo.create_role = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "ops_lead",
            "name": "Ops Lead",
            "description": None,
            "is_system": False,
        }
    )
    repo.get_role_by_id = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "ops_lead",
            "name": "Ops Lead",
            "description": None,
            "is_system": False,
        }
    )
    repo.get_permissions_for_role = AsyncMock(return_value=[])
    repo.count_members_with_role = AsyncMock(return_value=0)
    repo.replace_role_permissions = AsyncMock()
    svc._resolve_project_permission_ids = AsyncMock(return_value=["perm-1"])  # pylint: disable=protected-access
    svc.ensure_project_role_belongs_to_project = AsyncMock(
        return_value={
            "id": ROLE_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "slug": "ops_lead",
            "name": "Ops Lead",
            "description": None,
            "is_system": False,
        }
    )

    created = await svc.create_role(
        project_id=PROJECT_ID,
        body=CreateProjectRoleRequest(name="Ops Lead", permission_ids=["perm-1"]),
    )
    assert created.slug == "ops_lead"
    repo.replace_role_permissions.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_project_permission_ids_validation() -> None:
    from libs.shared_utils.http_exceptions import BadRequestException

    svc = _service()
    svc.project_permissions_repo.get_permission_by_id = AsyncMock(return_value=None)

    with pytest.raises(BadRequestException):
        await svc._resolve_project_permission_ids(["perm-missing"])  # pylint: disable=protected-access

    svc.project_permissions_repo.get_permission_by_id = AsyncMock(
        return_value={"id": "perm-1", "code": "legacy_stale.permission"}
    )
    with pytest.raises(ValidationException):
        await svc._resolve_project_permission_ids(["perm-1"])  # pylint: disable=protected-access

    svc.project_permissions_repo.get_permission_by_id = AsyncMock(
        return_value={"id": "perm-1", "code": NOTICES_MANAGEMENT_VIEW}
    )
    resolved = await svc._resolve_project_permission_ids(["perm-1"])  # pylint: disable=protected-access
    assert resolved == ["perm-1"]


@pytest.mark.asyncio
async def test_require_org_id_and_catalog_scopable_codes() -> None:
    svc = ProjectRolesService(db_connection=MagicMock(), user_context=None)
    with pytest.raises(ValidationException):
        svc._require_org_id()  # pylint: disable=protected-access

    codes = ProjectRolesService._catalog_scopable_codes(  # pylint: disable=protected-access
        [
            {"code": NOTICES_MANAGEMENT_VIEW},
            {"code": "legacy_stale.permission"},
            {"code": None},
        ]
    )
    assert codes == [NOTICES_MANAGEMENT_VIEW]


@pytest.mark.asyncio
async def test_resolve_unique_custom_slug_invalid_and_suffix() -> None:
    from libs.shared_utils.http_exceptions import ConflictException

    svc = _service()
    with pytest.raises(ValidationException):
        await svc._resolve_unique_custom_slug(  # pylint: disable=protected-access
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            requested_slug="!!!",
            name="Bad Slug",
        )

    svc.repo.get_role_by_slug = AsyncMock(side_effect=[{"id": "existing"}, None])
    slug = await svc._resolve_unique_custom_slug(  # pylint: disable=protected-access
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        requested_slug="maintenance_lead",
        name="Maintenance Lead",
    )
    assert slug == "maintenance_lead_2"

    async def _always_taken(**kwargs):
        return {"id": "existing"}

    svc.repo.get_role_by_slug = AsyncMock(side_effect=_always_taken)
    with pytest.raises(ConflictException):
        await svc._resolve_unique_custom_slug(  # pylint: disable=protected-access
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            requested_slug="maintenance_lead",
            name="Maintenance Lead",
        )


@pytest.mark.asyncio
async def test_fetch_org_permission_codes() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"user_permissions": [NOTICES_MANAGEMENT_VIEW]})
    svc = _service()
    svc.db_connection = conn

    codes = await svc._fetch_org_permission_codes(org_id=ORG_ID)  # pylint: disable=protected-access
    assert codes == {NOTICES_MANAGEMENT_VIEW}

    conn.fetchrow = AsyncMock(return_value=None)
    empty = await svc._fetch_org_permission_codes(org_id=ORG_ID)  # pylint: disable=protected-access
    assert empty == set()
