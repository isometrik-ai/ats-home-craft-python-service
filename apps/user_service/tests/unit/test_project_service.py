"""Unit tests for ProjectService business logic."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

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
    ProjectListQueryParams,
    RepositoryInput,
    TechStack,
)
from apps.user_service.app.services.project_service import ProjectService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
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

    async def get_project_repositories(self, project_id, organization_id):
        """Get project repositories."""
        self.calls["get_project_repositories"] = (project_id, organization_id)
        return self.repositories_result

    async def get_project_integrations(self, project_id, organization_id):
        """Get project integrations."""
        self.calls["get_project_integrations"] = (project_id, organization_id)
        return self.integrations_result


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
    return service, fake_project_repo, fake_client_repo, fake_team_repo


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
    assert project_data["primary_pm_tool"] == IntegrationType.LINEAR.value
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
    service, fake_project_repo, _, fake_team_repo = _service_with_fakes(monkeypatch)
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
