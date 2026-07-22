"""Unit tests for TeamService member-wise role handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import TeamRoles
from apps.user_service.app.schemas.teams import (
    CreateTeamRequest,
    TeamMemberInput,
    UpdateTeamRequest,
)
from apps.user_service.app.services.team_service import TeamService
from apps.user_service.app.utils.common_utils import UserContext

USER_A = "550e8400-e29b-41d4-a716-446655440000"
USER_B = "660e8400-e29b-41d4-a716-446655440001"
USER_C = "770e8400-e29b-41d4-a716-446655440002"
TEAM_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ctx() -> UserContext:
    """Build a reusable UserContext for team tests."""
    return UserContext(
        user_id="u1",
        email="admin@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_create_team_member_roles():
    """Create team stores per-member roles."""
    service = TeamService(user_context=_ctx(), db_connection=MagicMock())
    service._validate_team_name = AsyncMock()  # pylint: disable=protected-access
    service._validate_member_ids = AsyncMock()  # pylint: disable=protected-access
    service.team_repository = MagicMock()
    service.team_repository.create_team = AsyncMock(return_value="team-1")

    body = CreateTeamRequest(
        name="Sales",
        members=[
            TeamMemberInput(user_id=USER_A, role=TeamRoles.LEAD),
            TeamMemberInput(user_id=USER_B),
        ],
    )
    team_id = await service.create_team(body)

    assert team_id == "team-1"
    db_in = service.team_repository.create_team.await_args.args[0]
    assert db_in.member_data[0].role == TeamRoles.LEAD
    assert db_in.member_data[1].role == TeamRoles.MEMBER


def test_compute_member_sync():
    """Update sync adds, removes, and updates roles."""
    service = TeamService(user_context=_ctx(), db_connection=MagicMock())
    to_add, to_remove, to_update = service._compute_member_sync(  # pylint: disable=protected-access
        {USER_A, USER_B},
        [
            TeamMemberInput(user_id=USER_A, role=TeamRoles.PROJECT_LEAD),
            TeamMemberInput(user_id=USER_C, role=TeamRoles.LEAD),
        ],
    )

    assert [member.member_id for member in to_add] == [USER_C]
    assert to_add[0].role == TeamRoles.LEAD
    assert to_remove == [USER_B]
    assert [member.member_id for member in to_update] == [USER_A]
    assert to_update[0].role == TeamRoles.PROJECT_LEAD


@pytest.mark.asyncio
async def test_update_team_syncs_roles():
    """Update team forwards role updates for existing members."""
    service = TeamService(user_context=_ctx(), db_connection=MagicMock())
    service._validate_team_name = AsyncMock()  # pylint: disable=protected-access
    service._validate_member_ids = AsyncMock()  # pylint: disable=protected-access
    service.team_repository = MagicMock()
    service.team_repository.get_team_member_ids = AsyncMock(return_value=[USER_A, USER_B])
    service.team_repository.update_team = AsyncMock()

    body = UpdateTeamRequest(
        members=[
            TeamMemberInput(user_id=USER_A, role=TeamRoles.TECH_LEAD),
            TeamMemberInput(user_id=USER_C),
        ],
    )
    await service.update_team(TEAM_ID, body)

    update_in = service.team_repository.update_team.await_args.args[0]
    assert update_in.members_to_remove == [USER_B]
    assert update_in.members_to_add[0].member_id == USER_C
    assert update_in.members_to_update[0].member_id == USER_A
    assert update_in.members_to_update[0].role == TeamRoles.TECH_LEAD
