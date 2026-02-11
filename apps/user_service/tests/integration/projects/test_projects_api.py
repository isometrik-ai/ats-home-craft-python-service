"""Integration tests for projects API endpoints."""

import pytest

from apps.user_service.app.schemas.clients import PrimaryContactInfo
from apps.user_service.app.schemas.projects import (
    ClientInfo,
    ProjectDetailData,
    ProjectLeadInfo,
    TechStack,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success


def _ctx():
    """Return a reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


@pytest.mark.asyncio
async def test_create_project(monkeypatch, client):
    """Create a new project."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_create_project(self, request_data):
        """Fake create project."""
        del self
        assert request_data.project_title == "E-Commerce Platform Redesign"
        assert request_data.client_id == "client-123"
        assert request_data.status == "active"
        return None

    monkeypatch.setattr(
        "apps.user_service.app.api.projects.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectService.create_project",
        fake_create_project,
    )

    res = await client.post(
        "/v1/projects",
        json={
            "project_title": "E-Commerce Platform Redesign",
            "project_description": "Complete redesign and rebuild",
            "client_id": "client-123",
            "status": "active",
            "priority": "high",
        },
    )
    assert_success(res, 201)


@pytest.mark.asyncio
async def test_list_projects(monkeypatch, client):
    """List projects with filtering and pagination."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_list_projects(self, filters):
        """Fake list projects."""
        del self, filters
        return (
            [
                {
                    "id": "project-1",
                    "project_id": "ecommerce-platform-redesign",
                    "project_title": "E-Commerce Platform Redesign",
                    "client": {
                        "id": "client-1",
                        "name": "Client 1",
                        "type": "person",
                    },
                    "project_lead": {
                        "id": "member-1",
                        "full_name": "John Doe",
                    },
                    "team_size": 5,
                    "status": "active",
                    "priority": "high",
                    "category": "E-Commerce",
                    "practice_areas": ["Web Development"],
                    "start_date": None,
                    "tags": [],
                    "tech_stack": {
                        "frontend": ["React"],
                        "backend": ["Node.js"],
                        "database": [],
                        "cloud": [],
                        "mobile": [],
                        "ai_ml": [],
                        "other": [],
                    },
                }
            ],
            1,
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.projects.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectService.list_projects",
        fake_list_projects,
    )

    res = await client.get("/v1/projects?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == "project-1"
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_project_details(monkeypatch, client):
    """Get project details by ID."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_get_project_details(self, project_id):
        """Fake get project details."""
        del self
        assert project_id == "project-123"
        return ProjectDetailData(
            id="project-123",
            organization_id="org-1",
            project_id="ecommerce-platform-redesign",
            project_title="E-Commerce Platform Redesign",
            project_description="Complete redesign and rebuild",
            client=ClientInfo(
                id="client-123",
                name="Client 1",
                type="person",
                primary_contact=PrimaryContactInfo(
                    first_name="John",
                    last_name="Doe",
                    title=None,
                    email="john@example.com",
                    phone_isd_code=None,
                    phone=None,
                ),
            ),
            project_lead=ProjectLeadInfo(
                id="member-1",
                full_name="John Doe",
            ),
            status="active",
            priority="high",
            project_category=["E-Commerce"],
            practice_areas=["Web Development"],
            start_date=None,
            target_end_date=None,
            actual_end_date=None,
            billing_info=None,
            total_billed="0.00",
            total_hours="0.00",
            tech_stack=TechStack(
                frontend=["React"],
                backend=["Node.js"],
                database=[],
                cloud=[],
                mobile=[],
                ai_ml=[],
                other=[],
            ),
            project_goals=None,
            success_criteria=None,
            additional_ai_context=None,
            primary_pm_tool=None,
            primary_repo_url=None,
            tags=[],
            custom_fields={},
            is_billable=True,
            is_internal=False,
            team={
                "id": "team-1",
                "name": "E-Commerce Platform Redesign",
                "project_lead": None,
                "tech_lead": None,
                "members": [],
            },
            repositories=[],
            integrations=[],
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            created_by=None,
            updated_by=None,
        )

    monkeypatch.setattr(
        "apps.user_service.app.api.projects.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectService.get_project_details",
        fake_get_project_details,
    )

    res = await client.get("/v1/projects/project-123")
    body = assert_success(res, 200)
    assert body["data"]["id"] == "project-123"
    assert body["data"]["project_title"] == "E-Commerce Platform Redesign"


@pytest.mark.asyncio
async def test_list_projects_with_filters(monkeypatch, client):
    """List projects with search and filter parameters."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_list_projects(self, filters):
        """Fake list projects with filters."""
        del self
        assert filters.search == "ecommerce"
        assert filters.client_id == "client-123"
        assert filters.status == "active"
        assert filters.priority == "high"
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.api.projects.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectService.list_projects",
        fake_list_projects,
    )

    res = await client.get(
        (
            "/v1/projects?search=ecommerce&client_id=client-123&status=active"
            "&priority=high&page=1&page_size=20"
        )
    )
    body = assert_success(res, 200)
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_projects_empty_result(monkeypatch, client):
    """List projects returns empty result with proper message."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        """Fake permissions check."""
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    async def fake_list_projects(self, filters):
        """Fake list projects returning empty."""
        del self, filters
        return ([], 0)

    monkeypatch.setattr(
        "apps.user_service.app.api.projects.check_permissions",
        fake_check_permissions,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectService.list_projects",
        fake_list_projects,
    )

    res = await client.get("/v1/projects?page=1&page_size=20")
    body = assert_success(res, 200)
    assert body["data"] == []
    assert body["total"] == 0
