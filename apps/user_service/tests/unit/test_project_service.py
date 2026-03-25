"""Unit tests for ProjectService business logic."""

# pylint: disable=too-many-lines
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from apps.user_service.app.schemas.enums import (
    BillingType,
    IntegrationType,
    PaymentTerms,
    ProjectPriority,
    ProjectStatus,
    RepositoryPlatform,
    SyncDirection,
    TeamRoles,
)
from apps.user_service.app.schemas.projects import (
    BillingInfo,
    BudgetInfo,
    CreateProjectRequest,
    IntegrationInput,
    IntegrationsUpdate,
    IntegrationUpdateItem,
    ProjectListQueryParams,
    RepositoriesUpdate,
    RepositoryInput,
    RepositoryUpdateItem,
    TeamMemberInput,
    TeamMembersUpdate,
    TeamMemberUpdateItem,
    TechStack,
    UpdateProjectRequest,
)
from apps.user_service.app.schemas.teams import TeamDbDelete
from apps.user_service.app.services.project_service import ProjectService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)


class _FakeProjectRepo:
    """Lightweight fake project repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.project_id_unique = True
        self.project_result = {"id": "project-1"}
        self.projects_list_result = []
        self.projects_count_result = 0
        self.project_with_client_result = None
        self.repositories_result = []
        self.integrations_result = []
        self.project_basic_result = {"id": "project-1", "team_id": "team-1"}

    async def get_project_basic_information(self, project_id, organization_id):
        """Get project basic information."""
        self.calls["get_project_basic_information"] = (project_id, organization_id)
        return self.project_basic_result

    async def delete_all_project_repositories(self, project_id, organization_id):
        """Delete all project repositories."""
        self.calls["delete_all_project_repositories"] = (project_id, organization_id)
        return None

    async def delete_all_project_integrations(self, project_id, organization_id):
        """Delete all project integrations."""
        self.calls["delete_all_project_integrations"] = (project_id, organization_id)
        return None

    async def soft_delete_project(self, project_id, organization_id, updated_by=None):
        """Soft delete project."""
        self.calls["soft_delete_project"] = (
            project_id,
            organization_id,
            updated_by,
        )
        return None

    async def check_project_id_unique(self, project_id, organization_id, exclude_id=None):
        """Return uniqueness flag."""
        self.calls["check_project_id_unique"] = (project_id, organization_id, exclude_id)
        return self.project_id_unique

    async def create_project(self, project_data):
        """Create project and return result."""
        self.calls["create_project"] = project_data
        return self.project_result

    async def create_project_repositories(
        self,
        project_id,
        organization_id,
        repositories,
        created_by,
    ):
        """Create project repositories."""
        self.calls["create_project_repositories"] = {
            "project_id": project_id,
            "organization_id": organization_id,
            "repositories": repositories,
            "created_by": created_by,
        }
        return []

    async def create_project_integrations(
        self,
        project_id,
        organization_id,
        integrations,
        connected_by,
    ):
        """Create project integrations."""
        self.calls["create_project_integrations"] = {
            "project_id": project_id,
            "organization_id": organization_id,
            "integrations": integrations,
            "connected_by": connected_by,
        }
        return []

    async def get_projects_list(self, organization_id, filters):
        """Get projects list."""
        self.calls["get_projects_list"] = (organization_id, filters)
        return self.projects_list_result, self.projects_count_result

    async def get_project_with_client(self, project_id, organization_id):
        """Get project with client."""
        self.calls["get_project_with_client"] = (project_id, organization_id)
        return self.project_with_client_result

    async def get_project_repositories(self, project_id, organization_id, *, primary_only=False):
        """Get project repositories."""
        self.calls["get_project_repositories"] = (project_id, organization_id, primary_only)
        if primary_only:
            return [r for r in self.repositories_result if r.get("is_primary")] or []
        return self.repositories_result

    async def get_project_integrations(self, project_id, organization_id):
        """Get project integrations."""
        self.calls["get_project_integrations"] = (project_id, organization_id)
        return self.integrations_result

    async def update_project(self, project_id, organization_id, data):
        """Update project."""
        self.calls["update_project"] = (project_id, organization_id, data)
        return None

    async def update_project_repository(self, project_id, organization_id, repository_id, data):
        """Update project repository."""
        self.calls["update_project_repository"] = (
            project_id,
            organization_id,
            repository_id,
            data,
        )
        return None

    async def delete_project_repositories_by_ids(self, project_id, organization_id, repository_ids):
        """Delete project repositories."""
        self.calls["delete_project_repositories_by_ids"] = (
            project_id,
            organization_id,
            repository_ids,
        )
        return None

    async def update_project_integration(self, project_id, organization_id, integration_id, data):
        """Update project integration."""
        self.calls["update_project_integration"] = (
            project_id,
            organization_id,
            integration_id,
            data,
        )
        return None

    async def delete_project_integrations_by_ids(
        self, project_id, organization_id, integration_ids
    ):
        """Delete project integrations."""
        self.calls["delete_project_integrations_by_ids"] = (
            project_id,
            organization_id,
            integration_ids,
        )
        return None


class _FakeClientRepo:
    """Lightweight fake client repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.client_details_result = {"id": "client-1", "name": "Client 1"}

    async def get_client_details_with_primary_contact(self, client_id, organization_id):
        """Get client details."""
        self.calls["get_client_details_with_primary_contact"] = (client_id, organization_id)
        return self.client_details_result


class _FakeTeamRepo:
    """Lightweight fake team repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.members_valid = True
        self.team_id_result = "team-1"
        self.team_detail_result = None
        self.member_rows_result = []

    async def validate_organization_members(self, member_ids, organization_id):
        """Validate members."""
        self.calls["validate_organization_members"] = (member_ids, organization_id)
        return self.members_valid

    async def create_team(self, team_data):
        """Create team."""
        self.calls["create_team"] = team_data
        return self.team_id_result

    async def get_team_detail(self, team_id, organization_id):
        """Get team detail."""
        self.calls["get_team_detail"] = (team_id, organization_id)
        return self.team_detail_result, self.member_rows_result

    async def delete_team_members_by_user_ids(self, team_id, user_ids):
        """Delete team members."""
        self.calls["delete_team_members_by_user_ids"] = (team_id, user_ids)
        return None

    async def update_team_members_additional_data(self, team_id, organization_id, member_updates):
        """Update team members."""
        self.calls["update_team_members_additional_data"] = (
            team_id,
            organization_id,
            member_updates,
        )
        return None

    async def _insert_team_members(self, team_id, member_data, added_by):
        """Insert team members."""
        self.calls["_insert_team_members"] = (team_id, member_data, added_by)
        return None

    async def delete_team_and_members(self, team_input: TeamDbDelete):
        """Delete team and members."""
        self.calls["delete_team_and_members"] = team_input
        return None


class _FakeCustomFieldService:
    """Fake CustomFieldService for ProjectService unit tests."""

    def __init__(self):
        self.calls: dict[str, Any] = {}

    async def validate_and_format_custom_fields(
        self,
        custom_fields: dict[str, Any],
        entity_type,
        required_custom_fields_for_presence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and format custom fields."""
        self.calls["validate_and_format_custom_fields"] = {
            "custom_fields": custom_fields,
            "entity_type": entity_type,
            "required_custom_fields_for_presence": required_custom_fields_for_presence,
        }
        # Keep unit tests focused on service orchestration; return unchanged.
        return dict(custom_fields)

    async def ensure_required_fields_present(self, custom_fields, entity_type) -> None:
        """Ensure required fields are present."""
        self.calls["ensure_required_fields_present"] = {
            "custom_fields": custom_fields,
            "entity_type": entity_type,
        }
        return None


