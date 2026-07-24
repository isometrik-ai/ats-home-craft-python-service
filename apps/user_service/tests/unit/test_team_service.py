"""Unit tests for TeamService member-wise role handling and CRUD flows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
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
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    DuplicateValueException,
    NotFoundException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_A = "550e8400-e29b-41d4-a716-446655440000"
USER_B = "660e8400-e29b-41d4-a716-446655440001"
USER_C = "770e8400-e29b-41d4-a716-446655440002"
TEAM_ID = "880e8400-e29b-41d4-a716-446655440003"
MEMBER_ID = USER_C
MEMBER_ID_2 = "990e8400-e29b-41d4-a716-446655440004"


def _ctx() -> UserContext:
    """Build a reusable UserContext for team tests."""
    return UserContext(
        user_id="u1",
        email="admin@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


class _FakeTeamRepo:
    """Configurable fake TeamRepository."""

    def __init__(
        self,
        *,
        created_team_id: str = TEAM_ID,
        teams: list[dict[str, Any]] | None = None,
        total: int | None = None,
        team_data: dict[str, Any] | None = None,
        members: list[dict[str, Any]] | None = None,
        member_ids: list[str] | None = None,
        name_unique: bool = True,
        members_valid: bool = True,
    ) -> None:
        self.created_team_id = created_team_id
        self.teams = teams or []
        self.total = total if total is not None else len(self.teams)
        self.team_data = team_data
        self.members = members or []
        self.member_ids = member_ids or []
        self.name_unique = name_unique
        self.members_valid = members_valid
        self.last_create: Any = None
        self.last_update: Any = None
        self.last_delete: Any = None

    async def create_team(self, db_in):
        """Record create payload and return team id."""
        self.last_create = db_in
        return self.created_team_id

    async def get_teams_list(self, **kwargs):
        """Return paginated teams."""
        del kwargs
        return self.teams, self.total

    async def get_team_detail(self, **kwargs):
        """Return team row and members."""
        del kwargs
        return self.team_data, self.members

    async def get_team_member_ids(self, team_id, organization_id):
        """Return current member ids."""
        del team_id, organization_id
        return self.member_ids

    async def update_team(self, update_input):
        """Record update payload."""
        self.last_update = update_input

    async def delete_team_and_members(self, delete_input):
        """Record delete payload."""
        self.last_delete = delete_input

    async def check_team_name_unique(self, new_name, organization_id, team_id=None):
        """Return configured uniqueness result."""
        del new_name, organization_id, team_id
        return self.name_unique

    async def validate_organization_members(self, member_ids, organization_id):
        """Return configured member validation result."""
        del member_ids, organization_id
        return self.members_valid


def _service(repo: _FakeTeamRepo) -> TeamService:
    """Build TeamService with fake repository."""
    service = TeamService(user_context=_ctx(), db_connection=MagicMock())
    service.team_repository = repo
    return service


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


@pytest.mark.asyncio
async def test_create_team_with_members():
    """Create team validates members and persists team."""
    repo = _FakeTeamRepo()
    service = _service(repo)
    request = CreateTeamRequest(
        name="Sales Team",
        description="Revenue",
        members=[
            TeamMemberInput(user_id=MEMBER_ID),
            TeamMemberInput(user_id=MEMBER_ID_2),
        ],
    )

    team_id = await service.create_team(request)

    assert team_id == TEAM_ID
    assert repo.last_create.name == "Sales Team"
    assert len(repo.last_create.member_data) == 2


@pytest.mark.asyncio
async def test_create_team_duplicate_name():
    """Duplicate team name raises DuplicateValueException."""
    repo = _FakeTeamRepo(name_unique=False)
    service = _service(repo)

    with pytest.raises(DuplicateValueException):
        await service.create_team(CreateTeamRequest(name="Sales Team"))


@pytest.mark.asyncio
async def test_list_teams_maps_response():
    """List teams maps repository rows to response models."""
    now = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
    repo = _FakeTeamRepo(
        teams=[
            {
                "id": TEAM_ID,
                "name": "Sales Team",
                "description": "Revenue",
                "member_count": 3,
                "created_at": now,
                "updated_at": now,
            }
        ],
        total=1,
    )
    service = _service(repo)

    response = await service.list_teams(page=1, page_size=20, search="Sales")

    assert response.total_count == 1
    assert response.data[0].id == TEAM_ID
    assert response.data[0].member_count == 3


@pytest.mark.asyncio
async def test_get_team_detail_not_found():
    """Missing team raises NotFoundException."""
    repo = _FakeTeamRepo(team_data=None, members=[])
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.get_team_detail(TEAM_ID)


@pytest.mark.asyncio
async def test_get_team_detail_success():
    """Team detail includes member items."""
    now = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
    repo = _FakeTeamRepo(
        team_data={
            "id": TEAM_ID,
            "name": "Sales Team",
            "description": "Revenue",
            "created_at": now,
            "updated_at": now,
        },
        members=[
            {
                "id": MEMBER_ID,
                "name": "Alex",
                "email": "alex@example.com",
                "role": TeamRoles.MEMBER.value,
                "added_at": now,
            }
        ],
    )
    service = _service(repo)

    response = await service.get_team_detail(TEAM_ID)

    assert response.data.id == TEAM_ID
    assert response.data.members[0].email == "alex@example.com"


@pytest.mark.asyncio
async def test_update_team_computes_member_changes():
    """Update team computes member adds/removes and validates ids."""
    repo = _FakeTeamRepo(member_ids=[MEMBER_ID])
    service = _service(repo)

    await service.update_team(
        TEAM_ID,
        UpdateTeamRequest(
            name="Updated Team",
            members=[TeamMemberInput(user_id=MEMBER_ID_2)],
        ),
    )

    assert repo.last_update.members_to_add[0].member_id == MEMBER_ID_2
    assert repo.last_update.members_to_remove == [MEMBER_ID]


@pytest.mark.asyncio
async def test_update_team_invalid_member_ids():
    """Invalid member ids raise BadRequestException."""
    repo = _FakeTeamRepo(members_valid=False)
    service = _service(repo)

    with pytest.raises(BadRequestException):
        await service.update_team(
            TEAM_ID,
            UpdateTeamRequest(members=[TeamMemberInput(user_id=MEMBER_ID)]),
        )


@pytest.mark.asyncio
async def test_delete_team():
    """Delete team delegates to repository."""
    repo = _FakeTeamRepo()
    service = _service(repo)

    await service.delete_team(TEAM_ID)

    assert repo.last_delete.team_id == TEAM_ID
    assert repo.last_delete.organization_id == ORG_ID
