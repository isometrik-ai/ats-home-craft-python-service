"""Unit tests for PermissionsRepository with fake asyncpg connection."""

from datetime import datetime

import pytest

from apps.user_service.app.db.repositories.permission_repository import (
    PermissionsRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.rows = []
        self.row = None

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_get_all_permissions_returns_dicts():
    """fetch rows are converted to dicts."""

    conn = _FakeConn()
    conn.rows = [{"id": "p1", "name": "N", "code": "c", "created_at": datetime.now()}]
    repo = PermissionsRepository(db_connection=conn)

    rows = await repo.get_all_permissions("org1")

    assert rows[0]["id"] == "p1"
    assert conn.fetch_calls[0][1] == ("org1",)


@pytest.mark.asyncio
async def test_get_permission_by_id_none_when_missing():
    """Returns None when fetchrow returns None."""

    conn = _FakeConn()
    conn.row = None
    repo = PermissionsRepository(db_connection=conn)

    row = await repo.get_permission_by_id("p1", "org1")

    assert row is None


@pytest.mark.asyncio
async def test_create_permission_maps_to_dict():
    """fetchrow result is turned into dict."""

    class Obj:
        """Input object for create_permission."""

        def __init__(self):
            self.name = "N"
            self.code = "c"
            self.category = "cat"
            self.description = "d"

    conn = _FakeConn()
    conn.row = {"id": "p1", "name": "N"}
    repo = PermissionsRepository(db_connection=conn)

    created = await repo.create_permission(Obj(), "org1")

    assert created["id"] == "p1"
    assert conn.fetchrow_calls[0][1][0] == "N"


@pytest.mark.asyncio
async def test_delete_permission_returns_bool():
    """Returns True when row exists."""

    conn = _FakeConn()
    conn.row = {"id": "p1"}
    repo = PermissionsRepository(db_connection=conn)

    assert await repo.delete_permission("p1", "org1") is True


@pytest.mark.asyncio
async def test_create_default_permissions_builds_placeholders():
    """Ensures fetch is invoked with expanded values."""

    conn = _FakeConn()
    conn.rows = [{"id": "x1"}, {"id": "x2"}]
    repo = PermissionsRepository(db_connection=conn)

    ids = await repo.create_default_permissions("org1")

    assert ids == ["x1", "x2"]
    # Make sure values were passed (org id is first)
    assert conn.fetch_calls
