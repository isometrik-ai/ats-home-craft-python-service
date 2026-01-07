"""Unit tests for RoleRepository with fake asyncpg connection."""

import pytest

from apps.user_service.app.db.repositories.role_repository import RoleRepository
from libs.shared_utils.http_exceptions import NotFoundException


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Initialize fake call stores."""
        self.fetchrow_calls = []
        self.execute_calls = []
        self.fetchrow_result = None

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_assign_permissions_to_role_missing_role_raises():
    """assign_permissions_to_role raises when role not found."""

    conn = _FakeConn()
    conn.fetchrow_result = None  # get_role_by_id returns None
    repo = RoleRepository(db_connection=conn)

    with pytest.raises(NotFoundException):
        await repo.assign_permissions_to_role("r1", "org1", ["p1"])

    # ensure we called get_role_by_id
    assert "WHERE id = $1" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_assign_permissions_executes_when_role_exists():
    """Executes insert when role exists."""

    conn = _FakeConn()
    conn.fetchrow_result = {"id": "r1"}  # get_role_by_id succeeds
    repo = RoleRepository(db_connection=conn)

    await repo.assign_permissions_to_role("r1", "org1", ["p1", "p2"])

    # second call should be execute for insert
    assert conn.execute_calls
    assert conn.execute_calls[0][1][0] == "r1"
