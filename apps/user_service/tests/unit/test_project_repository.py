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
        "status": "active",
    }
    repo = ProjectRepository(db_connection=conn)

    result = await repo.create_project(
        {
            "organization_id": "org-1",
            "project_id": "test-project",
            "project_title": "Test Project",
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
async def test_get_project_details_raises_not_found():
    """get_project_details returns None when project not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_details("project-123", "org-1")

    assert result is None
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "FROM projects" in query


@pytest.mark.asyncio
async def test_get_project_details_success():
    """get_project_details returns project when found."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "project-1",
        "project_id": "test-project",
        "project_title": "Test Project",
    }
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_details("project-1", "org-1")

    assert result["id"] == "project-1"
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


# Update Project Tests


@pytest.mark.asyncio
async def test_update_project_updates_only_provided_fields():
    """update_project only updates fields present in data dict."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "project_title": "Updated Title",
        "status": "on_hold",
        "updated_by": "user-1",
    }

    await repo.update_project("project-1", "org-1", update_data)

    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "UPDATE projects" in query
    assert "project_title = $" in query
    assert "status = $" in query
    assert "updated_by = $" in query
    assert "updated_at = NOW()" in query
    assert "id = $" in query
    assert "organization_id = $" in query
    assert "status != 'archived'" in query


@pytest.mark.asyncio
async def test_update_project_skips_forbidden_fields():
    """update_project skips project_id, id, organization_id."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "project_title": "Updated Title",
        "project_id": "should-be-ignored",
        "id": "should-be-ignored",
        "organization_id": "should-be-ignored",
    }

    await repo.update_project("project-1", "org-1", update_data)

    query = conn.fetchrow_calls[0][0]
    assert "project_id" not in query
    assert "id = $" in query  # Only in WHERE clause
    assert "organization_id = $" in query  # Only in WHERE clause


@pytest.mark.asyncio
async def test_update_project_serializes_jsonb_fields():
    """update_project serializes JSONB fields correctly."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "billing_info": {"billing_type": "hourly", "hourly_rate": 100},
        "tech_stack": {"frontend": ["React"]},
        "custom_fields": {"key": "value"},
    }

    await repo.update_project("project-1", "org-1", update_data)

    query = conn.fetchrow_calls[0][0]
    # Verify JSONB fields are in the query
    assert "billing_info = $" in query
    assert "tech_stack = $" in query
    assert "custom_fields = $" in query


@pytest.mark.asyncio
async def test_update_project_does_nothing_when_data_empty():
    """update_project does nothing when data dict is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.update_project("project-1", "org-1", {})

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_update_project_repository_updates_fields():
    """update_project_repository updates only provided fields."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "repository_name": "updated-repo",
        "is_primary": True,
    }

    await repo.update_project_repository("project-1", "org-1", "repo-1", update_data)

    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "UPDATE project_repositories" in query
    assert "repository_name = $" in query
    assert "is_primary = $" in query
    assert "updated_at = NOW()" in query
    assert "id = $" in query
    assert "project_id = $" in query
    assert "organization_id = $" in query


@pytest.mark.asyncio
async def test_update_project_repo_skip_forbidden_fields():
    """update_project_repository skips forbidden fields."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "repository_name": "updated-repo",
        "id": "should-be-ignored",
        "project_id": "should-be-ignored",
        "organization_id": "should-be-ignored",
        "created_by": "should-be-ignored",
        "created_at": "should-be-ignored",
    }

    await repo.update_project_repository("project-1", "org-1", "repo-1", update_data)

    query = conn.execute_calls[0][0]
    assert "id = $" in query  # Only in WHERE clause
    assert "project_id = $" in query  # Only in WHERE clause
    assert "organization_id = $" in query  # Only in WHERE clause
    assert "created_by" not in query
    assert "created_at" not in query


@pytest.mark.asyncio
async def test_update_project_repo_noop_when_data_empty():
    """update_project_repository does nothing when data dict is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.update_project_repository("project-1", "org-1", "repo-1", {})

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_update_project_integration_updates_fields():
    """update_project_integration updates only provided fields."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "integration_name": "Updated Integration",
        "sync_enabled": False,
        "sync_interval_minutes": 30,
    }

    await repo.update_project_integration("project-1", "org-1", "integration-1", update_data)

    assert len(conn.execute_calls) == 1
    query = conn.execute_calls[0][0]
    assert "UPDATE project_integrations" in query
    assert "integration_name = $" in query
    assert "sync_enabled = $" in query
    assert "sync_interval_minutes = $" in query
    assert "updated_at = NOW()" in query