def _ctx(org_id="org-1"):
    """Build reusable UserContext for tests."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id=org_id,
        user_type="admin",
    )


def _service_with_fakes(monkeypatch):
    """Instantiate ProjectService with fake repositories."""
    fake_project_repo = _FakeProjectRepo()
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()
    fake_custom_fields = _FakeCustomFieldService()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.CustomFieldService",
        lambda db_connection=None, user_context=None: fake_custom_fields,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    return service, fake_project_repo, fake_client_repo, fake_team_repo, fake_custom_fields


def test_generate_project_id_normalizes_title():
    """_generate_project_id normalizes whitespace and punctuation."""
    result = ProjectService._generate_project_id("  Hello, World! 2024 ")
    assert result == "hello-world-2024"


def test_generate_project_id_defaults_when_empty():
    """_generate_project_id falls back to 'project' for empty input."""
    assert ProjectService._generate_project_id("!!!") == "project"


def test_prepare_billing_info_dict_returns_none(monkeypatch):
    """_prepare_billing_info_dict returns None when no billing info provided."""
    service, *_ = _service_with_fakes(monkeypatch)

    assert service._prepare_billing_info_dict(None) is None


def test_prepare_billing_info_dict_converts_values(monkeypatch):
    """_prepare_billing_info_dict converts decimals and enums to primitives."""
    service, *_ = _service_with_fakes(monkeypatch)
    billing_info = BillingInfo(
        billing_type=BillingType.TIME_AND_MATERIALS,
        hourly_rate=Decimal("150.50"),
        currency="USD",
        payment_terms=PaymentTerms.NET_30,
        budget=BudgetInfo(total=Decimal("25000")),
    )

    billing_dict = service._prepare_billing_info_dict(billing_info)

    assert billing_dict == {
        "billing_type": BillingType.TIME_AND_MATERIALS.value,
        "hourly_rate": 150.5,
        "currency": "USD",
        "payment_terms": PaymentTerms.NET_30.value,
        "budget": {"total": 25000.0},
    }


def test_prepare_tech_stack_dict_defaults(monkeypatch):
    """_prepare_tech_stack_dict returns empty lists when tech stack missing."""
    service, *_ = _service_with_fakes(monkeypatch)

    stack_dict = service._prepare_tech_stack_dict(None)

    assert stack_dict == {
        "frontend": [],
        "backend": [],
        "database": [],
        "cloud": [],
        "mobile": [],
        "ai_ml": [],
        "other": [],
    }


def test_prepare_tech_stack_dict_preserves_values(monkeypatch):
    """_prepare_tech_stack_dict preserves provided tech stack lists."""
    service, *_ = _service_with_fakes(monkeypatch)
    tech_stack = TechStack(frontend=["React"], backend=["FastAPI"])

    stack_dict = service._prepare_tech_stack_dict(tech_stack)

    assert stack_dict["frontend"] == ["React"]
    assert stack_dict["backend"] == ["FastAPI"]


def test_prepare_project_data_populates_optional_fields(monkeypatch):
    """_prepare_project_data includes optional and derived fields."""
    service, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Complex Project",
        project_description="Desc",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        priority=ProjectPriority.HIGH,
        project_category=["category"],
        practice_areas=["practice"],
        start_date=date(2024, 1, 1),
        target_end_date=date(2024, 6, 1),
        billing_info=BillingInfo(
            billing_type=BillingType.TIME_AND_MATERIALS,
            hourly_rate=Decimal("180"),
            currency="USD",
            payment_terms=PaymentTerms.NET_15,
            budget=BudgetInfo(total=Decimal("50000")),
        ),
        tech_stack=TechStack(
            frontend=["Vue"],
            backend=["FastAPI"],
            database=["Postgres"],
            cloud=["AWS"],
            ai_ml=["OpenAI"],
            other=["Docker"],
        ),
        project_goals="Goals",
        success_criteria="Criteria",
        additional_ai_context="Context",
        tags=["alpha"],
        custom_fields={"stage": "discovery"},
        is_billable=False,
        is_internal=True,
        repositories=[
            RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_owner="org",
                repository_name="frontend",
                repository_url="https://github.com/org/frontend",
                purpose="Main app",
                is_primary=True,
            )
        ],
        integrations=[
            IntegrationInput(
                integration_type=IntegrationType.LINEAR,
                integration_name="Linear",
                external_project_id="EXT-1",
                sync_direction=SyncDirection.INBOUND,
                is_primary=True,
            )
        ],
    )

    project_data = service._prepare_project_data(
        request, project_id="complex-project", team_id="team-1"
    )

    assert project_data["organization_id"] == service.user_context.organization_id
    assert project_data["primary_repo_url"] == "https://github.com/org/frontend"
    assert project_data["billing_info"]["budget"]["total"] == 50000.0
    assert project_data["tech_stack"]["backend"] == ["FastAPI"]
    assert project_data["team_id"] == "team-1"
    assert project_data["is_billable"] is False
    assert project_data["is_internal"] is True


@pytest.mark.asyncio
async def test_create_project_repositories_builds_payload(monkeypatch):
    """_create_project_repositories delegates with normalized payload."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Repo Project",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        repositories=[
            RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_owner="org",
                repository_name="frontend",
                repository_url="https://github.com/org/frontend",
                purpose="Main app",
                is_primary=True,
            ),
            RepositoryInput(
                platform=RepositoryPlatform.GITLAB,
                repository_owner="org",
                repository_name="backend",
                repository_url="https://gitlab.com/org/backend",
            ),
        ],
    )

    await service._create_project_repositories("project-1", request)

    call = fake_project_repo.calls["create_project_repositories"]
    assert call["project_id"] == "project-1"
    assert call["organization_id"] == service.user_context.organization_id
    assert call["created_by"] == service.user_context.user_id
    assert call["repositories"][0]["platform"] == RepositoryPlatform.GITHUB.value
    assert call["repositories"][1]["primary_branch"] == "main"


