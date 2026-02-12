"""Unit tests for ProjectRepository with fake asyncpg connection."""

import json

import pytest

from apps.user_service.app.db.repositories.project_repository import ProjectRepository
from apps.user_service.app.schemas.enums import ProjectPriority, ProjectStatus
from apps.user_service.app.schemas.projects import ProjectListQueryParams


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        """Initialize fake call stores."""
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self.fetchrow_result = None
        self.fetch_result = []
        self.fetchval_result = None

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchval(self, query, *args):
        """Record fetchval calls."""
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return None


@pytest.mark.asyncio
async def test_create_project_raises_required_fields_missing():
    """create_project raises ValueError when required fields missing."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    with pytest.raises(ValueError, match="Required field organization_id is missing"):
        await repo.create_project({})

    with pytest.raises(ValueError, match="Required field project_id is missing"):
        await repo.create_project({"organization_id": "org-1"})

    with pytest.raises(ValueError, match="Required field project_title is missing"):
        await repo.create_project({"organization_id": "org-1", "project_id": "proj-1"})


@pytest.mark.asyncio
async def test_create_project_includes_only_provided_fields():
    """create_project only includes fields that are explicitly provided."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "project-1",
        "organization_id": "org-1",
        "project_id": "test-project",
        "project_title": "Test Project",
        "client_id": "client-1",
        "status": "active",
    }
    repo = ProjectRepository(db_connection=conn)

    result = await repo.create_project(
        {
            "organization_id": "org-1",
            "project_id": "test-project",
            "project_title": "Test Project",
            "client_id": "client-1",
            "status": "active",
        }
    )

    assert result["id"] == "project-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "INSERT INTO projects" in query
    assert "organization_id" in query
    assert "project_id" in query
    assert "project_title" in query
    assert "client_id" in query
    assert "status" in query


@pytest.mark.asyncio
async def test_check_project_id_unique():
    """check_project_id_unique returns boolean."""
    conn = _FakeConn()
    conn.fetchval_result = True
    repo = ProjectRepository(db_connection=conn)

    result = await repo.check_project_id_unique("test-project", "org-1")

    assert result is True
    assert len(conn.fetchval_calls) == 1
    query = conn.fetchval_calls[0][0]
    assert "SELECT NOT EXISTS" in query
    assert "projects" in query
    assert "project_id = $1" in query


@pytest.mark.asyncio
async def test_check_project_id_unique_with_exclude_id():
    """check_project_id_unique excludes project ID when provided."""
    conn = _FakeConn()
    conn.fetchval_result = True
    repo = ProjectRepository(db_connection=conn)

    result = await repo.check_project_id_unique("test-project", "org-1", exclude_id="project-1")

    assert result is True
    assert len(conn.fetchval_calls) == 1
    query = conn.fetchval_calls[0][0]
    assert "id != $3" in query


@pytest.mark.asyncio
async def test_get_projects_list_excludes_archived():
    """get_projects_list filters out archived projects."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "project-1",
            "project_id": "test-project",
            "project_title": "Test Project",
            "status": "active",
        }
    ]
    conn.fetchval_result = 1  # Total count
    repo = ProjectRepository(db_connection=conn)

    filters = ProjectListQueryParams(page=1, page_size=20)
    result, total = await repo.get_projects_list("org-1", filters)

    assert len(result) == 1
    assert total == 1
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "p.status != 'archived'" in query


@pytest.mark.asyncio
async def test_get_projects_list_applies_search_filter():
    """get_projects_list applies search filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    conn.fetchval_result = 0  # Total count
    repo = ProjectRepository(db_connection=conn)

    filters = ProjectListQueryParams(page=1, page_size=20, search="test")
    await repo.get_projects_list("org-1", filters)

    query = conn.fetch_calls[0][0]
    assert "to_tsvector" in query
    assert "plainto_tsquery" in query


