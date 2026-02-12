"""Unit tests for TeamRepository with fake asyncpg connection."""

import pytest

from apps.user_service.app.db.repositories.team_repository import TeamRepository
from apps.user_service.app.schemas.teams import MemberData, TeamDbIn


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Init fake connection holders."""
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self.row = None
        self.val = 0

    async def fetchrow(self, query, *args):
        """Record fetchrow call."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval call."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute call."""
        self.execute_calls.append((query.strip(), args))
        return None


def _team_input(with_members=False):
    """Helper to build TeamDbIn payload."""
    members = (
        [
            MemberData(member_id="u2", additional_data=None),
            MemberData(member_id="u3", additional_data=None),
        ]
        if with_members
        else []
    )
    return TeamDbIn(
        organization_id="org1",
        name="Team",
        description="desc",
        created_by="u1",
        member_data=members,
    )


def test_build_team_filters_with_search():
    """Search adds ILIKE condition."""

    repo = TeamRepository(db_connection=None)
    where, params = repo._build_team_filters(  # pylint: disable=protected-access
        organization_id="org1",
        search="abc",
    )

    assert "ILIKE" in where
    assert params[-1] == "%abc%"


@pytest.mark.asyncio
async def test_insert_team_members_noop_on_empty():
    """No execute when member list empty."""

    conn = _FakeConn()
    repo = TeamRepository(db_connection=conn)

    await repo._insert_team_members(team_id="t1", member_data=[], added_by="u1")  # pylint: disable=protected-access

    assert not conn.execute_calls


@pytest.mark.asyncio
async def test_create_team_inserts_members():
    """create_team inserts team then adds members."""

    conn = _FakeConn()
    conn.row = {"id": "t1"}
    repo = TeamRepository(db_connection=conn)

    team_id = await repo.create_team(_team_input(with_members=True))

    assert team_id == "t1"
    # execute called for members
    assert conn.execute_calls
    # fetchrow used for team insert
    assert conn.fetchrow_calls