@pytest.mark.asyncio
async def test_create_project_repositories_skips_when_missing(monkeypatch):
    """_create_project_repositories skips repository creation when none provided."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Repo Project",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        repositories=None,
    )

    await service._create_project_repositories("project-1", request)

    assert "create_project_repositories" not in fake_project_repo.calls


@pytest.mark.asyncio
async def test_create_project_integrations_builds_payload(monkeypatch):
    """_create_project_integrations delegates with serialized config."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Integration Project",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        integrations=[
            IntegrationInput(
                integration_type=IntegrationType.JIRA,
                integration_name="Jira Cloud",
                external_project_key="Jira-1",
                auto_sync=False,
                sync_direction=SyncDirection.OUTBOUND,
                sync_interval_minutes=45,
                integration_purpose="Issue tracking",
                integration_config={"project_key": "Jira-1"},
                is_primary=True,
            )
        ],
    )

    await service._create_project_integrations("project-1", request)

    call = fake_project_repo.calls["create_project_integrations"]
    assert call["project_id"] == "project-1"
    assert call["organization_id"] == service.user_context.organization_id
    payload = call["integrations"][0]
    assert payload["integration_type"] == IntegrationType.JIRA.value
    assert payload["sync_direction"] == SyncDirection.OUTBOUND.value
    assert payload["auto_sync"] is False
    assert payload["integration_config"] == {"project_key": "Jira-1"}


