"""Unit tests for SessionRepository helper/query builder logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.session_repository import (
    SessionRepository,
    get_session_repo,
    init_session_repo,
)
from apps.user_service.app.schemas.auth import SessionFilter
from apps.user_service.app.schemas.enums import SessionStatus


def _async_mock_conn(*, rows=None, row=None, val=0):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=val)
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value=None)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=0):
        """Init fake connection holders."""
        self.fetch_calls = []
        self.fetchval_calls = []
        self.fetchrow_calls = []
        self.execute_calls = []
        self.rows = rows or []
        self.row = row
        self.val = val

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


def _filters(search=None):
    """Helper to build SessionFilter with defaults."""
    return SessionFilter(page=1, page_size=10, search=search)


def test_build_session_filters_includes_search():
    """Search adds org_member conditions and params."""

    repo = SessionRepository(db_connection=None)
    where, params = repo._build_session_filters(  # pylint: disable=protected-access
        organization_id="org1",
        user_id="u1",
        filters=_filters(search="abc"),
        include_search=True,
    )

    # Note: organization_id filtering is currently disabled in implementation
    assert "us.user_id = $1" in where
    # search now includes auth.users fallback
    assert "COALESCE(om.email, au.email)" in where
    assert params[0] == "u1"
    assert params[-1] == "%abc%"


def test_build_session_filters_no_org():
    """When org is None, only user_id filter is applied."""

    repo = SessionRepository(db_connection=None)
    where, params = repo._build_session_filters(  # pylint: disable=protected-access
        organization_id=None,
        user_id="u1",
        filters=_filters(),
        include_search=False,
    )

    # Note: organization_id filtering is currently disabled in implementation
    assert "user_id = $1" in where
    assert params == ["u1"]


@pytest.mark.asyncio
async def test_get_sessions_with_count_joins_on_search():
    """Search + org triggers join query and counts."""

    conn = _FakeConn()
    conn.rows = [{"id": "s1", "user_id": "u1"}]
    conn.val = 1
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_sessions_with_count(
        organization_id="org1",
        user_id="u1",
        filters=_filters(search="abc"),
    )

    # main query always includes joins (om + au); count query adds INNER JOIN when needed
    assert "LEFT JOIN organization_members" in conn.fetch_calls[0][0]
    assert "LEFT JOIN auth.users au" in conn.fetch_calls[0][0]
    assert result["total_count"] == 1
    assert result["data"][0]["id"] == "s1"


@pytest.mark.asyncio
async def test_get_sessions_with_count_no_search():
    """No search uses base query and passes limit/offset."""

    conn = _FakeConn()
    conn.rows = [{"id": "s2", "user_id": "u1"}]
    conn.val = 2
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_sessions_with_count(
        organization_id=None,
        user_id="u1",
        filters=_filters(),
    )

    query, args = conn.fetch_calls[0]
    assert "FROM user_sessions" in query
    # default limit/page_size -> 20, offset 0
    assert args[-2:] == (20, 0)
    assert result["total_count"] == 2
    assert result["data"][0]["id"] == "s2"


def test_build_org_session_filters_search():
    """Org-wide search adds om/au predicates and ip/user_agent."""
    repo = SessionRepository(db_connection=None)
    where, params = repo._build_org_session_filters(  # pylint: disable=protected-access
        organization_id="org1",
        filters=_filters(search="desk"),
        include_search=True,
    )

    assert "us.organization_id = $1" in where
    assert "us.ip_address::text" in where
    assert params[-1] == "%desk%"


@pytest.mark.asyncio
async def test_get_org_sessions_empty_org():
    """Missing organization_id returns empty result without DB."""
    conn = _FakeConn()
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_org_sessions_with_count("", _filters())

    assert result == {"data": [], "total_count": 0}
    assert not conn.fetch_calls


@pytest.mark.asyncio
async def test_get_session_organization_id():
    """Active session lookup returns organization_id."""
    conn = _FakeConn(val="org-1")
    repo = SessionRepository(db_connection=conn)

    org_id = await repo.get_session_organization_id("sess-1")

    assert org_id == "org-1"
    query, _ = conn.fetchval_calls[0]
    assert "session_status = $2" in query


@pytest.mark.asyncio
async def test_get_valid_session_context_empty_id():
    """Blank session id short-circuits validation."""
    conn = _FakeConn()
    repo = SessionRepository(db_connection=conn)

    assert await repo.get_valid_session_context("  ") is None
    assert not conn.fetchrow_calls


@pytest.mark.asyncio
async def test_get_valid_session_context_active():
    """Valid session returns organization context dict."""
    conn = _FakeConn(row={"organization_id": "org-1"})
    repo = SessionRepository(db_connection=conn)

    ctx = await repo.get_valid_session_context("sess-1")

    assert ctx == {"organization_id": "org-1"}
    query, _ = conn.fetchrow_calls[0]
    assert "auth.sessions" in query


@pytest.mark.asyncio
async def test_revoke_org_sessions_empty_user():
    """Revoke skips DB when user_id blank."""
    conn = _FakeConn()
    repo = SessionRepository(db_connection=conn)

    assert await repo.revoke_org_sessions_for_user("", "org-1") == []
    assert not conn.fetch_calls


@pytest.mark.asyncio
async def test_get_active_session_ids_for_user():
    """User session lookup joins auth.sessions."""
    conn = _FakeConn(rows=[{"id": "s1"}, {"id": "s2"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.get_active_session_ids_for_user("u1")

    assert ids == ["s1", "s2"]
    query, _ = conn.fetch_calls[0]
    assert "INNER JOIN auth.sessions" in query


def test_build_session_filters_status_and_login_method():
    """Session status and login_method add predicates."""
    repo = SessionRepository(db_connection=None)
    filters = SessionFilter(
        page=1,
        page_size=10,
        session_status=SessionStatus.ACTIVE.value,
        login_method="password",
    )
    where, params = repo._build_session_filters(  # pylint: disable=protected-access
        organization_id="org1",
        user_id="u1",
        filters=filters,
        include_search=False,
    )

    assert "session_status = $3" in where
    assert "login_method = $4" in where
    assert params == ["u1", "org1", SessionStatus.ACTIVE.value, "password"]


@pytest.mark.asyncio
async def test_get_sessions_with_count_search_no_org_join():
    """Search without org uses auth.users join in count query."""
    conn = _async_mock_conn(rows=[{"id": "s1"}], val=1)
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_sessions_with_count(
        organization_id=None,
        user_id="u1",
        filters=_filters(search="desk"),
    )

    assert result["total_count"] == 1
    count_query, _ = _sql_args(conn.fetchval)
    assert "LEFT JOIN auth.users au" in count_query


@pytest.mark.asyncio
async def test_get_org_sessions_with_count_no_search():
    """Org-wide listing without search uses simpler count query."""
    conn = _async_mock_conn(rows=[{"id": "s1"}], val=3)
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_org_sessions_with_count("org1", _filters())

    assert result["total_count"] == 3
    list_query, _ = _sql_args(conn.fetch)
    assert "FROM user_sessions us" in list_query
    count_query, _ = _sql_args(conn.fetchval)
    assert "COUNT(*)" in count_query


@pytest.mark.asyncio
async def test_get_org_sessions_with_count_with_search():
    """Org-wide search uses INNER JOIN on organization_members."""
    conn = _async_mock_conn(rows=[{"id": "s1"}], val=1)
    repo = SessionRepository(db_connection=conn)

    result = await repo.get_org_sessions_with_count("org1", _filters(search="alice"))

    assert len(result["data"]) == 1
    list_query, _ = _sql_args(conn.fetch)
    assert "INNER JOIN organization_members om" in list_query


@pytest.mark.asyncio
async def test_get_valid_session_context_null_org():
    """Valid session with null organization_id returns None org."""
    conn = _async_mock_conn(row={"organization_id": None})
    repo = SessionRepository(db_connection=conn)

    ctx = await repo.get_valid_session_context("sess-1")

    assert ctx == {"organization_id": None}


@pytest.mark.asyncio
async def test_check_session_has_organization():
    """Session org check returns row dict when found."""
    conn = _async_mock_conn(row={"organization_id": "org-1"})
    repo = SessionRepository(db_connection=conn)

    result = await repo.check_session_has_organization("sess-1")

    assert result == {"organization_id": "org-1"}


@pytest.mark.asyncio
async def test_update_session_organization_context():
    """Update session org context executes scoped UPDATE."""
    conn = _async_mock_conn()
    repo = SessionRepository(db_connection=conn)

    await repo.update_session_organization_context("sess-1", "u1", "org-1")

    query, args = _sql_args(conn.execute)
    assert "UPDATE user_sessions" in query
    assert args == ("org-1", "sess-1", "u1", SessionStatus.ACTIVE.value)


@pytest.mark.asyncio
async def test_delete_auth_session_by_id():
    """Delete auth session revokes Supabase row."""
    conn = _async_mock_conn()
    repo = SessionRepository(db_connection=conn)

    await repo.delete_auth_session_by_id("sess-1")

    query, args = _sql_args(conn.execute)
    assert "DELETE FROM auth.sessions" in query
    assert args == ("sess-1",)


@pytest.mark.asyncio
async def test_delete_auth_session_returns_context():
    """Delete auth session returns session and org ids."""
    conn = _async_mock_conn(row={"session_id": "sess-1", "organization_id": "org-1"})
    repo = SessionRepository(db_connection=conn)

    result = await repo.delete_auth_session("sess-1", "u1")

    assert result == {"session_id": "sess-1", "organization_id": "org-1"}


@pytest.mark.asyncio
async def test_get_active_session_ids_for_org_member_removal():
    """Org removal lookup includes org-bound and unscoped sessions."""
    conn = _async_mock_conn(rows=[{"id": "s1"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.get_active_session_ids_for_org_member_removal("u1", "org-1")

    assert ids == ["s1"]
    query, _ = _sql_args(conn.fetch)
    assert "organization_id IS NULL" in query


@pytest.mark.asyncio
async def test_get_active_session_ids_for_user_org_alias():
    """Alias delegates to org member removal lookup."""
    conn = _async_mock_conn(rows=[{"id": "s2"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.get_active_session_ids_for_user_org("u1", "org-1")

    assert ids == ["s2"]


@pytest.mark.asyncio
async def test_get_active_session_ids_for_organization():
    """Org session lookup includes unscoped member sessions."""
    conn = _async_mock_conn(rows=[{"id": "s3"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.get_active_session_ids_for_organization("org-1")

    assert ids == ["s3"]
    query, _ = _sql_args(conn.fetch)
    assert "organization_members om" in query


@pytest.mark.asyncio
async def test_revoke_org_sessions_for_user():
    """Revoke deletes auth sessions for org-bound user sessions."""
    conn = _async_mock_conn(rows=[{"id": "s1"}, {"id": "s2"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.revoke_org_sessions_for_user("u1", "org-1")

    assert ids == ["s1", "s2"]
    query, _ = _sql_args(conn.fetch)
    assert "DELETE FROM auth.sessions" in query


@pytest.mark.asyncio
async def test_revoke_all_sessions_for_organization():
    """Org-wide revoke deletes sessions for members and org context."""
    conn = _async_mock_conn(rows=[{"id": "s9"}])
    repo = SessionRepository(db_connection=conn)

    ids = await repo.revoke_all_sessions_for_organization("org-1")

    assert ids == ["s9"]


def test_init_and_get_session_repo_singleton():
    """Session repo helpers return shared singleton instance."""
    repo = init_session_repo()
    assert get_session_repo() is repo
