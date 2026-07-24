"""Unit tests for OrganizationMemberRepository with fake connection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.schemas.enums import OrganizationMemberStatus


def _async_mock_conn(*, row=None, rows=None, execute_result="UPDATE 1"):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, row=None, rows=None, result="UPDATE 1"):
        self.row = row
        self.rows = rows or []
        self.result = result
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.execute_calls = []

    async def fetchrow(self, query, *args):
        """Record fetchrow call."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetch(self, query, *args):
        """Record fetch call."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def execute(self, query, *args):
        """Record execute call."""
        self.execute_calls.append((query.strip(), args))
        return self.result


@pytest.mark.asyncio
async def test_add_member_without_id():
    """add_member upserts when no explicit member id."""
    conn = _FakeConn(row={"id": "m1", "email": "a@b.com"})
    repo = OrganizationMemberRepository(db_connection=conn)

    result = await repo.add_member(
        "org-1",
        {"user_id": "u1", "email": "a@b.com", "role_id": "r1"},
    )

    assert result["id"] == "m1"
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO organization_members" in query
    assert "ON CONFLICT (user_id, organization_id)" in query


@pytest.mark.asyncio
async def test_check_user_exists():
    """check_user_exists returns True when row found."""
    conn = _FakeConn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    assert await repo.check_user_exists("a@b.com", "org-1") is True
    query, args = conn.fetchrow_calls[0]
    assert "status != $3" in query
    assert args[-1] == OrganizationMemberStatus.DELETED.value


@pytest.mark.asyncio
async def test_get_user_role_id():
    """get_user_role_id returns role_id string."""
    conn = _FakeConn(row={"role_id": "role-1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    role_id = await repo.get_user_role_id("u1", "org-1")

    assert role_id == "role-1"
    query, _ = conn.fetchrow_calls[0]
    assert "SELECT role_id" in query


@pytest.mark.asyncio
async def test_get_member_id_by_user_id():
    """get_member_id_by_user_id returns membership id."""
    conn = _FakeConn(row={"id": "mem-1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    member_id = await repo.get_member_id_by_user_id("u1", "org-1")

    assert member_id == "mem-1"


@pytest.mark.asyncio
async def test_update_user_info_empty():
    """Empty update_data returns None without query."""
    conn = _FakeConn()
    repo = OrganizationMemberRepository(db_connection=conn)

    assert await repo.update_user_info("u1", "org-1", {}) is None
    assert not conn.fetchrow_calls


@pytest.mark.asyncio
async def test_suspend_user():
    """suspend_user updates status via shared helper."""
    conn = _FakeConn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    ok = await repo.suspend_user("u1", "org-1")

    assert ok is True
    query, args = conn.fetchrow_calls[0]
    assert args[0] == OrganizationMemberStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_get_users_details_list_with_search():
    """User list applies search ILIKE and pagination."""
    conn = _FakeConn(rows=[{"id": "m1", "alternate_emails": "[]"}])
    repo = OrganizationMemberRepository(db_connection=conn)

    rows = await repo.get_users_details_list("org-1", search="alice", limit=5, offset=10)

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "ILIKE" in query
    assert args[-2:] == (5, 10)


@pytest.mark.asyncio
async def test_delete_member_by_user_id():
    """Soft delete sets status to deleted."""
    conn = _FakeConn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    ok = await repo.delete_member_by_user_id("u1", "org-1")

    assert ok is True
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE organization_members" in query
    assert args[-1] == OrganizationMemberStatus.DELETED.value


@pytest.mark.asyncio
async def test_add_member_with_explicit_id():
    """add_member includes id column when member_data has id."""
    conn = _async_mock_conn(row={"id": "m99", "email": "x@y.com"})
    repo = OrganizationMemberRepository(db_connection=conn)

    result = await repo.add_member(
        "org-1",
        {"id": "m99", "user_id": "u1", "email": "x@y.com"},
    )

    assert result["id"] == "m99"
    query, args = _sql_args(conn.fetchrow)
    assert "INSERT INTO organization_members" in query
    assert args[0] == "m99"


@pytest.mark.asyncio
async def test_get_user_profile_by_id_with_org():
    """Profile lookup adds organization filter and excludes deleted."""
    conn = _async_mock_conn(row={"user_id": "u1", "email": "a@b.com"})
    repo = OrganizationMemberRepository(db_connection=conn)

    profile = await repo.get_user_profile_by_id("u1", "org-1")

    assert profile["user_id"] == "u1"
    query, args = _sql_args(conn.fetchrow)
    assert "organization_id = $2" in query
    assert args[-1] == OrganizationMemberStatus.DELETED.value


@pytest.mark.asyncio
async def test_fetch_context_for_member_role_change():
    """Role-change context loads org and member rows in one query."""
    conn = _async_mock_conn(row={"created_by_id": "owner", "target_user_id": "u2"})
    repo = OrganizationMemberRepository(db_connection=conn)

    ctx = await repo.fetch_context_for_member_role_change("org-1", "u1", "u2", "role-new")

    assert ctx["created_by_id"] == "owner"
    query, _ = _sql_args(conn.fetchrow)
    assert "FROM organizations o" in query


@pytest.mark.asyncio
async def test_get_user_role_id_empty_user():
    """Blank user_id short-circuits without DB."""
    conn = _async_mock_conn()
    repo = OrganizationMemberRepository(db_connection=conn)

    assert await repo.get_user_role_id("") is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_active_membership_isometrik_user_id():
    """Membership lookup returns isometrik_user_id tuple."""
    conn = _async_mock_conn(row={"isometrik_user_id": "iso-1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    is_member, iso_id = await repo.get_active_membership_isometrik_user_id("u1", "org-1")

    assert is_member is True
    assert iso_id == "iso-1"


@pytest.mark.asyncio
async def test_get_active_membership_disallow_suspended():
    """disallow_suspended adds extra status predicate."""
    conn = _async_mock_conn(row=None)
    repo = OrganizationMemberRepository(db_connection=conn)

    is_member, _ = await repo.get_active_membership_isometrik_user_id(
        "u1", "org-1", disallow_suspended=True
    )

    assert is_member is False
    query, args = _sql_args(conn.fetchrow)
    assert OrganizationMemberStatus.SUSPENDED.value in args


@pytest.mark.asyncio
async def test_check_user_membership_by_user_id():
    """Membership check delegates to isometrik lookup."""
    conn = _async_mock_conn(row={"isometrik_user_id": None})
    repo = OrganizationMemberRepository(db_connection=conn)

    assert await repo.check_user_membership_by_user_id("u1", "org-1") is True


@pytest.mark.asyncio
async def test_check_phone_exists_for_other_user_exclude_self():
    """Phone check excludes current user when user_id provided."""
    conn = _async_mock_conn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    exists = await repo.check_phone_exists_for_other_user("5551234", "+1", "org-1", user_id="u1")

    assert exists is True
    query, args = _sql_args(conn.fetchrow)
    assert "user_id != $5" in query
    assert args[-1] == "u1"


@pytest.mark.asyncio
async def test_get_users_total_count_with_search():
    """Total count applies search ILIKE predicate."""
    conn = _async_mock_conn(row={"count": 5})
    repo = OrganizationMemberRepository(db_connection=conn)

    total = await repo.get_users_total_count("org-1", search="bob")

    assert total == 5
    query, args = _sql_args(conn.fetchrow)
    assert "ILIKE" in query
    assert "%bob%" in args


@pytest.mark.asyncio
async def test_get_organization_member_status_by_email():
    """Status by email returns status column."""
    conn = _async_mock_conn(row={"status": "active"})
    repo = OrganizationMemberRepository(db_connection=conn)

    status = await repo.get_organization_member_status_by_email("a@b.com")

    assert status == "active"


@pytest.mark.asyncio
async def test_update_user_info_with_fields():
    """Dynamic update builds SET clause for non-null fields."""
    conn = _async_mock_conn(row={"id": "m1", "first_name": "Jane"})
    repo = OrganizationMemberRepository(db_connection=conn)

    updated = await repo.update_user_info("u1", "org-1", {"first_name": "Jane"})

    assert updated["first_name"] == "Jane"
    query, _ = _sql_args(conn.fetchrow)
    assert "UPDATE organization_members" in query
    assert "first_name = $1" in query


@pytest.mark.asyncio
async def test_update_user_activity():
    """Activity update executes on active members only."""
    conn = _async_mock_conn()
    repo = OrganizationMemberRepository(db_connection=conn)

    await repo.update_user_activity("u1", "org-1")

    query, args = _sql_args(conn.execute)
    assert "last_active_at = NOW()" in query
    assert args[-1] == OrganizationMemberStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_revoke_suspended_user():
    """Revoke sets status back to active."""
    conn = _async_mock_conn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    ok = await repo.revoke_suspended_user("u1", "org-1")

    assert ok is True
    _, args = _sql_args(conn.fetchrow)
    assert args[0] == OrganizationMemberStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_update_user_email():
    """Email update returns True when row updated."""
    conn = _async_mock_conn(row={"id": "m1"})
    repo = OrganizationMemberRepository(db_connection=conn)

    ok = await repo.update_user_email("u1", "org-1", "new@example.com")

    assert ok is True


@pytest.mark.asyncio
async def test_update_user_email_by_user_id():
    """Cross-org email update parses execute row count."""
    conn = _async_mock_conn(execute_result="UPDATE 3")
    repo = OrganizationMemberRepository(db_connection=conn)

    count = await repo.update_user_email_by_user_id("u1", "new@example.com")

    assert count == 3


@pytest.mark.asyncio
async def test_update_user_info_all_none_values():
    """update_user_info returns None when all values are null."""
    conn = _FakeConn()
    repo = OrganizationMemberRepository(db_connection=conn)

    result = await repo.update_user_info("u1", "org-1", {"first_name": None, "last_name": None})

    assert result is None
    assert not conn.fetchrow_calls


@pytest.mark.asyncio
async def test_update_user_phone_by_user_id():
    """Cross-org phone update parses execute row count."""
    conn = _async_mock_conn(execute_result="UPDATE 2")
    repo = OrganizationMemberRepository(db_connection=conn)

    count = await repo.update_user_phone_by_user_id("u1", "555", "+1")

    assert count == 2


@pytest.mark.asyncio
async def test_get_organization_id_by_user_id():
    """Org lookup returns organization_id string."""
    conn = _async_mock_conn(row={"organization_id": "org-99"})
    repo = OrganizationMemberRepository(db_connection=conn)

    org_id = await repo.get_organization_id_by_user_id("u1")

    assert org_id == "org-99"


@pytest.mark.asyncio
async def test_get_all_members_by_organization_id():
    """List all members returns dict rows."""
    conn = _async_mock_conn(rows=[{"id": "m1"}, {"id": "m2"}])
    repo = OrganizationMemberRepository(db_connection=conn)

    members = await repo.get_all_members_by_organization_id("org-1")

    assert len(members) == 2
    query, _ = _sql_args(conn.fetch)
    assert "ORDER BY created_at DESC" in query


@pytest.mark.asyncio
async def test_delete_all_members_by_organization_id():
    """Bulk soft delete executes update for organization."""
    conn = _async_mock_conn()
    repo = OrganizationMemberRepository(db_connection=conn)

    await repo.delete_all_members_by_organization_id("org-1")

    query, args = _sql_args(conn.execute)
    assert "UPDATE organization_members" in query
    assert args[0] == "org-1"