@pytest.mark.asyncio
async def test_create_project_integrations_skips_when_missing(monkeypatch):
    """_create_project_integrations skips when integrations absent."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Integration Project",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        integrations=None,
    )

    await service._create_project_integrations("project-1", request)

    assert "create_project_integrations" not in fake_project_repo.calls


def test_build_team_info_classifies_leads():
    """_build_team_info separates project lead, tech lead, and members."""
    team_data = {"id": "team-1", "name": "Delivery"}
    member_rows = [
        {
            "user_id": "lead-1",
            "additional_data": json.dumps(
                {
                    "role": TeamRoles.PROJECT_LEAD.value,
                    "allocation_percentage": 60,
                    "hourly_rate": 140.0,
                    "role_description": "Lead project",
                }
            ),
            "first_name": "Project",
            "last_name": "Lead",
            "email": "lead@example.com",
        },
        {
            "user_id": "tech-1",
            "additional_data": json.dumps(
                {
                    "role": TeamRoles.TECH_LEAD.value,
                    "allocation_percentage": 70,
                    "hourly_rate": 150.0,
                }
            ),
            "first_name": "Tech",
            "last_name": "Lead",
            "email": "tech@example.com",
        },
        {
            "user_id": "member-1",
            "additional_data": json.dumps(
                {
                    "role": "Engineer",
                    "allocation_percentage": 100,
                    "hourly_rate": 120.0,
                }
            ),
            "first_name": "Team",
            "last_name": "Member",
            "email": "member@example.com",
        },
    ]

    team_info = ProjectService._build_team_info(team_data, member_rows)

    assert team_info["project_lead"]["id"] == "lead-1"
    assert team_info["tech_lead"]["id"] == "tech-1"
    assert len(team_info["members"]) == 1
    assert team_info["members"][0]["role"] == "Engineer"


def test_map_repository_to_detail_defaults():
    """_map_repository_to_detail fills defaults and formats datetimes."""
    now = datetime.now(timezone.utc)
    repo_detail = ProjectService._map_repository_to_detail(
        {
            "id": "repo-1",
            "platform": "github",
            "repository_name": "frontend",
            "repository_url": "https://github.com/org/frontend",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert repo_detail["id"] == "repo-1"
    assert repo_detail["primary_branch"] == "main"
    assert repo_detail["is_private"] is True
    assert repo_detail["created_at"] == now.isoformat()


def test_map_integration_to_detail_defaults():
    """_map_integration_to_detail fills defaults and formats datetimes."""
    now = datetime.now(timezone.utc)
    integration_detail = ProjectService._map_integration_to_detail(
        {
            "id": "integration-1",
            "integration_type": "linear",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert integration_detail["integration_type"] == "linear"
    assert integration_detail["sync_direction"] == "bidirectional"
    assert integration_detail["auto_sync"] is True
    assert integration_detail["created_at"] == now.isoformat()


def test_build_billing_info_parses_json():
    """_build_billing_info parses JSON and converts decimals."""
    raw_billing = json.dumps(
        {
            "billing_type": "fixed_price",
            "hourly_rate": 125.25,
            "currency": "USD",
            "billing_cycle": "Monthly",
            "retainer_amount": 2000.0,
        }
    )

    billing_info = ProjectService._build_billing_info(raw_billing)

    assert billing_info["billing_type"] == "fixed_price"
    assert billing_info["hourly_rate"] == Decimal("125.25")
    assert billing_info["retainer_amount"] == Decimal("2000.0")


@pytest.mark.asyncio
async def test_get_project_details_includes_team(monkeypatch):
    """get_project_details returns team info and derived project lead."""
    service, fake_project_repo, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    now = datetime.now(timezone.utc)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "organization_id": "org-1",
        "project_id": "test-project",
        "project_title": "Test Project",
        "project_description": "Desc",
        "client_uuid": "client-1",
        "client_name": "Client 1",
        "client_type": "person",
        "client_first_name": "John",
        "client_last_name": "Doe",
        "client_title": "Mr",
        "client_email": "john@example.com",
        "client_phone_isd_code": "+1",
        "client_phone_number": "5550000",
        "team_id": "team-1",
        "status": "active",
        "priority": "high",
        "project_category": ["Category"],
        "practice_areas": ["Practice"],
        "start_date": now.date().isoformat(),
        "target_end_date": now.date().isoformat(),
        "actual_end_date": None,
        "billing_info": json.dumps({"billing_type": "fixed_price"}),
        "total_billed": "1000",
        "total_hours": "40",
        "tech_stack": json.dumps({"backend": ["FastAPI"]}),
        "project_goals": "Goals",
        "success_criteria": "Criteria",
        "additional_ai_context": None,
        "primary_pm_tool": "linear",
        "primary_repo_url": "https://github.com/org/frontend",
        "tags": ["alpha"],
        "custom_fields": json.dumps({"stage": "delivery"}),
        "is_billable": True,
        "is_internal": False,
        "created_at": now,
        "updated_at": now,
        "created_by": "creator-1",
        "updated_by": "updater-1",
    }
    fake_team_repo.team_detail_result = {"id": "team-1", "name": "Delivery"}
    fake_team_repo.member_rows_result = [
        {
            "user_id": "lead-1",
            "additional_data": json.dumps(
                {
                    "role": TeamRoles.PROJECT_LEAD.value,
                    "allocation_percentage": 50,
                    "hourly_rate": 135.0,
                }
            ),
            "first_name": "Project",
            "last_name": "Lead",
            "email": "lead@example.com",
        }
    ]
    fake_project_repo.repositories_result = [
        {
            "id": "repo-1",
            "platform": "github",
            "repository_name": "frontend",
            "repository_url": "https://github.com/org/frontend",
            "created_at": now,
            "updated_at": now,
        }
    ]
    fake_project_repo.integrations_result = [
        {
            "id": "integration-1",
            "integration_type": "linear",
            "created_at": now,
            "updated_at": now,
        }
    ]

    detail = await service.get_project_details("project-1")

    assert detail.id == "project-1"
    assert detail.team is not None
    assert detail.team.project_lead.id == "lead-1"
    assert detail.repositories[0].platform == "github"
    assert detail.integrations[0].integration_type == "linear"


@pytest.mark.asyncio
async def test_create_project_raises_client_not_found(monkeypatch):
    """Raises NotFoundException when client not found."""
    fake_project_repo = _FakeProjectRepo()
    fake_client_repo = _FakeClientRepo()
    fake_client_repo.client_details_result = None
    fake_team_repo = _FakeTeamRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    request_data = CreateProjectRequest(
        project_title="Test Project",
        client_id="client-123",
        status=ProjectStatus.ACTIVE,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_project(request_data)

    assert exc_info.value.message_key == "projects.errors.client_not_found"


@pytest.mark.asyncio
async def test_create_project_raises_team_member_not_found(monkeypatch):
    """Raises NotFoundException when team member not found."""
    fake_project_repo = _FakeProjectRepo()
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()
    fake_team_repo.members_valid = False

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    request_data = CreateProjectRequest(
        project_title="Test Project",
        client_id="client-123",
        status=ProjectStatus.ACTIVE,
        team_members=[
            {
                "member_id": "member-123",
                "role": "Developer",
                "allocation_percentage": 100,
            }
        ],
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_project(request_data)

    assert exc_info.value.message_key == "projects.errors.team_member_not_found"


@pytest.mark.asyncio
async def test_create_project_raises_project_title_exists(monkeypatch):
    """Raises ConflictException when project title already exists."""
    fake_project_repo = _FakeProjectRepo()
    fake_project_repo.project_id_unique = False
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    request_data = CreateProjectRequest(
        project_title="Existing Project",
        client_id="client-123",
        status=ProjectStatus.ACTIVE,
    )

    with pytest.raises(ConflictException) as exc_info:
        await service.create_project(request_data)

    assert exc_info.value.message_key == "projects.errors.project_title_exists"


@pytest.mark.asyncio
async def test_create_project_success(monkeypatch):
    """Successfully creates project with all components."""
    fake_project_repo = _FakeProjectRepo()
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()
    fake_custom_fields = _FakeCustomFieldService()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.CustomFieldService",
        lambda db_connection=None, user_context=None: fake_custom_fields,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    request_data = CreateProjectRequest(
        project_title="New Project",
        client_id="client-123",
        status=ProjectStatus.ACTIVE,
        priority=ProjectPriority.HIGH,
    )

    await service.create_project(request_data)

    assert "create_project" in fake_project_repo.calls
    assert fake_project_repo.calls["create_project"]["project_title"] == "New Project"
    assert fake_project_repo.calls["create_project"]["client_id"] == "client-123"


@pytest.mark.asyncio
async def test_create_project_with_team_members(monkeypatch):
    """Successfully creates project with team members."""
    fake_project_repo = _FakeProjectRepo()
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()
    fake_custom_fields = _FakeCustomFieldService()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.CustomFieldService",
        lambda db_connection=None, user_context=None: fake_custom_fields,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    request_data = CreateProjectRequest(
        project_title="Project With Team",
        client_id="client-123",
        status=ProjectStatus.ACTIVE,
        team_members=[
            {
                "member_id": "member-1",
                "role": "Project Lead",
                "allocation_percentage": 100,
                "hourly_rate": Decimal("150.00"),
            }
        ],
    )

    await service.create_project(request_data)

    assert "create_team" in fake_team_repo.calls
    assert fake_team_repo.calls["create_team"].name == "Project With Team"
    assert "create_project" in fake_project_repo.calls
    assert fake_project_repo.calls["create_project"]["team_id"] == "team-1"


@pytest.mark.asyncio
async def test_list_projects_success(monkeypatch):
    """Successfully lists projects."""
    fake_project_repo = _FakeProjectRepo()
    fake_project_repo.projects_list_result = [
        {
            "id": "project-1",
            "project_id": "test-project",
            "project_title": "Test Project",
            "client_id": "client-1",
            "client_name": "Client 1",
            "client_type": "person",
            "project_lead_id": None,
            "project_lead_name": None,
            "team_size": 0,
            "status": "active",
            "priority": "high",
            "project_category": [],
            "practice_areas": [],
            "start_date": None,
            "tags": [],
            "tech_stack": {},
            "client_first_name": "John",
            "client_last_name": "Doe",
            "client_title": None,
            "client_email": "john@example.com",
            "client_phone_isd_code": None,
            "client_phone_number": None,
        }
    ]
    fake_project_repo.projects_count_result = 1
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)
    filters = ProjectListQueryParams(page=1, page_size=20)

    projects, total = await service.list_projects(filters)

    assert len(projects) == 1
    assert total == 1
    assert projects[0].project_title == "Test Project"
    assert "get_projects_list" in fake_project_repo.calls


@pytest.mark.asyncio
async def test_get_project_details_raises_not_found(monkeypatch):
    """Raises NotFoundException when project not found."""
    fake_project_repo = _FakeProjectRepo()
    fake_project_repo.project_with_client_result = None
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_project_details("project-123")

    assert exc_info.value.message_key == "projects.errors.project_not_found"


@pytest.mark.asyncio
async def test_get_project_details_success(monkeypatch):
    """Successfully retrieves project details."""
    fake_project_repo = _FakeProjectRepo()
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "organization_id": "org-1",
        "project_id": "test-project",
        "project_title": "Test Project",
        "project_description": "Test Description",
        "client_uuid": "client-1",
        "client_name": "Client 1",
        "client_type": "person",
        "client_first_name": "John",
        "client_last_name": "Doe",
        "client_title": None,
        "client_email": "john@example.com",
        "client_phone_isd_code": None,
        "client_phone_number": None,
        "team_id": None,
        "status": "active",
        "priority": "high",
        "project_category": [],
        "practice_areas": [],
        "start_date": None,
        "target_end_date": None,
        "actual_end_date": None,
        "billing_info": None,
        "total_billed": "0.00",
        "total_hours": "0.00",
        "tech_stack": {},
        "project_goals": None,
        "success_criteria": None,
        "additional_ai_context": None,
        "primary_pm_tool": None,
        "primary_repo_url": None,
        "tags": [],
        "custom_fields": {},
        "is_billable": True,
        "is_internal": False,
        "created_at": None,
        "updated_at": None,
        "created_by": None,
        "updated_by": None,
    }
    fake_client_repo = _FakeClientRepo()
    fake_team_repo = _FakeTeamRepo()

    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ProjectRepository",
        lambda db_connection=None: fake_project_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.ClientRepository",
        lambda db_connection=None: fake_client_repo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.project_service.TeamRepository",
        lambda db_connection=None: fake_team_repo,
    )

    service = ProjectService(user_context=_ctx(), db_connection=None)

    project_detail = await service.get_project_details("project-1")

    assert project_detail.id == "project-1"
    assert project_detail.project_title == "Test Project"
    assert "get_project_with_client" in fake_project_repo.calls


# Update Project Tests
def test_build_project_update_dict_only_provided_fields(monkeypatch):
    """_build_project_update_dict only includes non-None fields."""
    service, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        project_title="Updated Title",
        status=ProjectStatus.ON_HOLD,
        priority=ProjectPriority.LOW,
    )

    update_dict = service._build_project_update_dict(request, "user-1")

    assert update_dict["updated_by"] == "user-1"
    assert update_dict["project_title"] == "Updated Title"
    assert update_dict["status"] == ProjectStatus.ON_HOLD.value
    assert update_dict["priority"] == ProjectPriority.LOW.value
    assert "project_description" not in update_dict


def test_build_project_update_dict_billing_info(monkeypatch):
    """_build_project_update_dict includes billing_info when provided."""
    service, *_ = _service_with_fakes(monkeypatch)
    billing_info = BillingInfo(
        billing_type=BillingType.FIXED_PRICE,
        hourly_rate=Decimal("200.00"),
        currency="USD",
    )
    request = UpdateProjectRequest(billing_info=billing_info)

    update_dict = service._build_project_update_dict(request, "user-1")

    assert "billing_info" in update_dict
    assert update_dict["billing_info"]["billing_type"] == BillingType.FIXED_PRICE.value
    assert update_dict["billing_info"]["hourly_rate"] == 200.0


def test_build_project_update_dict_tech_stack(monkeypatch):
    """_build_project_update_dict includes tech_stack when provided."""
    service, *_ = _service_with_fakes(monkeypatch)
    tech_stack = TechStack(frontend=["Vue"], backend=["Django"])
    request = UpdateProjectRequest(tech_stack=tech_stack)

    update_dict = service._build_project_update_dict(request, "user-1")

    assert "tech_stack" in update_dict
    assert update_dict["tech_stack"]["frontend"] == ["Vue"]
    assert update_dict["tech_stack"]["backend"] == ["Django"]


@pytest.mark.asyncio
async def test_ensure_project_team_creates_when_no_team_add(monkeypatch):
    """_ensure_project_team creates team when project has no team and add is requested."""
    service, _, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    fake_team_repo.team_id_result = "new-team-1"
    project = {"id": "project-1", "project_title": "Test Project"}
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            add=TeamMemberInput(
                member_id="member-1",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    team_id, new_team_id = await service._ensure_project_team(project, request)

    assert team_id == "new-team-1"
    assert new_team_id == "new-team-1"
    assert "create_team" in fake_team_repo.calls
    assert "validate_organization_members" in fake_team_repo.calls


@pytest.mark.asyncio
async def test_ensure_project_team_returns_existing_team_id(monkeypatch):
    """_ensure_project_team returns existing team_id when project has team."""
    service, *_ = _service_with_fakes(monkeypatch)
    project = {"id": "project-1", "team_id": "existing-team-1"}
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            add=TeamMemberInput(
                member_id="member-1",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    team_id, new_team_id = await service._ensure_project_team(project, request)

    assert team_id == "existing-team-1"
    assert new_team_id is None


@pytest.mark.asyncio
async def test_ensure_team_raises_when_member_not_found(monkeypatch):
    """_ensure_project_team raises NotFoundException when member not found."""
    service, _, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    fake_team_repo.members_valid = False
    project = {"id": "project-1", "project_title": "Test Project"}
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            add=TeamMemberInput(
                member_id="invalid-member",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service._ensure_project_team(project, request)

    assert exc_info.value.message_key == "projects.errors.team_member_not_found"


@pytest.mark.asyncio
async def test_apply_team_members_changes_removes_member(monkeypatch):
    """_apply_team_members_changes removes member when remove is requested."""
    service, _, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(team_members=TeamMembersUpdate(remove="member-1"))

    await service._apply_team_members_changes("team-1", request, skip_add=False)

    assert "delete_team_members_by_user_ids" in fake_team_repo.calls
    call = fake_team_repo.calls["delete_team_members_by_user_ids"]
    assert call[0] == "team-1"
    assert call[1] == ["member-1"]


@pytest.mark.asyncio
async def test_apply_team_members_changes_updates_member(monkeypatch):
    """_apply_team_members_changes updates member when update is requested."""
    service, _, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)

    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            update=TeamMemberUpdateItem(
                id="member-1",
                role="Senior Developer",
                allocation_percentage=80,
                hourly_rate=Decimal("180.00"),
            )
        )
    )

    await service._apply_team_members_changes("team-1", request, skip_add=False)

    assert "update_team_members_additional_data" in fake_team_repo.calls
    call = fake_team_repo.calls["update_team_members_additional_data"]
    assert call[0] == "team-1"
    assert call[2][0]["user_id"] == "member-1"
    assert call[2][0]["role"] == "Senior Developer"


@pytest.mark.asyncio
async def test_apply_team_members_changes_adds_member(monkeypatch):
    """_apply_team_members_changes adds member when add is requested."""
    service, _, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            add=TeamMemberInput(
                member_id="member-2",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    await service._apply_team_members_changes("team-1", request, skip_add=False)

    assert "_insert_team_members" in fake_team_repo.calls
    call = fake_team_repo.calls["_insert_team_members"]
    assert call[0] == "team-1"
    assert call[1][0].member_id == "member-2"


@pytest.mark.asyncio
async def test_apply_repositories_changes_removes_repository(monkeypatch):
    """_apply_repositories_changes removes repository when remove is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(repositories=RepositoriesUpdate(remove="repo-1"))

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "delete_project_repositories_by_ids" in fake_project_repo.calls
    call = fake_project_repo.calls["delete_project_repositories_by_ids"]
    assert call[0] == "project-1"
    assert call[1] == "org-1"
    assert call[2] == ["repo-1"]


