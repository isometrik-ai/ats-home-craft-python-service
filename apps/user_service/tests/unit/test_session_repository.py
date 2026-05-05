"""Unit tests for SessionRepository helper/query builder logic."""

import pytest

from apps.user_service.app.db.repositories.session_repository import (
    SessionRepository,
)
from apps.user_service.app.schemas.auth import SessionFilter


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Init fake connection holders."""
        self.fetch_calls = []
        self.fetchval_calls = []
        self.rows = []
        self.val = 0

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val


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
