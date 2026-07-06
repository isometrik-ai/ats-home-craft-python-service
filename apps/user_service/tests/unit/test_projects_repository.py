"""Unit tests for ProjectsRepository query building with a fake connection."""

import pytest

from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []

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
            "community_admin_email": "a@b.com",
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
    # only org id + offset + limit
    assert list_args[0] == "org-1"
    assert list_args[-2] == 10  # offset = (2-1)*10
    assert list_args[-1] == 10  # page_size


@pytest.mark.asyncio
async def test_recompute_units_count_returns_int():
    """Recompute returns an integer count."""
    conn = _FakeConn(val=5)
    repo = ProjectsRepository(db_connection=conn)

    count = await repo.recompute_units_count(organization_id="org-1", project_id="p1")

    assert count == 5