@pytest.mark.asyncio
async def test_apply_repositories_changes_updates_repository(monkeypatch):
    """_apply_repositories_changes updates repository when update is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            update=RepositoryUpdateItem(
                id="repo-1",
                repository_name="updated-repo",
                is_primary=False,
            )
        )
    )

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "update_project_repository" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project_repository"]
    assert call[0] == "project-1"
    assert call[1] == "org-1"
    assert call[2] == "repo-1"
    assert call[3]["repository_name"] == "updated-repo"


@pytest.mark.asyncio
async def test_apply_repositories_changes_adds_repository(monkeypatch):
    """_apply_repositories_changes adds repository when add is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = []
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            add=RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_name="new-repo",
                repository_url="https://github.com/org/new-repo",
                is_primary=True,
            )
        )
    )

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "get_project_repositories" in fake_project_repo.calls
    assert fake_project_repo.calls["get_project_repositories"][2] is True  # primary_only=True
    assert "create_project_repositories" in fake_project_repo.calls
    call = fake_project_repo.calls["create_project_repositories"]
    assert call["project_id"] == "project-1"
    assert call["repositories"][0]["repository_name"] == "new-repo"


@pytest.mark.asyncio
async def test_apply_integrations_changes_removes(monkeypatch):
    """_apply_integrations_changes removes integration when remove is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(integrations=IntegrationsUpdate(remove="integration-1"))

    await service._apply_integrations_changes("project-1", "org-1", "user-1", request)

    assert "delete_project_integrations_by_ids" in fake_project_repo.calls
    call = fake_project_repo.calls["delete_project_integrations_by_ids"]
    assert call[0] == "project-1"
    assert call[1] == "org-1"
    assert call[2] == ["integration-1"]


@pytest.mark.asyncio
async def test_apply_integrations_changes_updates(monkeypatch):
    """_apply_integrations_changes updates integration when update is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)

    request = UpdateProjectRequest(
        integrations=IntegrationsUpdate(
            update=IntegrationUpdateItem(
                id="integration-1",
                integration_name="Updated Integration",
                sync_enabled=False,
            )
        )
    )

    await service._apply_integrations_changes("project-1", "org-1", "user-1", request)

    assert "update_project_integration" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project_integration"]
    assert call[0] == "project-1"
    assert call[2] == "integration-1"
    assert call[3]["integration_name"] == "Updated Integration"


