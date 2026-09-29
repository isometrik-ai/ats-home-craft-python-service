"""Unit tests for ProjectRolesRepository with fake asyncpg connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.project_roles_repository import (
    ProjectRolesRepository,
)
from libs.shared_utils.project_role_defaults import (
    DEFAULT_PROJECT_ROLE_DEFINITIONS,
    DEFAULT_PROJECT_ROLE_PERMISSIONS,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
ROLE_ID = "770e8400-e29b-41d4-a716-446655440002"
PERM_ID = "880e8400-e29b-41d4-a716-446655440003"


def _role_row(**overrides) -> dict:
    base = {
        "id": ROLE_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "slug": "custom_role",
        "name": "Custom Role",
        "description": "Desc",
        "is_system": False,
        "created_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return base


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(
        self,
        *,
        rows=None,
        row=None,
        val=None,
        execute_result: str = "DELETE 0",
    ):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result

    async def executemany(self, query, args):
        self.executemany_calls.append((query.strip(), args))


class _SeedFakeConn(_FakeConn):
    """Returns role and permission rows for seed_default_roles_for_project."""

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        if "INSERT INTO project_roles" in query:
            slug = args[2]
            return {"id": f"role-id-{slug}", "slug": slug}
        return self.row

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        if "FROM project_permissions" in query:
            codes = args[1]
            return [{"id": f"perm-{code}", "code": code} for code in codes]
        return self.rows


@pytest.mark.asyncio
async def test_list_roles_for_project():
    conn = _FakeConn(rows=[_role_row()])
    repo = ProjectRolesRepository(db_connection=conn)

    rows = await repo.list_roles_for_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert len(rows) == 1
    assert rows[0]["slug"] == "custom_role"
    query, args = conn.fetch_calls[0]
    assert "FROM project_roles" in query
    assert "ORDER BY slug" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_get_role_by_slug_found_and_missing():
    conn = _FakeConn(row=_role_row(slug="security"))
    repo = ProjectRolesRepository(db_connection=conn)

    role = await repo.get_role_by_slug(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slug="security",
    )
    assert role is not None
    query, args = conn.fetchrow_calls[0]
    assert "slug = $3" in query
    assert args == (ORG_ID, PROJECT_ID, "security")

    conn.row = None
    missing = await repo.get_role_by_slug(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slug="missing",
    )
    assert missing is None


@pytest.mark.asyncio
async def test_get_role_by_id():
    conn = _FakeConn(row=_role_row())
    repo = ProjectRolesRepository(db_connection=conn)

    role = await repo.get_role_by_id(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )
    assert role["id"] == ROLE_ID
    query, args = conn.fetchrow_calls[0]
    assert "id = $3::uuid" in query
    assert args == (ORG_ID, PROJECT_ID, ROLE_ID)


@pytest.mark.asyncio
async def test_seed_default_roles_for_project():
    conn = _SeedFakeConn()
    repo = ProjectRolesRepository(db_connection=conn)

    slug_to_id = await repo.seed_default_roles_for_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert len(slug_to_id) == len(DEFAULT_PROJECT_ROLE_DEFINITIONS)
    for role_def in DEFAULT_PROJECT_ROLE_DEFINITIONS:
        assert slug_to_id[role_def.slug] == f"role-id-{role_def.slug}"

    role_inserts = [
        call for call in conn.fetchrow_calls if "INSERT INTO project_roles" in call[0]
    ]
    assert len(role_inserts) == len(DEFAULT_PROJECT_ROLE_DEFINITIONS)

    perm_fetch_query, perm_fetch_args = conn.fetch_calls[0]
    assert "FROM project_permissions" in perm_fetch_query
    assert perm_fetch_args[0] == ORG_ID
    expected_codes = {
        code for codes in DEFAULT_PROJECT_ROLE_PERMISSIONS.values() for code in codes
    }
    assert set(perm_fetch_args[1]) == expected_codes

    assert len(conn.execute_calls) > 0
    insert_perm_query, insert_perm_args = conn.execute_calls[0]
    assert "INSERT INTO project_role_permissions" in insert_perm_query
    assert insert_perm_args[0] == ORG_ID


@pytest.mark.asyncio
async def test_get_permission_codes_for_role():
    conn = _FakeConn(rows=[{"code": "a"}, {"code": "b"}])
    repo = ProjectRolesRepository(db_connection=conn)

    codes = await repo.get_permission_codes_for_role(project_role_id=ROLE_ID)

    assert codes == {"a", "b"}
    query, args = conn.fetch_calls[0]
    assert "project_role_permissions" in query
    assert args == (ROLE_ID,)


@pytest.mark.asyncio
async def test_get_permissions_for_role():
    conn = _FakeConn(
        rows=[
            {
                "id": PERM_ID,
                "code": "contacts_management.view",
                "name": "View",
                "category": "contacts",
                "description": None,
                "created_at": None,
            }
        ]
    )
    repo = ProjectRolesRepository(db_connection=conn)

    perms = await repo.get_permissions_for_role(
        organization_id=ORG_ID,
        project_role_id=ROLE_ID,
    )

    assert len(perms) == 1
    query, args = conn.fetch_calls[0]
    assert "ORDER BY p.category, p.code" in query
    assert args == (ORG_ID, ROLE_ID)


@pytest.mark.asyncio
async def test_replace_role_permissions_empty():
    conn = _FakeConn()
    repo = ProjectRolesRepository(db_connection=conn)

    await repo.replace_role_permissions(
        organization_id=ORG_ID,
        project_role_id=ROLE_ID,
        project_permission_ids=[],
    )

    assert len(conn.execute_calls) == 1
    delete_query, delete_args = conn.execute_calls[0]
    assert "DELETE FROM project_role_permissions" in delete_query
    assert delete_args == (ORG_ID, ROLE_ID)
    assert conn.executemany_calls == []


@pytest.mark.asyncio
async def test_replace_role_permissions_non_empty():
    conn = _FakeConn()
    repo = ProjectRolesRepository(db_connection=conn)
    perm_ids = [PERM_ID, "990e8400-e29b-41d4-a716-446655440004"]

    await repo.replace_role_permissions(
        organization_id=ORG_ID,
        project_role_id=ROLE_ID,
        project_permission_ids=perm_ids,
    )

    assert len(conn.execute_calls) == 1
    assert len(conn.executemany_calls) == 1
    insert_query, batch = conn.executemany_calls[0]
    assert "INSERT INTO project_role_permissions" in insert_query
    assert batch == [
        (ORG_ID, ROLE_ID, perm_ids[0]),
        (ORG_ID, ROLE_ID, perm_ids[1]),
    ]


@pytest.mark.asyncio
async def test_count_members_with_role():
    conn = _FakeConn(val=7)
    repo = ProjectRolesRepository(db_connection=conn)

    count = await repo.count_members_with_role(project_role_id=ROLE_ID)

    assert count == 7
    query, args = conn.fetchval_calls[0]
    assert "FROM project_members" in query
    assert "status = 'active'" in query
    assert args == (ROLE_ID,)


@pytest.mark.asyncio
async def test_update_role_metadata_no_fields_delegates_to_get_by_id():
    conn = _FakeConn(row=_role_row(name="Unchanged"))
    repo = ProjectRolesRepository(db_connection=conn)

    role = await repo.update_role_metadata(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )

    assert role["name"] == "Unchanged"
    assert len(conn.fetchrow_calls) == 1
    query, _ = conn.fetchrow_calls[0]
    assert "FROM project_roles" in query
    assert "UPDATE project_roles" not in query


@pytest.mark.asyncio
async def test_update_role_metadata_name_only():
    conn = _FakeConn(row=_role_row(name="Renamed"))
    repo = ProjectRolesRepository(db_connection=conn)

    role = await repo.update_role_metadata(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
        name="Renamed",
    )

    assert role["name"] == "Renamed"
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE project_roles" in query
    assert "name = $4" in query
    assert "description = $" not in query
    assert args == (ORG_ID, PROJECT_ID, ROLE_ID, "Renamed")


@pytest.mark.asyncio
async def test_update_role_metadata_description_only():
    conn = _FakeConn(row=_role_row(description="New desc"))
    repo = ProjectRolesRepository(db_connection=conn)

    await repo.update_role_metadata(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
        description="New desc",
    )

    query, args = conn.fetchrow_calls[0]
    assert "description = $4" in query
    assert "name = $" not in query.replace("name,", "")
    assert args == (ORG_ID, PROJECT_ID, ROLE_ID, "New desc")


@pytest.mark.asyncio
async def test_update_role_metadata_name_and_description():
    conn = _FakeConn(row=_role_row(name="N", description="D"))
    repo = ProjectRolesRepository(db_connection=conn)

    await repo.update_role_metadata(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
        name="N",
        description="D",
    )

    query, args = conn.fetchrow_calls[0]
    assert "name = $4" in query
    assert "description = $5" in query
    assert args == (ORG_ID, PROJECT_ID, ROLE_ID, "N", "D")


@pytest.mark.asyncio
async def test_update_role_metadata_update_returns_none():
    conn = _FakeConn(row=None)
    repo = ProjectRolesRepository(db_connection=conn)

    result = await repo.update_role_metadata(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
        name="Missing",
    )
    assert result is None


@pytest.mark.asyncio
async def test_create_role():
    conn = _FakeConn(row=_role_row(is_system=False))
    repo = ProjectRolesRepository(db_connection=conn)

    role = await repo.create_role(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        slug="analyst",
        name="Analyst",
        description="Read ops",
        is_system=False,
    )

    assert role["slug"] == "custom_role"
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO project_roles" in query
    assert args == (ORG_ID, PROJECT_ID, "analyst", "Analyst", "Read ops", False)


@pytest.mark.asyncio
async def test_delete_role_true_and_false():
    conn = _FakeConn(execute_result="DELETE 1")
    repo = ProjectRolesRepository(db_connection=conn)

    deleted = await repo.delete_role(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )
    assert deleted is True
    query, args = conn.execute_calls[0]
    assert "is_system = false" in query
    assert args == (ORG_ID, PROJECT_ID, ROLE_ID)

    conn.execute_result = "DELETE 0"
    not_deleted = await repo.delete_role(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        project_role_id=ROLE_ID,
    )
    assert not_deleted is False
