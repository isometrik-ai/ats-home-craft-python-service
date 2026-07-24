"""Unit tests for projects Pydantic schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import (
    BillingType,
    ConnectionStatus,
    IntegrationType,
    PaymentTerms,
    ProjectPriority,
    ProjectStatus,
    RepositoryPlatform,
    SyncDirection,
)
from apps.user_service.app.schemas.projects import (
    PROJECT_JSONB_COLUMNS,
    BillingInfo,
    BillingInfoDetail,
    BudgetInfo,
    CreateProjectRequest,
    DocumentsUpdate,
    IntegrationInfo,
    IntegrationInput,
    IntegrationsUpdate,
    IntegrationUpdateItem,
    ProjectCompaniesUpdate,
    ProjectCompanyAssociationUpdate,
    ProjectCompanyContactItem,
    ProjectCompanyCreate,
    ProjectCompanyListItem,
    ProjectDetailData,
    ProjectDocument,
    ProjectDocumentUpdateItem,
    ProjectLeadDetail,
    ProjectLeadInfo,
    ProjectListItem,
    ProjectListQueryParams,
    ProjectListResponse,
    RepositoriesUpdate,
    RepositoryInfo,
    RepositoryInput,
    RepositoryUpdateItem,
    TeamInfo,
    TeamMemberInfo,
    TeamMemberInput,
    TeamMembersUpdate,
    TeamMemberUpdateItem,
    TechLeadDetail,
    TechStack,
    UpdateProjectRequest,
)


def test_project_jsonb_columns_constant() -> None:
    """JSONB column names should match projects table fields."""
    assert PROJECT_JSONB_COLUMNS == frozenset(
        {"billing_info", "tech_stack", "custom_fields", "documents"}
    )


def test_budget_info_model_dump() -> None:
    """BudgetInfo should accept valid totals and dump cleanly."""
    model = BudgetInfo(total=Decimal("125000.00"))
    dumped = model.model_dump(mode="json")
    assert dumped["total"] == "125000.00"


def test_billing_info_model_dump() -> None:
    """BillingInfo should serialize nested budget data."""
    model = BillingInfo(
        billing_type=BillingType.TIME_AND_MATERIALS,
        hourly_rate=Decimal("150.00"),
        currency="USD",
        payment_terms=PaymentTerms.NET_30,
        budget=BudgetInfo(total=Decimal("50000")),
    )
    dumped = model.model_dump(mode="json")
    assert dumped["billing_type"] == "time_and_materials"
    assert dumped["currency"] == "USD"
    assert dumped["budget"]["total"] == "50000"


def test_tech_stack_defaults_and_dump() -> None:
    """TechStack should default empty lists and dump provided values."""
    model = TechStack(frontend=["React"], backend=["Python"])
    dumped = model.model_dump(mode="json")
    assert dumped["frontend"] == ["React"]
    assert dumped["mobile"] == []


def test_team_member_input_model_dump() -> None:
    """TeamMemberInput should validate allocation bounds."""
    model = TeamMemberInput(
        member_id="mem-1",
        role="Lead",
        allocation_percentage=60,
        hourly_rate=Decimal("120"),
    )
    assert model.model_dump(mode="json")["allocation_percentage"] == 60


def test_repository_input_model_dump() -> None:
    """RepositoryInput should capture platform and URL fields."""
    model = RepositoryInput(
        platform=RepositoryPlatform.GITHUB,
        repository_name="app",
        repository_url="https://github.com/org/app",
        is_primary=True,
    )
    dumped = model.model_dump(mode="json")
    assert dumped["platform"] == "github"
    assert dumped["is_primary"] is True


def test_integration_input_model_dump() -> None:
    """IntegrationInput should serialize sync configuration."""
    model = IntegrationInput(
        integration_type=IntegrationType.LINEAR,
        integration_name="Sprint Board",
        sync_direction=SyncDirection.BIDIRECTIONAL,
    )
    dumped = model.model_dump(mode="json")
    assert dumped["integration_type"] == "linear"
    assert dumped["sync_interval_minutes"] == 15


def test_team_member_update_item_partial() -> None:
    """TeamMemberUpdateItem allows partial updates by id."""
    model = TeamMemberUpdateItem(id="tm-1", role="Developer")
    assert model.model_dump(exclude_none=True) == {"id": "tm-1", "role": "Developer"}


def test_repository_update_item_partial() -> None:
    """RepositoryUpdateItem allows partial repository patches."""
    model = RepositoryUpdateItem(
        id="repo-1",
        connection_status=ConnectionStatus.CONNECTED,
    )
    dumped = model.model_dump(exclude_none=True, mode="json")
    assert dumped["connection_status"] == "connected"


def test_integration_update_item_partial() -> None:
    """IntegrationUpdateItem allows partial integration patches."""
    model = IntegrationUpdateItem(id="int-1", sync_enabled=False)
    assert model.model_dump(exclude_none=True)["sync_enabled"] is False


def test_project_document_model_dump() -> None:
    """ProjectDocument should require type, name, and url."""
    model = ProjectDocument(type="spec", name="Brief", url="https://example.com/brief.pdf")
    dumped = model.model_dump(mode="json")
    assert dumped["name"] == "Brief"


def test_project_document_update_item() -> None:
    """ProjectDocumentUpdateItem patches documents by id."""
    model = ProjectDocumentUpdateItem(id="doc-1", name="Updated Brief")
    assert model.model_dump(exclude_none=True) == {"id": "doc-1", "name": "Updated Brief"}


def test_documents_update_model_dump() -> None:
    """DocumentsUpdate supports add/update/remove operations."""
    model = DocumentsUpdate(
        add=[ProjectDocument(type="spec", name="Brief", url="https://x.com/a")],
        remove=["doc-old"],
    )
    dumped = model.model_dump(mode="json")
    assert len(dumped["add"]) == 1
    assert dumped["remove"] == ["doc-old"]


def test_team_members_update_add_branch() -> None:
    """TeamMembersUpdate add branch wraps TeamMemberInput."""
    model = TeamMembersUpdate(
        add=TeamMemberInput(member_id="m1", role="Dev", allocation_percentage=50),
    )
    assert model.add is not None
    assert model.add.member_id == "m1"


def test_repositories_update_remove_branch() -> None:
    """RepositoriesUpdate remove branch stores repository id."""
    model = RepositoriesUpdate(remove="repo-1")
    assert model.remove == "repo-1"


def test_integrations_update_add_branch() -> None:
    """IntegrationsUpdate add branch wraps IntegrationInput."""
    model = IntegrationsUpdate(
        add=IntegrationInput(integration_type=IntegrationType.JIRA),
    )
    assert model.add.integration_type == IntegrationType.JIRA


def test_project_company_create_normalizes_label() -> None:
    """ProjectCompanyCreate strips blank labels to None."""
    model = ProjectCompanyCreate(company_id="co-1", label="  partner  ")
    assert model.label == "partner"
    blank = ProjectCompanyCreate(company_id="co-2", label="   ")
    assert blank.label is None


def test_project_company_assoc_update_normalizes() -> None:
    """Association update normalizes company_id and label."""
    model = ProjectCompanyAssociationUpdate(
        company_id="  co-1  ",
        label=" sponsor ",
    )
    assert model.company_id == "co-1"
    assert model.label == "sponsor"


def test_project_companies_update_requires_operation() -> None:
    """ProjectCompaniesUpdate requires at least one delta."""
    with pytest.raises(ValidationError):
        ProjectCompaniesUpdate()


def test_project_companies_update_add_association() -> None:
    """ProjectCompaniesUpdate normalizes add association ids."""
    model = ProjectCompaniesUpdate(
        add_associations=[ProjectCompanyCreate(company_id="  co-1 ", label="client")],
    )
    dumped = model.model_dump(mode="json")
    assert dumped["add_associations"][0]["company_id"] == "co-1"


def test_create_project_request_valid_dump() -> None:
    """CreateProjectRequest accepts a minimal valid payload."""
    model = CreateProjectRequest(
        project_title="Platform Redesign",
        status=ProjectStatus.ACTIVE,
        start_date=date(2026, 1, 1),
        target_end_date=date(2026, 6, 30),
    )
    dumped = model.model_dump(mode="json")
    assert dumped["project_title"] == "Platform Redesign"
    assert dumped["priority"] == "medium"


def test_create_project_rejects_invalid_dates() -> None:
    """CreateProjectRequest rejects end date before start date."""
    with pytest.raises(ValidationError, match="Target end date"):
        CreateProjectRequest(
            project_title="Bad Dates",
            status=ProjectStatus.ACTIVE,
            start_date=date(2026, 6, 1),
            target_end_date=date(2026, 1, 1),
        )


def test_create_project_rejects_multi_primary_repo() -> None:
    """CreateProjectRequest allows only one primary repository."""
    repos = [
        RepositoryInput(
            platform=RepositoryPlatform.GITHUB,
            repository_name="a",
            repository_url="https://github.com/o/a",
            is_primary=True,
        ),
        RepositoryInput(
            platform=RepositoryPlatform.GITHUB,
            repository_name="b",
            repository_url="https://github.com/o/b",
            is_primary=True,
        ),
    ]
    with pytest.raises(ValidationError, match="Only one repository"):
        CreateProjectRequest(
            project_title="Multi Primary",
            status=ProjectStatus.ACTIVE,
            repositories=repos,
        )


def test_update_project_request_valid_dump() -> None:
    """UpdateProjectRequest accepts partial updates."""
    model = UpdateProjectRequest(
        project_title="Renamed",
        priority=ProjectPriority.HIGH,
    )
    dumped = model.model_dump(exclude_none=True, mode="json")
    assert dumped["project_title"] == "Renamed"
    assert dumped["priority"] == "high"


def test_update_project_rejects_invalid_dates() -> None:
    """UpdateProjectRequest validates date ordering when both provided."""
    with pytest.raises(ValidationError, match="Target end date"):
        UpdateProjectRequest(
            start_date=date(2026, 5, 1),
            target_end_date=date(2026, 1, 1),
        )


def test_project_list_query_params_defaults() -> None:
    """ProjectListQueryParams should expose pagination defaults."""
    params = ProjectListQueryParams()
    dumped = params.model_dump(mode="json")
    assert dumped["page"] == 1
    assert dumped["page_size"] == 20


def test_project_lead_info_model_dump() -> None:
    """ProjectLeadInfo stores id and display name."""
    model = ProjectLeadInfo(id="lead-1", full_name="Alex Lead")
    assert model.model_dump(mode="json")["full_name"] == "Alex Lead"


def test_project_list_item_model_dump() -> None:
    """ProjectListItem composes lead info and tech stack."""
    model = ProjectListItem(
        id="proj-1",
        project_id="PRJ-001",
        project_title="Alpha",
        team_size=3,
        status=ProjectStatus.ACTIVE,
        priority=ProjectPriority.MEDIUM,
        tech_stack=TechStack(),
    )
    dumped = model.model_dump(mode="json")
    assert dumped["project_id"] == "PRJ-001"
    assert dumped["tech_stack"]["frontend"] == []


def test_team_member_info_model_dump() -> None:
    """TeamMemberInfo serializes allocation and rate as strings."""
    model = TeamMemberInfo(
        id="m1",
        full_name="Sam Dev",
        email="sam@example.com",
        role="Developer",
        allocation_percentage=80,
        hourly_rate="150.00",
    )
    assert model.model_dump(mode="json")["hourly_rate"] == "150.00"


def test_project_lead_detail_model_dump() -> None:
    """ProjectLeadDetail mirrors team member detail fields."""
    model = ProjectLeadDetail(
        id="m1",
        full_name="Lead",
        email="lead@example.com",
        role="Project Lead",
        allocation_percentage=100,
        hourly_rate="200.00",
    )
    assert model.role == "Project Lead"


def test_tech_lead_detail_model_dump() -> None:
    """TechLeadDetail mirrors project lead detail fields."""
    model = TechLeadDetail(
        id="m2",
        full_name="Tech Lead",
        email="tech@example.com",
        role="Tech Lead",
        allocation_percentage=50,
        hourly_rate="175.00",
    )
    assert model.full_name == "Tech Lead"


def test_team_info_model_dump() -> None:
    """TeamInfo nests leads and member list."""
    member = TeamMemberInfo(
        id="m1",
        full_name="Dev",
        email="dev@example.com",
        role="Dev",
        allocation_percentage=100,
        hourly_rate="100.00",
    )
    model = TeamInfo(id="team-1", name="Core Team", members=[member])
    dumped = model.model_dump(mode="json")
    assert dumped["members"][0]["email"] == "dev@example.com"


def test_billing_info_detail_model_dump() -> None:
    """BillingInfoDetail includes extended billing metadata."""
    model = BillingInfoDetail(
        billing_type=BillingType.FIXED_PRICE,
        currency="EUR",
        budget={"total": 90000},
    )
    dumped = model.model_dump(mode="json")
    assert dumped["billing_type"] == "fixed_price"
    assert dumped["budget"]["total"] == 90000


def test_repository_info_model_dump() -> None:
    """RepositoryInfo captures connection and webhook metadata."""
    model = RepositoryInfo(
        id="repo-1",
        platform=RepositoryPlatform.GITLAB,
        repository_name="service",
        repository_url="https://gitlab.com/o/service",
        primary_branch="main",
        is_private=True,
        is_primary=True,
        is_connected=True,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    dumped = model.model_dump(mode="json")
    assert dumped["platform"] == "gitlab"
    assert dumped["is_connected"] is True


def test_project_company_contact_item_dump() -> None:
    """ProjectCompanyContactItem ignores extra fields."""
    model = ProjectCompanyContactItem(
        id="c1",
        first_name="Pat",
        last_name="Lee",
        email="pat@example.com",
    )
    assert model.model_dump(mode="json")["is_primary"] is False


def test_project_company_list_item_dump() -> None:
    """ProjectCompanyListItem links company metadata on projects."""
    model = ProjectCompanyListItem(
        company_id="co-1",
        name="Acme",
        company_name="Acme",
        label="client",
    )
    dumped = model.model_dump(mode="json")
    assert dumped["company_id"] == "co-1"
    assert dumped["label"] == "client"


def test_integration_info_model_dump() -> None:
    """IntegrationInfo serializes sync and webhook state."""
    model = IntegrationInfo(
        id="int-1",
        integration_type=IntegrationType.ASANA,
        is_connected=False,
        sync_enabled=True,
        sync_direction=SyncDirection.INBOUND,
        auto_sync=False,
        sync_interval_minutes=30,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    dumped = model.model_dump(mode="json")
    assert dumped["sync_direction"] == "inbound"


def test_project_detail_data_model_dump() -> None:
    """ProjectDetailData aggregates nested project detail sections."""
    model = ProjectDetailData(
        id="proj-1",
        organization_id="org-1",
        project_id="PRJ-001",
        project_title="Alpha",
        status=ProjectStatus.ACTIVE,
        priority=ProjectPriority.LOW,
        total_billed="0.00",
        total_hours="0.00",
        tech_stack=TechStack(),
        is_billable=True,
        is_internal=False,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    dumped = model.model_dump(mode="json")
    assert dumped["organization_id"] == "org-1"
    assert dumped["documents"] == []


def test_project_list_response_model_dump() -> None:
    """ProjectListResponse wraps paginated list payload."""
    item = ProjectListItem(
        id="proj-1",
        project_id="PRJ-001",
        project_title="Alpha",
        team_size=1,
        status=ProjectStatus.DISCOVERY,
        priority=ProjectPriority.MEDIUM,
        tech_stack=TechStack(),
    )
    model = ProjectListResponse(
        data=[item],
        total=1,
        page=1,
        page_size=20,
        total_pages=1,
    )
    dumped = model.model_dump(mode="json")
    assert dumped["total"] == 1
    assert dumped["data"][0]["project_title"] == "Alpha"