@pytest.mark.asyncio
async def test_apply_integrations_changes_adds_integration(monkeypatch):
    """_apply_integrations_changes adds integration when add is requested."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        integrations=IntegrationsUpdate(
            add=IntegrationInput(
                integration_type=IntegrationType.LINEAR,
                integration_name="New Integration",
                sync_enabled=True,
            )
        )
    )

    await service._apply_integrations_changes("project-1", "org-1", "user-1", request)

    assert "create_project_integrations" in fake_project_repo.calls
    call = fake_project_repo.calls["create_project_integrations"]
    assert call["project_id"] == "project-1"
    assert call["integrations"][0]["integration_name"] == "New Integration"


@pytest.mark.asyncio
async def test_update_project_raises_not_found_missing(monkeypatch):
    """update_project raises NotFoundException
    when project not found."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = None
    request = UpdateProjectRequest(project_title="Updated Title")

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_project("project-123", request)

    assert exc_info.value.message_key == "projects.errors.project_not_found"


@pytest.mark.asyncio
async def test_update_project_raises_bad_request_no_team(monkeypatch):
    """update_project raises BadRequestException when
    updating team member without team."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Test Project",
        "team_id": None,
    }
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            update=TeamMemberUpdateItem(
                id="member-1",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    with pytest.raises(BadRequestException) as exc_info:
        await service.update_project("project-1", request)

    assert exc_info.value.message_key == "projects.errors.project_has_no_team"


@pytest.mark.asyncio
async def test_update_project_success_with_scalar_fields(monkeypatch):
    """update_project successfully updates scalar fields."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Original Title",
        "project_description": "Original Description",
        "status": "active",
        "priority": "high",
        "team_id": None,
    }
    request = UpdateProjectRequest(
        project_title="Updated Title",
        project_description="Updated Description",
        status=ProjectStatus.ON_HOLD,
    )

    result = await service.update_project("project-1", request)

    assert result is not None
    assert "old_data" in result
    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[0] == "project-1"
    assert call[2]["project_title"] == "Updated Title"
    assert call[2]["status"] == ProjectStatus.ON_HOLD.value


@pytest.mark.asyncio
async def test_update_project_success_with_team_member_add(monkeypatch):
    """update_project successfully adds team member."""
    service, fake_project_repo, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    fake_team_repo.team_id_result = "new-team-1"
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Test Project",
        "team_id": None,
    }
    request = UpdateProjectRequest(
        team_members=TeamMembersUpdate(
            add=TeamMemberInput(
                member_id="member-1",
                role="Developer",
                allocation_percentage=100,
            )
        )
    )

    result = await service.update_project("project-1", request)

    assert result is not None
    assert "create_team" in fake_team_repo.calls
    assert "update_project" in fake_project_repo.calls
    # Verify team_id is set in project update
    call = fake_project_repo.calls["update_project"]
    assert call[2]["team_id"] == "new-team-1"


@pytest.mark.asyncio
async def test_update_project_success_with_repo_update(monkeypatch):
    """update_project successfully updates repository."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Test Project",
        "team_id": None,
    }
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            update=RepositoryUpdateItem(
                id="repo-1",
                repository_name="updated-repo-name",
            )
        )
    )

    result = await service.update_project("project-1", request)

    assert result is not None
    assert "update_project_repository" in fake_project_repo.calls


@pytest.mark.asyncio
async def test_update_project_success_with_integration_add(monkeypatch):
    """update_project successfully adds integration."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Test Project",
        "team_id": None,
    }
    request = UpdateProjectRequest(
        integrations=IntegrationsUpdate(
            add=IntegrationInput(
                integration_type=IntegrationType.JIRA,
                integration_name="Jira Integration",
            )
        )
    )

    result = await service.update_project("project-1", request)

    assert result is not None
    assert "create_project_integrations" in fake_project_repo.calls


