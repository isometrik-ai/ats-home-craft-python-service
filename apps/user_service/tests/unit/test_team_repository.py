"""Unit tests for TeamRepository with fake asyncpg connection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.team_repository import TeamRepository
from apps.user_service.app.schemas.teams import (
    MemberData,
    TeamDbDelete,
    TeamDbIn,
    TeamDbUpdate,
)
from libs.shared_utils.http_exceptions import NotFoundException


def _async_mock_conn(*, row=None, rows=None, val=0, execute_result=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, row=None, rows=None, val=0):
        """Init fake connection holders."""
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.fetch_calls = []
        self.execute_calls = []
        self.row = row
        self.rows = rows or []
        self.val = val

    async def fetch(self, query, *args):
        """Record fetch call."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

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


@pytest.mark.asyncio
async def test_get_teams_list_pagination():
    """get_teams_list applies search, limit, and offset."""
    conn = _FakeConn(rows=[{"id": "t1", "member_count": 2}], val=1)
    repo = TeamRepository(db_connection=conn)

    teams, total = await repo.get_teams_list("org1", search="legal", page=2, page_size=5)

    assert total == 1
    assert teams[0]["id"] == "t1"
    list_query, list_args = conn.fetch_calls[0]
    assert "ILIKE" in list_query
    assert list_args[-2:] == (5, 5)


@pytest.mark.asyncio
async def test_get_team_detail_not_found():
    """Missing team returns None and empty members."""
    conn = _FakeConn(row=None)
    repo = TeamRepository(db_connection=conn)

    team, members = await repo.get_team_detail("t1", "org1")

    assert team is None
    assert members == []


@pytest.mark.asyncio
async def test_check_team_name_unique():
    """Unique check inverts EXISTS result."""
    conn = _FakeConn(val=False)
    repo = TeamRepository(db_connection=conn)

    assert await repo.check_team_name_unique("Legal", "org1") is True
    query, args = conn.fetchval_calls[0]
    assert "LOWER(name) = LOWER($1)" in query
    assert args[:2] == ("Legal", "org1")


@pytest.mark.asyncio
async def test_validate_organization_members():
    """All user ids must match distinct count."""
    conn = _FakeConn(val=2)
    repo = TeamRepository(db_connection=conn)

    ok = await repo.validate_organization_members(["u1", "u2"], "org1")

    assert ok is True
    query, _ = conn.fetchval_calls[0]
    assert "user_id = ANY($2::uuid[])" in query


@pytest.mark.asyncio
async def test_delete_team_members_noop_on_empty():
    """delete_team_members_by_user_ids skips execute when empty."""
    conn = _FakeConn()
    repo = TeamRepository(db_connection=conn)

    await repo.delete_team_members_by_user_ids("t1", [])
    assert not conn.execute_calls


@pytest.mark.asyncio
async def test_get_team_member_ids():
    """Member ids scoped via teams join."""
    conn = _FakeConn(rows=[{"user_id": "u1"}, {"user_id": "u2"}])
    repo = TeamRepository(db_connection=conn)

    ids = await repo.get_team_member_ids("t1", "org1")

    assert ids == ["u1", "u2"]
    query, _ = conn.fetch_calls[0]
    assert "INNER JOIN teams t" in query


@pytest.mark.asyncio
async def test_update_team_name_only():
    """update_team builds dynamic SET for name."""
    conn = _FakeConn(row={"id": "t1"})
    repo = TeamRepository(db_connection=conn)

    await repo.update_team(
        TeamDbUpdate(
            team_id="t1",
            organization_id="org1",
            added_by="u1",
            name="Renamed",
        )
    )

    query, args = conn.fetchrow_calls[0]
    assert "UPDATE teams" in query
    assert "name = $1" in query
    assert args[0] == "Renamed"


@pytest.mark.asyncio
async def test_create_team_without_members():
    """create_team skips member insert when member_data empty."""
    conn = _async_mock_conn(row={"id": "t1"})
    repo = TeamRepository(db_connection=conn)

    team_id = await repo.create_team(_team_input(with_members=False))

    assert team_id == "t1"
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_team_member_count():
    """_get_team_member_count queries team_members count."""
    conn = _async_mock_conn(val=4)
    repo = TeamRepository(db_connection=conn)

    count = await repo._get_team_member_count("t1")  # pylint: disable=protected-access

    assert count == 4