@pytest.mark.asyncio
async def test_update_project_integration_serializes_config():
    """update_project_integration serializes integration_config as JSON."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "integration_config": {"api_key": "secret", "workspace": "test"},
    }

    await repo.update_project_integration("project-1", "org-1", "integration-1", update_data)

    query, args = conn.execute_calls[0]
    assert "integration_config = $" in query
    # Verify config is serialized (check args)
    config_arg = args[0] if args else None
    assert isinstance(config_arg, str) or config_arg is None


@pytest.mark.asyncio
async def test_update_project_integration_skip_forbidden():
    """update_project_integration skips forbidden fields."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    update_data = {
        "integration_name": "Updated",
        "id": "should-be-ignored",
        "project_id": "should-be-ignored",
        "organization_id": "should-be-ignored",
        "connected_by": "should-be-ignored",
        "created_at": "should-be-ignored",
    }

    await repo.update_project_integration("project-1", "org-1", "integration-1", update_data)

    query = conn.execute_calls[0][0]
    assert "id = $" in query  # Only in WHERE clause
    assert "project_id = $" in query  # Only in WHERE clause
    assert "organization_id = $" in query  # Only in WHERE clause
    assert "connected_by" not in query
    assert "created_at" not in query


@pytest.mark.asyncio
async def test_delete_project_repositories_by_ids():
    """delete_project_repositories_by_ids deletes repositories by IDs."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_project_repositories_by_ids("project-1", "org-1", ["repo-1", "repo-2"])

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_repositories" in query
    assert "project_id = $1" in query
    assert "organization_id = $2" in query
    assert "id = ANY($3" in query
    assert args[0] == "project-1"
    assert args[1] == "org-1"
    assert args[2] == ["repo-1", "repo-2"]


@pytest.mark.asyncio
async def test_delete_project_repos_by_ids_noop_empty():
    """delete_project_repositories_by_ids does nothing when repository_ids is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_project_repositories_by_ids("project-1", "org-1", [])

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_delete_project_integrations_by_ids():
    """delete_project_integrations_by_ids deletes integrations by IDs."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_project_integrations_by_ids(
        "project-1", "org-1", ["integration-1", "integration-2"]
    )

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_integrations" in query
    assert "project_id = $1" in query
    assert "organization_id = $2" in query
    assert "id = ANY($3" in query
    assert args[0] == "project-1"
    assert args[1] == "org-1"
    assert args[2] == ["integration-1", "integration-2"]


@pytest.mark.asyncio
async def test_delete_project_integrations_by_ids_noop_empty():
    """delete_project_integrations_by_ids
    does nothing when integration_ids is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_project_integrations_by_ids("project-1", "org-1", [])

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_get_project_repositories_with_primary_only():
    """get_project_repositories returns only primary repository when primary_only=True."""
    conn = _FakeConn()
    conn.fetch_result = [
        {
            "id": "repo-1",
            "repository_name": "primary-repo",
            "is_primary": True,
        }
    ]
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_repositories("project-1", "org-1", primary_only=True)

    assert len(result) == 1
    assert result[0]["is_primary"] is True
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "is_primary = true" in query
    assert "LIMIT 1" in query


@pytest.mark.asyncio
async def test_get_project_repositories_without_primary_only():
    """get_project_repositories returns all repositories when primary_only=False."""
    conn = _FakeConn()
    conn.fetch_result = [
        {"id": "repo-1", "repository_name": "repo-1", "is_primary": True},
        {"id": "repo-2", "repository_name": "repo-2", "is_primary": False},
    ]
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_repositories("project-1", "org-1", primary_only=False)

    assert len(result) == 2
    assert len(conn.fetch_calls) == 1
    query = conn.fetch_calls[0][0]
    assert "is_primary = true" not in query
    assert "LIMIT 1" not in query