@pytest.mark.asyncio
async def test_update_project_returns_old_data_for_audit(monkeypatch):
    """update_project returns old_data formatted for audit logging."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_with_client_result = {
        "id": "project-1",
        "project_title": "Original Title",
        "project_description": "Original Description",
        "status": "active",
        "priority": "high",
        "team_id": None,
        "billing_info": None,
        "tech_stack": None,
        "project_category": [],
        "practice_areas": [],
        "start_date": None,
        "target_end_date": None,
        "project_goals": None,
        "success_criteria": None,
        "additional_ai_context": None,
        "tags": [],
        "custom_fields": None,
        "is_billable": True,
        "is_internal": False,
    }
    request = UpdateProjectRequest(project_title="Updated Title")

    result = await service.update_project("project-1", request)

    assert result is not None
    assert "old_data" in result
    assert result["old_data"]["project_title"] == "Original Title"
    assert result["old_data"]["status"] == "active"


@pytest.mark.asyncio
async def test_ensure_single_primary_repo_clears_existing(monkeypatch):
    """_ensure_single_primary_repository clears existing primary when different from exclude_id."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = [{"id": "repo-2", "is_primary": True}]

    await service._ensure_single_primary_repository("project-1", "org-1", exclude_id="repo-1")

    assert "get_project_repositories" in fake_project_repo.calls
    assert fake_project_repo.calls["get_project_repositories"][2] is True
    assert "update_project_repository" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project_repository"]
    assert call[2] == "repo-2"
    assert call[3]["is_primary"] is False


@pytest.mark.asyncio
async def test_ensure_single_primary_repo_skips_same_id(monkeypatch):
    """_ensure_single_primary_repository skips update when exclude_id matches."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = [{"id": "repo-1", "is_primary": True}]

    await service._ensure_single_primary_repository("project-1", "org-1", exclude_id="repo-1")

    assert "get_project_repositories" in fake_project_repo.calls
    assert "update_project_repository" not in fake_project_repo.calls


@pytest.mark.asyncio
async def test_apply_repos_changes_updates_primary_flag(monkeypatch):
    """_apply_repositories_changes clears existing primary when setting new primary."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = [{"id": "repo-2", "is_primary": True}]
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            update=RepositoryUpdateItem(
                id="repo-1",
                repository_name="updated-repo",
                is_primary=True,
            )
        )
    )

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "get_project_repositories" in fake_project_repo.calls
    assert "update_project_repository" in fake_project_repo.calls
    calls = [c for k, c in fake_project_repo.calls.items() if k == "update_project_repository"]
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_apply_repo_changes_skips_when_no_request(monkeypatch):
    """_apply_repositories_changes returns early when repositories is None."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(repositories=None)

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "delete_project_repositories_by_ids" not in fake_project_repo.calls
    assert "update_project_repository" not in fake_project_repo.calls
    assert "create_project_repositories" not in fake_project_repo.calls


@pytest.mark.asyncio
async def test_apply_project_row_sets_primary_from_add(monkeypatch):
    """_apply_project_row_update sets primary_repo_url from add request."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            add=RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_name="new-repo",
                repository_url="https://github.com/org/new-repo",
                is_primary=True,
            )
        )
    )

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["primary_repo_url"] == "https://github.com/org/new-repo"


@pytest.mark.asyncio
async def test_apply_project_row_sets_primary_from_update(monkeypatch):
    """_apply_project_row_update sets primary_repo_url from update request."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            update=RepositoryUpdateItem(
                id="repo-1",
                repository_url="https://github.com/org/updated-repo",
                is_primary=True,
            )
        )
    )

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["primary_repo_url"] == "https://github.com/org/updated-repo"


@pytest.mark.asyncio
async def test_apply_project_row_fetches_primary_not_in_req(monkeypatch):
    """_apply_project_row_update fetches primary repo when not in request."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = [
        {"id": "repo-1", "repository_url": "https://github.com/org/existing", "is_primary": True}
    ]
    request = UpdateProjectRequest(repositories=RepositoriesUpdate(remove="repo-2"))

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    assert "get_project_repositories" in fake_project_repo.calls
    assert fake_project_repo.calls["get_project_repositories"][2] is True
    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["primary_repo_url"] == "https://github.com/org/existing"


@pytest.mark.asyncio
async def test_apply_project_row_handles_no_primary_repo(monkeypatch):
    """_apply_project_row_update handles case when no primary repo exists."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = []
    request = UpdateProjectRequest(repositories=RepositoriesUpdate(remove="repo-1"))

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["primary_repo_url"] is None


@pytest.mark.asyncio
async def test_apply_project_row_skips_no_repos_request(monkeypatch):
    """_apply_project_row_update skips primary_repo_url when repositories not in request."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(project_title="Updated Title")

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert "primary_repo_url" not in call[2]


@pytest.mark.asyncio
async def test_apply_project_row_update_sets_team_id(monkeypatch):
    """_apply_project_row_update sets team_id when new_team_id provided."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(project_title="Updated Title")

    await service._apply_project_row_update("project-1", "org-1", {}, request, "new-team-1")

    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["team_id"] == "new-team-1"


@pytest.mark.asyncio
async def test_apply_integrations_changes_skips_when_no_req(monkeypatch):
    """_apply_integrations_changes returns early when integrations is None."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest(integrations=None)

    await service._apply_integrations_changes("project-1", "org-1", "user-1", request)

    assert "delete_project_integrations_by_ids" not in fake_project_repo.calls
    assert "update_project_integration" not in fake_project_repo.calls
    assert "create_project_integrations" not in fake_project_repo.calls


@pytest.mark.asyncio
async def test_build_billing_info_handles_none():
    """_build_billing_info returns None for None input."""
    result = ProjectService._build_billing_info(None)
    assert result is None


@pytest.mark.asyncio
async def test_build_billing_info_handles_non_dict():
    """_build_billing_info returns None for non-dict data."""
    result = ProjectService._build_billing_info(json.dumps([1, 2, 3]))
    assert result is None