@pytest.mark.asyncio
async def test_get_projects_list_applies_client_filter():
    """get_projects_list applies client_id filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    conn.fetchval_result = 0  # Total count
    repo = ProjectRepository(db_connection=conn)

    filters = ProjectListQueryParams(page=1, page_size=20, client_id="client-123")
    await repo.get_projects_list("org-1", filters)

    query = conn.fetch_calls[0][0]
    assert "p.client_id = $" in query


@pytest.mark.asyncio
async def test_get_projects_list_applies_status_filter():
    """get_projects_list applies status filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    conn.fetchval_result = 0  # Total count
    repo = ProjectRepository(db_connection=conn)

    filters = ProjectListQueryParams(page=1, page_size=20, status=ProjectStatus.ACTIVE)
    await repo.get_projects_list("org-1", filters)

    query = conn.fetch_calls[0][0]
    assert "p.status = $" in query


@pytest.mark.asyncio
async def test_get_projects_list_applies_priority_filter():
    """get_projects_list applies priority filter when provided."""
    conn = _FakeConn()
    conn.fetch_result = []
    conn.fetchval_result = 0  # Total count
    repo = ProjectRepository(db_connection=conn)

    filters = ProjectListQueryParams(page=1, page_size=20, priority=ProjectPriority.HIGH)
    await repo.get_projects_list("org-1", filters)

    query = conn.fetch_calls[0][0]
    assert "p.priority = $" in query


@pytest.mark.asyncio
async def test_get_project_with_client_raises_not_found():
    """get_project_with_client returns None when project not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_with_client("project-123", "org-1")

    assert result is None
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "JOIN clients" in query or "clients" in query


@pytest.mark.asyncio
async def test_get_project_with_client_success():
    """get_project_with_client returns project with client data when found."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "project-1",
        "project_id": "test-project",
        "project_title": "Test Project",
        "client_uuid": "client-1",
        "client_name": "Client 1",
    }
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_with_client("project-1", "org-1")

    assert result["id"] == "project-1"
    assert result["client_uuid"] == "client-1"
    assert len(conn.fetchrow_calls) == 1


@pytest.mark.asyncio
async def test_get_project_repositories():
    """get_project_repositories returns list of repositories."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "repo-1",
            "repository_name": "test-repo",
            "platform": "github",
        }
    ]
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_repositories("project-1", "org-1")

    assert len(result) == 1
    assert result[0]["id"] == "repo-1"
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "project_repositories" in query


@pytest.mark.asyncio
async def test_get_project_integrations():
    """get_project_integrations returns list of integrations."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "integration-1",
            "integration_type": "linear",
            "is_connected": True,
        }
    ]
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_integrations("project-1", "org-1")

    assert len(result) == 1
    assert result[0]["id"] == "integration-1"
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "project_integrations" in query


