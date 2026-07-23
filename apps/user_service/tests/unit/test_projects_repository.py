"""Unit tests for ProjectsRepository query building with a fake connection."""

import pytest

from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = "DELETE 1"
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval call and return configured value."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute call."""
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_project_code_exists_lookup():
    """Code lookup checks organization_id and code."""
    conn = _FakeConn(val=True)
    repo = ProjectsRepository(db_connection=conn)

    exists = await repo.project_code_exists(organization_id="org-1", code="alpha")

    assert exists is True
    query, args = conn.fetchval_calls[0]
    assert "FROM projects" in query
    assert args == ("org-1", "alpha")


@pytest.mark.asyncio
async def test_insert_project_only_includes_present_columns():
    """Insert builds column list from provided keys and casts enum columns."""
    conn = _FakeConn(row={"id": "p1"})
    repo = ProjectsRepository(db_connection=conn)

    await repo.insert_project(
        {
            "organization_id": "org-1",
            "code": "A1",
            "name": "Alpha",
            "developer_name": "Dev",
            "community_admin_user_id": "00000000-0000-4000-8000-000000000001",
            "gstin": "123456789012345",
            "address_line_1": "L1",
            "pin_code": "111",
            "city": "C",
            "state": "S",
            "country": "IN",
            "property_types": ["residential"],
            "primary_measurement_unit": "sq_ft",
        }
    )

    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO projects" in query
    assert "::property_type[]" in query
    assert "::measurement_unit" in query
    assert "possession_date" not in query


@pytest.mark.asyncio
async def test_list_projects_status_and_property_type_filters():
    """List query adds status and property_type predicates when provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = ProjectsRepository(db_connection=conn)

    await repo.list_projects(
        organization_id="org-1",
        search="alpha",
        status="onboarding",
        property_type="residential",
        page=1,
        page_size=20,
    )

    count_query, count_args = conn.fetchval_calls[0]
    assert "p.status = $2::project_status" in count_query
    assert "= ANY(p.property_types)" in count_query
    assert count_args[0] == "org-1"


@pytest.mark.asyncio
async def test_list_projects_without_filters():
    """List query omits optional predicates when not provided."""
    conn = _FakeConn(rows=[], val=0)
    repo = ProjectsRepository(db_connection=conn)

    await repo.list_projects(
        organization_id="org-1",
        search=None,
        status=None,
        property_type=None,
        page=2,
        page_size=10,
    )

    _, list_args = conn.fetch_calls[0]
    list_query, _ = conn.fetch_calls[0]
    assert "LEFT JOIN organization_members ca" in list_query
    assert "community_admin_email" in list_query
    # only org id + offset + limit
    assert list_args[0] == "org-1"
    assert list_args[-2] == 10  # offset = (2-1)*10
    assert list_args[-1] == 10  # page_size


@pytest.mark.asyncio
async def test_list_projects_for_member_joins_project_members():
    """Assigned-project list scopes by active project_members row."""
    conn = _FakeConn(rows=[], val=0)
    repo = ProjectsRepository(db_connection=conn)

    await repo.list_projects_for_member(
        organization_id="org-1",
        user_id="user-1",
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )

    count_query, _ = conn.fetchval_calls[0]
    list_query, _ = conn.fetch_calls[0]
    assert "INNER JOIN project_members pm" in count_query
    assert "pm.user_id = $2::uuid" in count_query
    assert "pm.status = 'active'" in count_query
    assert "pm.role" in list_query


@pytest.mark.asyncio
async def test_recompute_units_count_returns_int():
    """Recompute returns an integer count."""
    conn = _FakeConn(val=5)
    repo = ProjectsRepository(db_connection=conn)

    count = await repo.recompute_units_count(organization_id="org-1", project_id="p1")

    assert count == 5


@pytest.mark.asyncio
async def test_get_update_delete_project():
    """Get, patch, and delete project rows."""
    conn = _FakeConn(row={"id": "p1", "name": "Alpha"})
    repo = ProjectsRepository(db_connection=conn)

    project = await repo.get_project(organization_id="org-1", project_id="p1")
    assert project["name"] == "Alpha"

    conn.row = {"id": "p1", "name": "Beta"}
    updated = await repo.update_project(
        organization_id="org-1",
        project_id="p1",
        update_data={"name": "Beta", "id": "ignored"},
    )
    assert updated["name"] == "Beta"
    update_query, _ = conn.fetchrow_calls[1]
    assert "UPDATE projects" in update_query
    assert "name = $1" in update_query
    assert "id = $1" not in update_query.split("SET")[1].split("WHERE")[0]

    conn.row = {"id": "p1"}
    unchanged = await repo.update_project(organization_id="org-1", project_id="p1", update_data={})
    assert unchanged["id"] == "p1"

    assert await repo.delete_project(organization_id="org-1", project_id="p1")


@pytest.mark.asyncio
async def test_set_setup_current_step_and_status():
    """Wizard step and status updates cast enums."""
    conn = _FakeConn(row={"id": "p1", "status": "active"})
    repo = ProjectsRepository(db_connection=conn)

    await repo.set_setup_current_step(organization_id="org-1", project_id="p1", step_key="units")
    assert "::project_setup_step" in conn.execute_calls[0][0]

    status_row = await repo.set_status(organization_id="org-1", project_id="p1", status="active")
    assert status_row["status"] == "active"


@pytest.mark.asyncio
async def test_project_media_and_members():
    """Media CRUD and member upsert/list."""
    conn = _FakeConn(row={"id": "m1"}, rows=[{"id": "mem1"}])
    repo = ProjectsRepository(db_connection=conn)

    media = await repo.insert_media(
        {
            "organization_id": "org-1",
            "project_id": "p1",
            "kind": "brochure",
            "path": "/media/b.pdf",
            "mime": "application/pdf",
            "size_bytes": 2048,
        }
    )
    assert media["id"] == "m1"

    conn.rows = [{"id": "m1"}]
    listed = await repo.list_media(organization_id="org-1", project_id="p1")
    assert len(listed) == 1

    conn.row = {"id": "m1"}
    fetched = await repo.get_media(organization_id="org-1", project_id="p1", media_id="m1")
    assert fetched["id"] == "m1"

    assert await repo.delete_media(organization_id="org-1", project_id="p1", media_id="m1")

    conn.row = {"id": "mem1", "role": "community_admin"}
    member = await repo.upsert_member(
        organization_id="org-1",
        project_id="p1",
        user_id="user-1",
        role="community_admin",
    )
    assert member["role"] == "community_admin"

    members = await repo.list_members(organization_id="org-1", project_id="p1")
    assert len(members) == 1