def test_prepare_billing_info_dict_without_hourly_rate(monkeypatch):
    """_prepare_billing_info_dict handles billing_info without hourly_rate."""
    service, *_ = _service_with_fakes(monkeypatch)
    billing_info = BillingInfo(
        billing_type=BillingType.FIXED_PRICE,
        hourly_rate=None,
        currency="USD",
    )

    billing_dict = service._prepare_billing_info_dict(billing_info)

    assert "hourly_rate" not in billing_dict
    assert billing_dict["billing_type"] == BillingType.FIXED_PRICE.value
    assert billing_dict["currency"] == "USD"


def test_prepare_billing_info_dict_without_currency(monkeypatch):
    """_prepare_billing_info_dict handles billing_info without currency."""
    service, *_ = _service_with_fakes(monkeypatch)
    billing_info = BillingInfo(
        billing_type=BillingType.FIXED_PRICE,
        hourly_rate=Decimal("150.00"),
        currency=None,
    )

    billing_dict = service._prepare_billing_info_dict(billing_info)

    assert "currency" not in billing_dict
    assert billing_dict["hourly_rate"] == 150.0


def test_apply_project_flags_with_none_values(monkeypatch):
    """_apply_project_flags skips fields when is_billable or is_internal is not provided."""
    service, *_ = _service_with_fakes(monkeypatch)
    project_data = {}
    # Create request without is_billable and is_internal (they default to None in the method)
    request = CreateProjectRequest(
        project_title="Test",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
    )
    # Manually set to None to test the None check logic
    request.is_billable = None
    request.is_internal = None

    service._apply_project_flags(project_data, request)

    assert "is_billable" not in project_data
    assert "is_internal" not in project_data


def test_primary_relationships_without_primary_repo(monkeypatch):
    """_apply_primary_relationships skips when no primary repository."""
    service, *_ = _service_with_fakes(monkeypatch)
    project_data = {}
    request = CreateProjectRequest(
        project_title="Test",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        repositories=[
            RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_name="repo",
                repository_url="https://github.com/org/repo",
                is_primary=False,
            )
        ],
    )

    service._apply_primary_relationships(project_data, request)

    assert "primary_repo_url" not in project_data


@pytest.mark.asyncio
async def test_create_project_integrations_optional_missing(monkeypatch):
    """_create_project_integrations handles integration with missing optional fields."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = CreateProjectRequest(
        project_title="Integration Project",
        client_id="client-1",
        status=ProjectStatus.ACTIVE,
        integrations=[
            IntegrationInput(
                integration_type=IntegrationType.LINEAR,
                integration_name=None,
                external_project_id=None,
                external_project_key=None,
                external_workspace_id=None,
                external_board_id=None,
                integration_purpose=None,
                integration_config=None,
                sync_enabled=True,
            )
        ],
    )

    await service._create_project_integrations("project-1", request)

    call = fake_project_repo.calls["create_project_integrations"]
    payload = call["integrations"][0]
    assert payload["integration_type"] == IntegrationType.LINEAR.value
    assert "integration_name" not in payload
    assert "external_project_id" not in payload
    assert "external_project_key" not in payload
    assert "external_workspace_id" not in payload
    assert "external_board_id" not in payload
    assert "integration_purpose" not in payload
    assert "integration_config" not in payload


@pytest.mark.asyncio
async def test_apply_repos_changes_adds_non_primary(monkeypatch):
    """_apply_repositories_changes adds repository without primary flag."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.repositories_result = []
    request = UpdateProjectRequest(
        repositories=RepositoriesUpdate(
            add=RepositoryInput(
                platform=RepositoryPlatform.GITHUB,
                repository_name="new-repo",
                repository_url="https://github.com/org/new-repo",
                is_primary=False,
            )
        )
    )

    await service._apply_repositories_changes("project-1", "org-1", "user-1", request)

    assert "get_project_repositories" not in fake_project_repo.calls
    assert "create_project_repositories" in fake_project_repo.calls


@pytest.mark.asyncio
async def test_apply_project_row_update_with_only_updated_by(monkeypatch):
    """_apply_project_row_update includes updated_by even when no other fields."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    request = UpdateProjectRequest()

    await service._apply_project_row_update("project-1", "org-1", {}, request, None)

    # updated_by is always included, so update_project is called
    assert "update_project" in fake_project_repo.calls
    call = fake_project_repo.calls["update_project"]
    assert call[2]["updated_by"] == service.user_context.user_id
    assert len(call[2]) == 1  # Only updated_by


# Delete Project Tests
@pytest.mark.asyncio
async def test_delete_project_raises_not_found(monkeypatch):
    """delete_project raises NotFoundException when project not found."""
    service, fake_project_repo, *_ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_basic_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_project("project-123")

    assert exc_info.value.message_key == "projects.errors.project_not_found"
    assert "get_project_basic_information" in fake_project_repo.calls


@pytest.mark.asyncio
async def test_delete_project_success_with_team(monkeypatch):
    """delete_project hard deletes team, repos, integrations; soft deletes project."""
    service, fake_project_repo, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_basic_result = {
        "id": "project-uuid-1",
        "team_id": "team-uuid-1",
    }

    await service.delete_project("project-123")

    assert "get_project_basic_information" in fake_project_repo.calls
    assert fake_project_repo.calls["get_project_basic_information"][0] == "project-123"
    assert "delete_team_and_members" in fake_team_repo.calls
    team_delete = fake_team_repo.calls["delete_team_and_members"]
    assert team_delete.team_id == "team-uuid-1"
    assert team_delete.organization_id == service.user_context.organization_id
    assert "delete_all_project_repositories" in fake_project_repo.calls
    call = fake_project_repo.calls["delete_all_project_repositories"]
    assert call[0] == "project-uuid-1"
    assert call[1] == service.user_context.organization_id
    assert "delete_all_project_integrations" in fake_project_repo.calls
    call = fake_project_repo.calls["delete_all_project_integrations"]
    assert call[0] == "project-uuid-1"
    assert "soft_delete_project" in fake_project_repo.calls
    call = fake_project_repo.calls["soft_delete_project"]
    assert call[0] == "project-uuid-1"
    assert call[2] == service.user_context.user_id


@pytest.mark.asyncio
async def test_delete_project_success_without_team(monkeypatch):
    """delete_project skips team delete when project has no team."""
    service, fake_project_repo, _, fake_team_repo, _ = _service_with_fakes(monkeypatch)
    fake_project_repo.project_basic_result = {
        "id": "project-uuid-2",
        "team_id": None,
    }

    await service.delete_project("project-456")

    assert "get_project_basic_information" in fake_project_repo.calls
    assert "delete_team_and_members" not in fake_team_repo.calls
    assert "delete_all_project_repositories" in fake_project_repo.calls
    assert "delete_all_project_integrations" in fake_project_repo.calls
    assert "soft_delete_project" in fake_project_repo.calls
