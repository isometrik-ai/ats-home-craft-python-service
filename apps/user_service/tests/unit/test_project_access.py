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
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
)
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode

ORG_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


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
    repo_mock.get_active_member = AsyncMock(return_value=None)

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