def test_extract_team_and_member_data():
    """Extract helpers map DB rows to API shapes."""
    repo = TeamRepository(db_connection=None)
    team = repo._extract_team_data(  # pylint: disable=protected-access
        {"id": "t1", "name": "Legal", "description": "d", "created_at": 1, "updated_at": 2}
    )
    member = repo._extract_member_data(  # pylint: disable=protected-access
        {
            "user_id": "u1",
            "email": "a@b.com",
            "first_name": "Jane",
            "last_name": None,
            "role": "member",
            "added_at": 1,
            "additional_data": None,
        }
    )

    assert team["name"] == "Legal"
    assert member["last_name"] == ""
    assert member["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_get_team_detail_found():
    """Found team returns team dict and extracted members."""
    conn = _async_mock_conn()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": "t1",
            "name": "Sales",
            "description": "d",
            "created_at": 1,
            "updated_at": 2,
        }
    )
    conn.fetch = AsyncMock(
        return_value=[
            {
                "user_id": "u1",
                "email": "a@b.com",
                "first_name": "A",
                "last_name": "B",
                "role": "member",
                "added_at": 1,
                "additional_data": None,
            }
        ]
    )
    repo = TeamRepository(db_connection=conn)

    team, members = await repo.get_team_detail("t1", "org1")

    assert team["name"] == "Sales"
    assert members[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_check_team_name_unique_exclude_self():
    """Unique check excludes current team id during update."""
    conn = _async_mock_conn(val=True)
    repo = TeamRepository(db_connection=conn)

    unique = await repo.check_team_name_unique("Legal", "org1", team_id="t1")

    assert unique is False
    query, args = _sql_args(conn.fetchval)
    assert "id != $3" in query
    assert args[2] == "t1"


@pytest.mark.asyncio
async def test_validate_organization_members_empty():
    """Empty user list validates as True."""
    conn = _async_mock_conn()
    repo = TeamRepository(db_connection=conn)

    assert await repo.validate_organization_members([], "org1") is True
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_update_team_add_and_remove_members():
    """update_team adds and removes members after field update."""
    conn = _async_mock_conn(row={"id": "t1"})
    repo = TeamRepository(db_connection=conn)

    await repo.update_team(
        TeamDbUpdate(
            team_id="t1",
            organization_id="org1",
            added_by="u1",
            name="Updated",
            members_to_add=["u2"],
            members_to_remove=["u3"],
        )
    )

    assert conn.fetchrow.await_count == 1
    assert conn.execute.await_count == 2


@pytest.mark.asyncio
async def test_update_team_not_found_raises():
    """Missing team raises NotFoundException."""
    conn = _async_mock_conn(row=None)
    repo = TeamRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.update_team(
            TeamDbUpdate(team_id="t1", organization_id="org1", added_by="u1", name="X")
        )


@pytest.mark.asyncio
async def test_update_team_members_additional_data():
    """Additional data update merges jsonb and role."""
    conn = _async_mock_conn()
    repo = TeamRepository(db_connection=conn)

    await repo.update_team_members_additional_data(
        team_id="t1",
        organization_id="org1",
        updates=[{"user_id": "u1", "role": "lead", "hourly_rate": 100}],
    )

    query, args = _sql_args(conn.execute)
    assert "additional_data" in query
    assert args[1] == "u1"


@pytest.mark.asyncio
async def test_delete_team_and_members():
    """Soft delete team then hard delete members."""
    conn = _async_mock_conn(row={"id": "t1"})
    repo = TeamRepository(db_connection=conn)

    await repo.delete_team_and_members(TeamDbDelete(team_id="t1", organization_id="org1"))

    assert conn.fetchrow.await_count == 1
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_all_teams_by_organization_id():
    """Org delete parses execute status for row count."""
    conn = _async_mock_conn(execute_result="DELETE 5")
    repo = TeamRepository(db_connection=conn)

    deleted = await repo.delete_all_teams_by_organization_id("org1")

    assert deleted == 5


@pytest.mark.asyncio
async def test_delete_user_from_all_teams():
    """User removal deletes team_members via teams join."""
    conn = _async_mock_conn()
    repo = TeamRepository(db_connection=conn)

    await repo.delete_user_from_all_teams("u1", "org1")

    query, args = _sql_args(conn.execute)
    assert "DELETE FROM team_members tm" in query
    assert args == ("u1", "org1")
