"""Unit tests for ProjectMembersService."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ProjectMemberRole, ProjectMemberStatus
from apps.user_service.app.schemas.project_members import (
    AssignProjectMemberRequest,
    UpdateProjectMemberRequest,
)
from apps.user_service.app.services.project_members_service import ProjectMembersService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

ORG_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
MEMBER_ID = "44444444-4444-4444-4444-444444444444"


def _service(**overrides) -> ProjectMembersService:
    db = MagicMock()
    ctx = UserContext(
        user_id=USER_ID,
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )
    svc = ProjectMembersService(db_connection=db, user_context=ctx)
    svc.projects_repo = overrides.get("projects_repo") or MagicMock()
    svc.org_member_repo = overrides.get("org_member_repo") or MagicMock()
    svc.setup_service = overrides.get("setup_service") or MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    svc._ensure_can_view_members = AsyncMock()
    svc._ensure_can_manage_members = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_assign_member_success():
    repo = MagicMock()
    repo.get_member = AsyncMock(return_value=None)
    repo.upsert_member = AsyncMock(
        return_value={
            "id": MEMBER_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "user_id": "u2",
            "role": "security",
            "status": "active",
        }
    )
    repo.list_members_with_profiles = AsyncMock(
        return_value=[
            {
                "id": MEMBER_ID,
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "user_id": "u2",
                "role": "security",
                "status": "active",
                "email": "guard@example.com",
            }
        ]
    )
    org_repo = MagicMock()
    org_repo.check_user_membership_by_user_id = AsyncMock(return_value=True)
    svc = _service(projects_repo=repo, org_member_repo=org_repo)

    result = await svc.assign_member(
        project_id=PROJECT_ID,
        body=AssignProjectMemberRequest(user_id="u2", role=ProjectMemberRole.SECURITY),
    )
    assert result.user_id == "u2"
    assert result.role == ProjectMemberRole.SECURITY


@pytest.mark.asyncio
async def test_assign_member_rejects_duplicate_active():
    repo = MagicMock()
    repo.get_member = AsyncMock(return_value={"status": "active"})
    org_repo = MagicMock()
    org_repo.check_user_membership_by_user_id = AsyncMock(return_value=True)
    svc = _service(projects_repo=repo, org_member_repo=org_repo)

    with pytest.raises(ConflictException):
        await svc.assign_member(
            project_id=PROJECT_ID,
            body=AssignProjectMemberRequest(user_id="u2"),
        )


@pytest.mark.asyncio
async def test_remove_member_blocks_last_community_admin():
    repo = MagicMock()
    repo.get_member = AsyncMock(
        return_value={
            "role": ProjectMemberRole.COMMUNITY_ADMIN.value,
            "status": ProjectMemberStatus.ACTIVE.value,
        }
    )
    repo.count_active_members_by_role = AsyncMock(return_value=1)
    svc = _service(projects_repo=repo)

    with pytest.raises(ValidationException):
        await svc.remove_member(project_id=PROJECT_ID, user_id="u2")


@pytest.mark.asyncio
async def test_update_member_role():
    repo = MagicMock()
    repo.get_member = AsyncMock(
        return_value={
            "id": MEMBER_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "user_id": "u2",
            "role": ProjectMemberRole.VIEWER.value,
            "status": ProjectMemberStatus.ACTIVE.value,
        }
    )
    repo.update_member = AsyncMock(
        return_value={
            "id": MEMBER_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "user_id": "u2",
            "role": ProjectMemberRole.SECURITY.value,
            "status": ProjectMemberStatus.ACTIVE.value,
        }
    )
    repo.list_members_with_profiles = AsyncMock(
        return_value=[
            {
                "id": MEMBER_ID,
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "user_id": "u2",
                "role": ProjectMemberRole.SECURITY.value,
                "status": ProjectMemberStatus.ACTIVE.value,
            }
        ]
    )
    svc = _service(projects_repo=repo)

    result = await svc.update_member(
        project_id=PROJECT_ID,
        user_id="u2",
        body=UpdateProjectMemberRequest(role=ProjectMemberRole.SECURITY),
    )
    assert result.role == ProjectMemberRole.SECURITY


@pytest.mark.asyncio
async def test_list_members_formats_joined_at_datetime():
    joined = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    repo = MagicMock()
    repo.list_members_with_profiles = AsyncMock(
        return_value=[
            {
                "id": MEMBER_ID,
                "organization_id": ORG_ID,
                "project_id": PROJECT_ID,
                "user_id": USER_ID,
                "role": ProjectMemberRole.COMMUNITY_ADMIN.value,
                "status": ProjectMemberStatus.ACTIVE.value,
                "joined_at": joined,
                "email": "admin@example.com",
            }
        ]
    )
    svc = _service(projects_repo=repo)

    result = await svc.list_members(project_id=PROJECT_ID)

    assert len(result) == 1
    assert result[0].joined_at == format_iso_datetime(joined)
