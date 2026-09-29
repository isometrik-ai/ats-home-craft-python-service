"""Unit tests for ProjectPermissionsRepository with fake asyncpg connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.project_permissions_repository import (
    ProjectPermissionsRepository,
)
from libs.shared_utils.common_query import DEFAULT_PROJECT_PERMISSIONS

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PERM_ID = "660e8400-e29b-41d4-a716-446655440001"


def _perm_row(**overrides) -> dict:
    base = {
        "id": PERM_ID,
        "name": "View Contacts",
        "code": "contacts_management.view",
        "category": "contacts",
        "description": "View contacts",
        "created_at": None,
    }
    base.update(overrides)
    return base


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_get_all_permissions():
    conn = _FakeConn(rows=[_perm_row(), _perm_row(code="other.code", name="Other")])
    repo = ProjectPermissionsRepository(db_connection=conn)

    rows = await repo.get_all_permissions(ORG_ID)

    assert len(rows) == 2
    query, args = conn.fetch_calls[0]
    assert "FROM project_permissions" in query
    assert "ORDER BY category ASC, name ASC" in query
    assert args == (ORG_ID,)


@pytest.mark.asyncio
async def test_get_permission_by_id_found():
    conn = _FakeConn(row=_perm_row())
    repo = ProjectPermissionsRepository(db_connection=conn)

    perm = await repo.get_permission_by_id(PERM_ID, ORG_ID)

    assert perm["code"] == "contacts_management.view"
    query, args = conn.fetchrow_calls[0]
    assert "id = $1::uuid" in query
    assert "organization_id = $2::uuid" in query
    assert args == (PERM_ID, ORG_ID)


@pytest.mark.asyncio
async def test_get_permission_by_id_missing():
    conn = _FakeConn(row=None)
    repo = ProjectPermissionsRepository(db_connection=conn)

    assert await repo.get_permission_by_id(PERM_ID, ORG_ID) is None


@pytest.mark.asyncio
async def test_create_default_permissions():
    returned_ids = [f"id-{idx}" for idx in range(len(DEFAULT_PROJECT_PERMISSIONS))]
    conn = _FakeConn(rows=[{"id": rid} for rid in returned_ids])
    repo = ProjectPermissionsRepository(db_connection=conn)

    ids = await repo.create_default_permissions(ORG_ID)

    assert ids == returned_ids
    assert len(conn.fetch_calls) == 1
    query, args = conn.fetch_calls[0]
    assert "INSERT INTO project_permissions" in query
    assert "ON CONFLICT (organization_id, code) DO NOTHING" in query
    assert "RETURNING id" in query
    assert args[0] == ORG_ID
    assert len(args) == len(DEFAULT_PROJECT_PERMISSIONS) * 6
    for idx, (code, name, description, category) in enumerate(DEFAULT_PROJECT_PERMISSIONS):
        base = idx * 6
        assert args[base] == ORG_ID
        assert args[base + 1] == code
        assert args[base + 2] == name
        assert args[base + 3] == description
        assert args[base + 4] == category
