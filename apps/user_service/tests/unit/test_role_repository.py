"""Unit tests for RoleRepository with fake asyncpg connection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.role_repository import RoleRepository
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ROLE_ID = "660e8400-e29b-41d4-a716-446655440001"


def _async_mock_conn(*, rows=None, row=None, val=None, execute_result=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None, execute_result=None):
        """Initialize fake call stores."""
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_assign_permissions_to_role_missing_role_raises():
    """assign_permissions_to_role raises when role not found."""

    conn = _FakeConn(row=None)
    repo = RoleRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.assign_permissions_to_role("r1", "org1", ["p1"])

    assert "WHERE id = $1" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_assign_permissions_executes_when_role_exists():
    """Executes insert when role exists."""

    conn = _FakeConn(row={"id": "r1"})
    repo = RoleRepository(db_connection=conn)

    await repo.assign_permissions_to_role("r1", "org1", ["p1", "p2"])

    # second call should be execute for insert
    assert conn.execute_calls
    assert conn.execute_calls[0][1][0] == "r1"


@pytest.mark.asyncio
async def test_create_role():
    """create_role inserts and returns row."""
    conn = _FakeConn(row={"id": ROLE_ID, "name": "Editor"})
    repo = RoleRepository(db_connection=conn)

    result = await repo.create_role("Editor", "Can edit", ORG_ID, is_default=False)

    assert result["id"] == ROLE_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO roles" in query
    assert args == ("Editor", "Can edit", ORG_ID, False)


@pytest.mark.asyncio
async def test_get_role_by_id():
    """get_role_by_id scopes by org."""
    conn = _FakeConn(row={"id": ROLE_ID})
    repo = RoleRepository(db_connection=conn)

    row = await repo.get_role_by_id(ROLE_ID, ORG_ID)

    assert row["id"] == ROLE_ID
    query, args = conn.fetchrow_calls[0]
    assert "organization_id = $2" in query
    assert args == (ROLE_ID, ORG_ID)


@pytest.mark.asyncio
async def test_get_roles_list_enriched():
    """Enriched list uses CTEs for counts and permissions."""
    conn = _FakeConn(rows=[{"id": ROLE_ID, "user_count": 2, "permission_count": 3}])
    repo = RoleRepository(db_connection=conn)

    rows = await repo.get_roles_list_enriched(ORG_ID, search="edit", limit=10, offset=5)

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "WITH base_roles AS" in query
    assert args[0] == ORG_ID
    assert args[1] == "edit"


@pytest.mark.asyncio
async def test_get_roles_count_with_search():
    """Count excludes admin role and applies search."""
    conn = _async_mock_conn(val=4)
    repo = RoleRepository(db_connection=conn)

    count = await repo.get_roles_count(ORG_ID, search="edit")

    assert count == 4
    query, args = _sql_args(conn.fetchval)
    assert "name != 'admin'" in query
    assert "ILIKE" in query
    assert args == (ORG_ID, "%edit%")


@pytest.mark.asyncio
async def test_get_role_permissions():
    """get_role_permissions joins permissions."""
    conn = _FakeConn(rows=[{"id": "p1", "code": "leads.read"}])
    repo = RoleRepository(db_connection=conn)

    perms = await repo.get_role_permissions(ROLE_ID, ORG_ID)

    assert perms[0]["code"] == "leads.read"
    query, _ = conn.fetch_calls[0]
    assert "FROM permissions p" in query


@pytest.mark.asyncio
async def test_get_role_permission_ids():
    """Permission IDs returned as strings."""
    conn = _FakeConn(rows=[{"permission_id": "p1"}, {"permission_id": "p2"}])
    repo = RoleRepository(db_connection=conn)

    ids = await repo.get_role_permission_ids(ROLE_ID, ORG_ID)

    assert ids == ["p1", "p2"]


@pytest.mark.asyncio
async def test_update_role_empty_returns_none():
    """Empty update_data returns None without query."""
    conn = _FakeConn()
    repo = RoleRepository(db_connection=conn)

    assert await repo.update_role(ROLE_ID, ORG_ID, {}) is None
    assert not conn.fetchrow_calls


@pytest.mark.asyncio
async def test_update_role_sets_fields():
    """Update builds dynamic SET clause."""
    conn = _FakeConn(row={"id": ROLE_ID, "name": "New"})
    repo = RoleRepository(db_connection=conn)

    updated = await repo.update_role(ROLE_ID, ORG_ID, {"name": "New"})

    assert updated["name"] == "New"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE roles" in query
    assert args[0] == ROLE_ID


@pytest.mark.asyncio
async def test_delete_role():
    """delete_role executes DELETE."""
    conn = _async_mock_conn()
    repo = RoleRepository(db_connection=conn)

    await repo.delete_role(ROLE_ID, ORG_ID)

    query, args = _sql_args(conn.fetchval)
    assert "DELETE FROM roles" in query
    assert args == (ROLE_ID, ORG_ID)


@pytest.mark.asyncio
async def test_delete_all_roles_by_organization_id():
    """Bulk delete parses affected row count."""
    conn = _async_mock_conn(execute_result="DELETE 3")
    repo = RoleRepository(db_connection=conn)

    deleted = await repo.delete_all_roles_by_organization_id(ORG_ID)

    assert deleted == 3


@pytest.mark.asyncio
async def test_check_role_exists():
    """Exists check returns fetchval result."""
    conn = _async_mock_conn(val=True)
    repo = RoleRepository(db_connection=conn)

    assert await repo.check_role_exists(ROLE_ID, ORG_ID) is True


@pytest.mark.asyncio
async def test_check_permissions_exist_empty():
    """Empty permission list returns True without query."""
    conn = _async_mock_conn()
    repo = RoleRepository(db_connection=conn)

    assert await repo.check_permissions_exist([], ORG_ID) is True
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_get_permissions_by_ids_or_codes_empty():
    """No ids/codes returns empty list."""
    conn = _async_mock_conn()
    repo = RoleRepository(db_connection=conn)

    assert await repo.get_permissions_by_ids_or_codes([], ORG_ID, []) == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_check_role_name_unique_with_exclude():
    """Name uniqueness optionally excludes role id."""
    conn = _async_mock_conn(val=True)
    repo = RoleRepository(db_connection=conn)

    unique = await repo.check_role_name_unique("Editor", ORG_ID, exclude_role_id=ROLE_ID)

    assert unique is True
    query, args = _sql_args(conn.fetchval)
    assert "id != $3" in query


@pytest.mark.asyncio
async def test_check_role_usage():
    """Usage count excludes deleted members."""
    conn = _async_mock_conn(val=2)
    repo = RoleRepository(db_connection=conn)

    count = await repo.check_role_usage(ROLE_ID, ORG_ID)

    assert count == 2


@pytest.mark.asyncio
async def test_remove_permissions_from_role():
    """Remove permissions returns True when rows deleted."""
    conn = _FakeConn(rows=[{"?column?": 1}])
    repo = RoleRepository(db_connection=conn)

    removed = await repo.remove_permissions_from_role(ROLE_ID, ORG_ID, ["p1"])

    assert removed is True


@pytest.mark.asyncio
async def test_remove_all_permissions_from_role_empty():
    """Remove all returns False when nothing deleted."""
    conn = _FakeConn(rows=[])
    repo = RoleRepository(db_connection=conn)

    assert await repo.remove_all_permissions_from_role(ROLE_ID, ORG_ID) is False


@pytest.mark.asyncio
async def test_get_permissions_for_roles():
    """Batch permission lookup by role ids."""
    conn = _FakeConn(rows=[{"role_id": ROLE_ID, "permission_id": "p1"}])
    repo = RoleRepository(db_connection=conn)

    rows = await repo.get_permissions_for_roles([ROLE_ID], ORG_ID)

    assert rows[0]["permission_id"] == "p1"


@pytest.mark.asyncio
async def test_get_permission_counts_for_roles_empty():
    """Empty role_ids returns empty dict."""
    repo = RoleRepository(db_connection=_async_mock_conn())

    assert await repo.get_permission_counts_for_roles([], ORG_ID) == {}


@pytest.mark.asyncio
async def test_get_permission_counts_for_roles():
    """Counts map includes zero for roles without permissions."""
    conn = _FakeConn(rows=[{"role_id": ROLE_ID, "permission_count": 2}])
    repo = RoleRepository(db_connection=conn)

    counts = await repo.get_permission_counts_for_roles([ROLE_ID, "other-id"], ORG_ID)

    assert counts[ROLE_ID] == 2
    assert counts["other-id"] == 0