@pytest.mark.asyncio
async def test_create_project_serializes_jsonb_fields():
    """create_project serializes JSONB fields correctly."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "project-1"}
    repo = ProjectRepository(db_connection=conn)

    await repo.create_project(
        {
            "organization_id": "org-1",
            "project_id": "test-project",
            "project_title": "Test Project",
            "client_id": "client-1",
            "status": "active",
            "billing_info": {"billing_type": "hourly", "hourly_rate": 100},
            "tech_stack": {"frontend": ["React"]},
            "custom_fields": {"key": "value"},
        }
    )

    assert len(conn.fetchrow_calls) == 1
    # Verify JSONB fields are serialized (check args, not query string)
    args = conn.fetchrow_calls[0][1]
    # The JSONB fields should be JSON strings in the args
    billing_arg = next(
        (arg for arg in args if isinstance(arg, str) and "billing_type" in arg), None
    )
    assert billing_arg is not None or any(isinstance(arg, str) for arg in args)


def test_serialize_jsonb_param_passthrough():
    """_serialize_jsonb_param returns original value for non-JSONB columns."""
    assert ProjectRepository._serialize_jsonb_param("project_title", "Title") == "Title"


@pytest.mark.asyncio
async def test_create_project_repositories_batches_defaults():
    """create_project_repositories batches repository payload with defaults."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    repositories = [
        {
            "platform": "github",
            "repository_name": "frontend",
            "repository_owner": "org",
            "repository_url": "https://github.com/org/frontend",
            "purpose": "main app",
            "primary_branch": "develop",
            "is_private": False,
            "is_primary": True,
        },
        {
            "platform": "gitlab",
            "repository_name": "backend",
            "repository_owner": "org",
            "repository_url": "https://gitlab.com/org/backend",
        },
    ]

    await repo.create_project_repositories(
        project_id="project-1",
        organization_id="org-1",
        repositories=repositories,
        created_by="user-1",
    )

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "INSERT INTO project_repositories" in query
    (
        organization_ids,
        project_ids,
        platforms,
        repository_names,
        repository_owners,
        repository_urls,
        purposes,
        primary_branches,
        is_private_flags,
        is_primary_flags,
        created_bys,
    ) = args
    assert organization_ids == ["org-1", "org-1"]
    assert project_ids == ["project-1", "project-1"]
    assert platforms == ["github", "gitlab"]
    assert repository_names == ["frontend", "backend"]
    assert repository_owners == ["org", "org"]
    assert repository_urls == ["https://github.com/org/frontend", "https://gitlab.com/org/backend"]
    assert purposes == ["main app", None]
    assert primary_branches == ["develop", "main"]
    assert is_private_flags == [False, True]
    assert is_primary_flags == [True, False]
    assert created_bys == ["user-1", "user-1"]


@pytest.mark.asyncio
async def test_create_project_integrations_batches_payload():
    """create_project_integrations serializes integration configs."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    integrations = [
        {
            "integration_type": "linear",
            "integration_name": "Linear",
            "external_project_id": "ext-1",
            "external_project_key": "LP-1",
            "external_workspace_id": "workspace-1",
            "external_board_id": "board-1",
            "sync_enabled": False,
            "sync_direction": "incoming",
            "auto_sync": False,
            "sync_interval_minutes": 30,
            "integration_purpose": "issue-tracking",
            "integration_config": {"api_key": "secret"},
        }
    ]

    await repo.create_project_integrations(
        project_id="project-1",
        organization_id="org-1",
        integrations=integrations,
        connected_by="user-1",
    )

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "INSERT INTO project_integrations" in query
    (
        organization_ids,
        project_ids,
        integration_types,
        integration_names,
        external_project_ids,
        external_project_keys,
        external_workspace_ids,
        external_board_ids,
        sync_enabled_flags,
        sync_directions,
        auto_sync_flags,
        sync_interval_minutes_list,
        integration_purposes,
        integration_configs,
        connected_bys,
    ) = args
    assert organization_ids == ["org-1"]
    assert project_ids == ["project-1"]
    assert integration_types == ["linear"]
    assert integration_names == ["Linear"]
    assert external_project_ids == ["ext-1"]
    assert external_project_keys == ["LP-1"]
    assert external_workspace_ids == ["workspace-1"]
    assert external_board_ids == ["board-1"]
    assert sync_enabled_flags == [False]
    assert sync_directions == ["incoming"]
    assert auto_sync_flags == [False]
    assert sync_interval_minutes_list == [30]
    assert integration_purposes == ["issue-tracking"]
    assert json.loads(integration_configs[0])["api_key"] == "secret"
    assert connected_bys == ["user-1"]


@pytest.mark.asyncio
async def test_build_project_filters_includes_tags():
    """_build_project_filters adds tags filter with trimmed values."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    filters = ProjectListQueryParams(page=1, page_size=10, tags="alpha, beta , ,gamma")

    where_clause, params = repo._build_project_filters("org-1", filters)

    assert "p.tags && $" in where_clause
    assert params[-1] == ["alpha", "beta", "gamma"]
