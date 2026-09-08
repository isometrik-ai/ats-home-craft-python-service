"""Unit tests for staff project access helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.utils.common_utils import (
    UserContext,
    ensure_staff_project_access,
    require_any_permission,
    user_has_any_permission,
)
from libs.shared_utils.common_query import (
    PROJECT_SETUP_EDIT,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    VISITOR_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode

ORG_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
ROLE_ID = "44444444-4444-4444-4444-444444444444"


def _user_context() -> UserContext:
    return UserContext(
        user_id=USER_ID,
        email="staff@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_require_any_permission_accepts_first_match():
    db = MagicMock()
    ctx = _user_context()
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        new=AsyncMock(side_effect=[False, True]),
    ):
        await require_any_permission(
            permission_codes=[PROJECTS_MANAGEMENT_VIEW, PROJECTS_MANAGEMENT_VIEW_ASSIGNED],
            user_context=ctx,
            db_connection=db,
            organization_id=ORG_ID,
        )


@pytest.mark.asyncio
async def test_require_any_permission_raises_when_none_match():
    db = MagicMock()
    ctx = _user_context()
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ForbiddenException):
            await require_any_permission(
                permission_codes=[PROJECTS_MANAGEMENT_VIEW],
                user_context=ctx,
                db_connection=db,
                organization_id=ORG_ID,
            )


@pytest.mark.asyncio
async def test_ensure_staff_project_access_org_wide_bypasses_membership():
    db = MagicMock()
    current_user = {"sub": USER_ID}
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})

    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new=AsyncMock(return_value=_user_context()),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.require_any_permission",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            new=AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"] == [PROJECTS_MANAGEMENT_VIEW]
            ),
        ),
    ):
        ctx = await ensure_staff_project_access(
            current_user=current_user,
            db_connection=db,
            project_id=PROJECT_ID,
            permission_codes=PROJECTS_MANAGEMENT_VIEW,
        )
        assert ctx.organization_id == ORG_ID
        setup_mock.ensure_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_staff_project_access_assigned_requires_membership():
    db = MagicMock()
    current_user = {"sub": USER_ID}
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    repo_mock = MagicMock()
    repo_mock.get_active_member_with_role = AsyncMock(return_value=None)

    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new=AsyncMock(return_value=_user_context()),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.require_any_permission",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository",
            return_value=repo_mock,
        ),
    ):
        with pytest.raises(ForbiddenException) as exc:
            await ensure_staff_project_access(
                current_user=current_user,
                db_connection=db,
                project_id=PROJECT_ID,
                permission_codes=PROJECTS_MANAGEMENT_VIEW,
            )
        assert exc.value.custom_code == CustomStatusCode.FORBIDDEN


@pytest.mark.asyncio
async def test_ensure_staff_project_access_assigned_requires_project_role_permission():
    db = MagicMock()
    current_user = {"sub": USER_ID}
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    repo_mock = MagicMock()
    repo_mock.get_active_member_with_role = AsyncMock(
        return_value={
            "project_role_id": ROLE_ID,
            "role_slug": "viewer",
            "user_id": USER_ID,
        }
    )
    roles_repo_mock = MagicMock()
    roles_repo_mock.get_permission_codes_for_role = AsyncMock(
        return_value={PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    )

    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new=AsyncMock(return_value=_user_context()),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.require_any_permission",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            new=AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"]
                == [PROJECTS_MANAGEMENT_VIEW_ASSIGNED]
            ),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository",
            return_value=repo_mock,
        ),
        patch(
            "apps.user_service.app.db.repositories.project_roles_repository.ProjectRolesRepository",
            return_value=roles_repo_mock,
        ),
    ):
        with pytest.raises(ForbiddenException) as exc:
            await ensure_staff_project_access(
                current_user=current_user,
                db_connection=db,
                project_id=PROJECT_ID,
                permission_codes=VISITOR_MANAGEMENT_VIEW,
            )
        assert exc.value.message_key == "errors.insufficient_permissions"


@pytest.mark.asyncio
async def test_ensure_staff_project_access_assigned_allows_matching_project_role():
    db = MagicMock()
    current_user = {"sub": USER_ID}
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    repo_mock = MagicMock()
    repo_mock.get_active_member_with_role = AsyncMock(
        return_value={
            "project_role_id": ROLE_ID,
            "role_slug": "security",
            "user_id": USER_ID,
        }
    )
    roles_repo_mock = MagicMock()
    roles_repo_mock.get_permission_codes_for_role = AsyncMock(
        return_value={VISITOR_MANAGEMENT_VIEW, PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    )

    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new=AsyncMock(return_value=_user_context()),
        ),
        patch(
            "apps.user_service.app.utils.common_utils.require_any_permission",
            new=AsyncMock(),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            new=AsyncMock(
                side_effect=lambda **kwargs: kwargs["permission_code"]
                in ([PROJECTS_MANAGEMENT_VIEW_ASSIGNED], [VISITOR_MANAGEMENT_VIEW])
            ),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository",
            return_value=repo_mock,
        ),
        patch(
            "apps.user_service.app.db.repositories.project_roles_repository.ProjectRolesRepository",
            return_value=roles_repo_mock,
        ),
    ):
        ctx = await ensure_staff_project_access(
            current_user=current_user,
            db_connection=db,
            project_id=PROJECT_ID,
            permission_codes=VISITOR_MANAGEMENT_VIEW,
        )
        assert ctx.project_member_role == "security"


@pytest.mark.asyncio
async def test_ensure_staff_project_access_legacy_org_edit_satisfies_setup_ceiling():
    """Legacy org projects_management.edit satisfies project_setup.edit ceiling."""
    db = MagicMock()
    current_user = {"sub": USER_ID}
    setup_mock = MagicMock()
    setup_mock.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    repo_mock = MagicMock()
    repo_mock.get_active_member_with_role = AsyncMock(
        return_value={
            "project_role_id": ROLE_ID,
            "role_slug": "community_admin",
            "user_id": USER_ID,
        }
    )
    roles_repo_mock = MagicMock()
    roles_repo_mock.get_permission_codes_for_role = AsyncMock(
        return_value={PROJECT_SETUP_EDIT, PROJECTS_MANAGEMENT_VIEW_ASSIGNED}
    )

    async def _access_side_effect(**kwargs):
        code = kwargs["permission_code"][0]
        return code in {
            PROJECTS_MANAGEMENT_EDIT,
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        }

    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new=AsyncMock(return_value=_user_context()),
        ),
        patch(
            "apps.user_service.app.services.project_setup_service.ProjectSetupService",
            return_value=setup_mock,
        ),
        patch(
            "apps.user_service.app.utils.common_utils.check_user_access_async",
            new=AsyncMock(side_effect=_access_side_effect),
        ),
        patch(
            "apps.user_service.app.db.repositories.projects_repository.ProjectsRepository",
            return_value=repo_mock,
        ),
        patch(
            "apps.user_service.app.db.repositories.project_roles_repository.ProjectRolesRepository",
            return_value=roles_repo_mock,
        ),
    ):
        ctx = await ensure_staff_project_access(
            current_user=current_user,
            db_connection=db,
            project_id=PROJECT_ID,
            permission_codes=PROJECT_SETUP_EDIT,
        )
        assert ctx.project_member_role == "community_admin"


@pytest.mark.asyncio
async def test_user_has_any_permission():
    db = MagicMock()
    ctx = _user_context()
    with patch(
        "apps.user_service.app.utils.common_utils.check_user_access_async",
        new=AsyncMock(side_effect=[False, True]),
    ):
        assert await user_has_any_permission(
            permission_codes=["a", "b"],
            user_context=ctx,
            db_connection=db,
            organization_id=ORG_ID,
        )