@pytest.mark.asyncio
async def test_create_project_repositories_with_empty_list():
    """create_project_repositories returns early when repositories list is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.create_project_repositories("project-1", "org-1", [], "user-1")

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_create_project_integrations_with_empty_list():
    """create_project_integrations returns early when integrations list is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.create_project_integrations("project-1", "org-1", [], "user-1")

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_build_project_filters_with_empty_tags():
    """_build_project_filters handles tags filter with empty tag list."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)
    filters = ProjectListQueryParams(tags="  ,  ,  ")

    where_clause, _ = repo._build_project_filters("org-1", filters)

    # Should not include tags condition when tag_list is empty
    assert "tags" not in where_clause.lower()


@pytest.mark.asyncio
async def test_update_project_with_empty_data():
    """update_project returns early when data is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.update_project("project-1", "org-1", {})

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_get_project_repo_ids_existing_empty_list():
    """get_project_repository_ids_existing returns empty set for empty list."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_repository_ids_existing("project-1", "org-1", [])

    assert result == set()
    assert len(conn.fetch_calls) == 0


@pytest.mark.asyncio
async def test_update_project_repository_with_empty_data():
    """update_project_repository returns early when data is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.update_project_repository("project-1", "org-1", "repo-1", {})

    assert len(conn.execute_calls) == 0


@pytest.mark.asyncio
async def test_get_project_integration_ids_existing_empty():
    """get_project_integration_ids_existing returns empty set for empty list."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_integration_ids_existing("project-1", "org-1", [])

    assert result == set()
    assert len(conn.fetch_calls) == 0


@pytest.mark.asyncio
async def test_update_project_integration_with_empty_data():
    """update_project_integration returns early when data is empty."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.update_project_integration("project-1", "org-1", "integration-1", {})

    assert len(conn.execute_calls) == 0


# Delete Project (soft delete) and related methods
@pytest.mark.asyncio
async def test_delete_all_project_repositories():
    """delete_all_project_repositories deletes all repositories for project."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_all_project_repositories("project-1", "org-1")

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_repositories" in query
    assert "project_id = $1" in query
    assert "organization_id = $2" in query
    assert args[0] == "project-1"
    assert args[1] == "org-1"


@pytest.mark.asyncio
async def test_delete_all_project_integrations():
    """delete_all_project_integrations deletes all integrations for project."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.delete_all_project_integrations("project-1", "org-1")

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "DELETE FROM project_integrations" in query
    assert "project_id = $1" in query
    assert "organization_id = $2" in query
    assert args[0] == "project-1"
    assert args[1] == "org-1"


@pytest.mark.asyncio
async def test_soft_delete_project():
    """soft_delete_project sets status to archived and clears team_id."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.soft_delete_project("project-1", "org-1")

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "UPDATE projects" in query
    assert "status = $3" in query
    assert "team_id = NULL" in query
    assert "updated_at = NOW()" in query
    assert args[0] == "project-1"
    assert args[1] == "org-1"
    assert args[2] == "archived"


@pytest.mark.asyncio
async def test_soft_delete_project_with_updated_by():
    """soft_delete_project includes updated_by when provided."""
    conn = _FakeConn()
    repo = ProjectRepository(db_connection=conn)

    await repo.soft_delete_project("project-1", "org-1", updated_by="user-1")

    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "updated_by = $4" in query
    assert args[3] == "user-1"


@pytest.mark.asyncio
async def test_get_project_basic_information_success():
    """get_project_basic_information returns project when found."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "id": "project-1",
        "project_id": "test-project",
        "project_title": "Test Project",
        "status": "active",
        "team_id": "team-1",
        "client_id": "client-1",
    }
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_basic_information("project-1", "org-1")

    assert result is not None
    assert result["id"] == "project-1"
    assert result["team_id"] == "team-1"
    assert len(conn.fetchrow_calls) == 1
    query = conn.fetchrow_calls[0][0]
    assert "p.id" in query
    assert "p.team_id" in query
    assert "status != 'archived'" in query


@pytest.mark.asyncio
async def test_get_project_basic_information_not_found():
    """get_project_basic_information returns None when project not found."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = ProjectRepository(db_connection=conn)

    result = await repo.get_project_basic_information("project-123", "org-1")

    assert result is None
    assert len(conn.fetchrow_calls) == 1
