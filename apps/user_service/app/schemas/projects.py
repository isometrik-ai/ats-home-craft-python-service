"""Projects Management Schemas Module

This module contains all Pydantic models and schemas related to project management.
These schemas are used for request/response validation and API documentation.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.clients import PrimaryContactInfo
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


class BudgetInfo(BaseModel):
    """Budget information model."""

    total: Decimal = Field(..., ge=0, description="Total budget amount")


class BillingInfo(BaseModel):
    """Billing information model."""

    billing_type: BillingType = Field(..., description="Billing type")
    hourly_rate: Decimal | None = Field(None, ge=0, le=99999.99, description="Hourly rate")
    currency: str | None = Field(
        None,
        min_length=3,
        max_length=3,
        description="Currency code (3-letter)",
    )
    payment_terms: PaymentTerms | None = Field(None, description="Payment terms")
    budget: BudgetInfo | None = Field(None, description="Budget information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "billing_type": "time_and_materials",
                "hourly_rate": 150.00,
                "currency": "USD",
                "payment_terms": "Net 30",
                "budget": {"total": 125000.00},
            }
        }
    )


class TechStack(BaseModel):
    """Technology stack model."""

    frontend: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Frontend technologies",
    )
    backend: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Backend technologies",
    )
    database: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Database technologies",
    )
    cloud: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Cloud technologies",
    )
    mobile: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Mobile technologies",
    )
    ai_ml: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="AI/ML technologies",
    )
    other: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Other technologies",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "frontend": ["React", "TypeScript", "TailwindCSS"],
                "backend": ["Node.js", "Express", "PostgreSQL"],
                "database": ["PostgreSQL", "Redis"],
                "cloud": ["AWS", "Vercel"],
                "mobile": [],
                "ai_ml": ["OpenAI API"],
                "other": ["Docker", "GitHub Actions"],
            }
        }
    )


class TeamMemberInput(BaseModel):
    """Team member input model for project creation."""

    member_id: str = Field(..., description="Organization member UUID")
    role: str = Field(..., min_length=1, max_length=100, description="Member role in project")
    allocation_percentage: int = Field(
        ...,
        ge=1,
        le=100,
        description="Allocation percentage (1-100)",
    )
    hourly_rate: Decimal | None = Field(
        None,
        ge=0,
        le=99999.99,
        description="Hourly rate (overrides project default)",
    )
    role_description: str | None = Field(
        None,
        max_length=500,
        description="Role description for AI context",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "member_id": "b1ffdc99-0d2c-5fg0-cc8e-8cc0df612b33",
                "role": "Project Lead",
                "allocation_percentage": 60,
                "hourly_rate": 150.00,
                "role_description": "Manages client relationships \
                and ensures deliverables meet expectations",
            }
        }
    )


class RepositoryInput(BaseModel):
    """Repository input model for project creation."""

    platform: RepositoryPlatform = Field(..., description="Repository platform")
    repository_owner: str | None = Field(
        None,
        max_length=100,
        description="Repository owner/organization",
    )
    repository_name: str = Field(..., max_length=100, description="Repository name")
    repository_url: str = Field(..., max_length=500, description="Full repository URL")
    purpose: str | None = Field(None, max_length=200, description="Repository purpose")
    primary_branch: str = Field(default="main", max_length=100, description="Primary branch name")
    is_private: bool = Field(default=True, description="Whether repository is private")
    is_primary: bool = Field(default=False, description="Whether repository is primary")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "platform": "github",
                "repository_owner": "agency-dev",
                "repository_name": "ecom-frontend",
                "repository_url": "https://github.com/agency-dev/ecom-frontend",
                "purpose": "Main frontend application",
                "primary_branch": "main",
                "is_private": True,
                "is_primary": True,
            }
        }
    )


class IntegrationInput(BaseModel):
    """Integration input model for project creation."""

    integration_type: IntegrationType = Field(..., description="Integration type")
    integration_name: str | None = Field(None, max_length=200, description="Integration name")
    external_project_id: str | None = Field(None, max_length=200, description="External project ID")
    external_project_key: str | None = Field(
        None,
        max_length=100,
        description="External project key",
    )
    external_workspace_id: str | None = Field(
        None,
        max_length=200,
        description="External workspace ID",
    )
    external_board_id: str | None = Field(None, max_length=200, description="External board ID")
    sync_enabled: bool = Field(default=True, description="Whether sync is enabled")
    sync_direction: SyncDirection = Field(
        default=SyncDirection.BIDIRECTIONAL,
        description="Sync direction",
    )
    auto_sync: bool = Field(default=True, description="Whether auto-sync is enabled")
    sync_interval_minutes: int = Field(
        default=15,
        ge=5,
        le=1440,
        description="Sync interval in minutes",
    )
    integration_purpose: str | None = Field(None, max_length=500, description="Integration purpose")
    integration_config: dict[str, Any] | None = Field(
        None,
        description="Integration-specific configuration",
    )
    is_primary: bool = Field(default=False, description="Whether integration is primary PM tool")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "integration_type": "linear",
                "integration_name": "E-Commerce Redesign",
                "external_project_id": "PRJ_abc123",
                "external_workspace_id": "WSP_xyz789",
                "sync_enabled": True,
                "sync_direction": "bidirectional",
                "auto_sync": True,
                "sync_interval_minutes": 15,
                "integration_purpose": "Sprint planning and issue tracking",
            }
        }
    )


class CreateProjectRequest(BaseModel):
    """Request model for creating a new project."""

    project_title: str = Field(..., min_length=1, max_length=200, description="Project title")
    project_description: str | None = Field(
        None,
        max_length=2000,
        description="Project description",
    )
    client_id: str = Field(..., description="Client UUID")
    status: ProjectStatus = Field(..., description="Project status")
    priority: ProjectPriority = Field(
        default=ProjectPriority.MEDIUM,
        description="Project priority",
    )
    project_category: list[str] | None = Field(
        None, max_length=10, description="Project categories"
    )
    practice_areas: list[str] | None = Field(
        None,
        max_length=10,
        description="Practice areas",
    )
    start_date: date | None = Field(None, description="Project start date")
    target_end_date: date | None = Field(None, description="Target end date")
    billing_info: BillingInfo | None = Field(None, description="Billing information")
    tech_stack: TechStack | None = Field(None, description="Technology stack")
    project_goals: str | None = Field(None, max_length=2000, description="Project goals")
    success_criteria: str | None = Field(None, max_length=2000, description="Success criteria")
    additional_ai_context: str | None = Field(
        None,
        max_length=2000,
        description="Additional AI context",
    )
    tags: list[str] | None = Field(None, max_length=50, description="Project tags")
    custom_fields: dict[str, Any] | None = Field(None, description="Custom field key-value pairs")
    is_billable: bool = Field(default=True, description="Whether project is billable")
    is_internal: bool = Field(default=False, description="Whether project is internal")
    team_members: list[TeamMemberInput] = Field(
        default_factory=list,
        max_length=100,
        description="Team members (optional; team created only when provided)",
    )
    repositories: list[RepositoryInput] | None = Field(
        None,
        max_length=10,
        description="Repositories",
    )
    integrations: list[IntegrationInput] | None = Field(
        None,
        max_length=10,
        description="Integrations",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "CreateProjectRequest":
        """Validate that target_end_date is after start_date if both provided."""
        if self.start_date and self.target_end_date and self.target_end_date <= self.start_date:
            raise ValueError("Target end date must be after start date")
        return self

    @model_validator(mode="after")
    def validate_primary_repository(self) -> "CreateProjectRequest":
        """Validate that only one repository is marked as primary."""
        if self.repositories:
            primary_count = sum(1 for r in self.repositories if r.is_primary)
            if primary_count > 1:
                raise ValueError("Only one repository can be marked as primary")
        return self

    @model_validator(mode="after")
    def validate_primary_integration(self) -> "CreateProjectRequest":
        """Validate that only one integration is marked as primary."""
        if self.integrations:
            primary_count = sum(1 for i in self.integrations if i.is_primary)
            if primary_count > 1:
                raise ValueError("Only one integration can be marked as primary")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "project_title": "E-Commerce Platform Redesign",
                "project_description": "Complete redesign and rebuild",
                "client_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "status": "active",
                "priority": "high",
                "project_category": ["E-Commerce", "FinTech"],
                "practice_areas": ["Web Development", "E-Commerce"],
                "start_date": "2024-01-15",
                "target_end_date": "2024-06-30",
                "team_members": [
                    {
                        "member_id": "b1ffdc99-0d2c-5fg0-cc8e-8cc0df612b33",
                        "role": "Project Lead",
                        "allocation_percentage": 60,
                        "hourly_rate": 150.00,
                    }
                ],
            }
        }
    )


class ProjectListQueryParams(BaseModel):
    """Query parameters for project list endpoint."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Page size")
    search: str | None = Field(None, min_length=2, description="Search term")
    client_id: str | None = Field(None, description="Filter by client ID")
    status: ProjectStatus | None = Field(None, description="Filter by status")
    priority: ProjectPriority | None = Field(None, description="Filter by priority")
    tags: str | None = Field(None, description="Comma-separated tags")


class ClientInfo(BaseModel):
    """Client information in project response."""

    id: str = Field(..., description="Client UUID")
    name: str = Field(..., description="Client name")
    type: str = Field(..., description="Client type")
    primary_contact: PrimaryContactInfo | None = Field(
        None, description="Primary contact information"
    )


class ProjectLeadInfo(BaseModel):
    """Project lead information."""

    id: str = Field(..., description="Member UUID")
    full_name: str = Field(..., description="Full name")


class ProjectListItem(BaseModel):
    """Project item in list response."""

    id: str = Field(..., description="Project UUID")
    project_id: str = Field(..., description="Human-readable project ID")
    project_title: str = Field(..., description="Project title")
    client: ClientInfo = Field(..., description="Client information")
    project_lead: ProjectLeadInfo | None = Field(None, description="Project lead information")
    team_size: int = Field(..., description="Team size")
    status: ProjectStatus = Field(..., description="Project status")
    priority: ProjectPriority = Field(..., description="Project priority")
    category: str | None = Field(None, description="First category")
    practice_areas: list[str] = Field(default_factory=list, description="Practice areas")
    start_date: date | None = Field(None, description="Start date")
    tags: list[str] = Field(default_factory=list, description="Tags")
    tech_stack: TechStack = Field(..., description="Technology stack")


class TeamMemberInfo(BaseModel):
    """Team member information in project detail."""

    id: str = Field(..., description="Member UUID")
    full_name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: str = Field(..., description="Role")
    allocation_percentage: int = Field(..., description="Allocation percentage")
    hourly_rate: str = Field(..., description="Hourly rate as string")
    role_description: str | None = Field(None, description="Role description")


class ProjectLeadDetail(BaseModel):
    """Project lead detail information."""

    id: str = Field(..., description="Member UUID")
    full_name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: str = Field(..., description="Role")
    allocation_percentage: int = Field(..., description="Allocation percentage")
    hourly_rate: str = Field(..., description="Hourly rate as string")
    role_description: str | None = Field(None, description="Role description")


class TechLeadDetail(BaseModel):
    """Tech lead detail information."""

    id: str = Field(..., description="Member UUID")
    full_name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: str = Field(..., description="Role")
    allocation_percentage: int = Field(..., description="Allocation percentage")
    hourly_rate: str = Field(..., description="Hourly rate as string")
    role_description: str | None = Field(None, description="Role description")


class TeamInfo(BaseModel):
    """Team information in project detail."""

    id: str = Field(..., description="Team UUID")
    name: str = Field(..., description="Team name")
    project_lead: ProjectLeadDetail | None = Field(None, description="Project lead")
    tech_lead: TechLeadDetail | None = Field(None, description="Tech lead")
    members: list[TeamMemberInfo] = Field(default_factory=list, description="Team members")


class BillingInfoDetail(BaseModel):
    """Billing information detail."""

    billing_type: BillingType = Field(..., description="Billing type")
    hourly_rate: Decimal | None = Field(None, description="Hourly rate")
    currency: str | None = Field(None, description="Currency code")
    billing_cycle: str | None = Field(None, description="Billing cycle")
    billing_contact_id: str | None = Field(None, description="Billing contact ID")
    payment_terms: PaymentTerms | None = Field(None, description="Payment terms")
    retainer_amount: Decimal | None = Field(None, description="Retainer amount")
    budget: dict[str, Any] | None = Field(None, description="Budget information")


class RepositoryInfo(BaseModel):
    """Repository information in project detail."""

    id: str = Field(..., description="Repository UUID")
    platform: RepositoryPlatform = Field(..., description="Platform")
    external_repository_id: str | None = Field(None, description="External repository ID")
    repository_owner: str | None = Field(None, description="Repository owner")
    repository_name: str = Field(..., description="Repository name")
    repository_url: str = Field(..., description="Repository URL")
    purpose: str | None = Field(None, description="Purpose")
    primary_branch: str = Field(..., description="Primary branch")
    is_private: bool = Field(..., description="Is private")
    is_primary: bool = Field(..., description="Is primary")
    is_connected: bool = Field(..., description="Is connected")
    connection_status: ConnectionStatus | None = Field(None, description="Connection status")
    webhook_url: str | None = Field(None, description="Webhook URL")
    webhook_secret: str | None = Field(None, description="Webhook secret")
    webhook_events: list[str] | None = Field(None, description="Webhook events")
    last_synced_at: str | None = Field(None, description="Last synced timestamp")
    total_commits: int = Field(default=0, description="Total commits")
    total_branches: int = Field(default=0, description="Total branches")
    total_contributors: int = Field(default=0, description="Total contributors")
    description: str | None = Field(None, description="Description")
    created_at: str = Field(..., description="Created timestamp")
    updated_at: str = Field(..., description="Updated timestamp")


class IntegrationInfo(BaseModel):
    """Integration information in project detail."""

    id: str = Field(..., description="Integration UUID")
    integration_type: IntegrationType = Field(..., description="Integration type")
    integration_name: str | None = Field(None, description="Integration name")
    is_connected: bool = Field(..., description="Is connected")
    connection_status: ConnectionStatus | None = Field(None, description="Connection status")
    external_project_id: str | None = Field(None, description="External project ID")
    external_project_key: str | None = Field(None, description="External project key")
    external_workspace_id: str | None = Field(None, description="External workspace ID")
    external_board_id: str | None = Field(None, description="External board ID")
    nango_connection_id: str | None = Field(None, description="Nango connection ID")
    webhook_url: str | None = Field(None, description="Webhook URL")
    webhook_events: list[str] | None = Field(None, description="Webhook events")
    outgoing_webhook_url: str | None = Field(None, description="Outgoing webhook URL")
    sync_enabled: bool = Field(..., description="Sync enabled")
    sync_direction: SyncDirection = Field(..., description="Sync direction")
    auto_sync: bool = Field(..., description="Auto sync")
    sync_interval_minutes: int = Field(..., description="Sync interval minutes")
    last_synced_at: str | None = Field(None, description="Last synced timestamp")
    last_sync_status: str | None = Field(None, description="Last sync status")
    last_sync_error: str | None = Field(None, description="Last sync error")
    next_sync_at: str | None = Field(None, description="Next sync timestamp")
    integration_purpose: str | None = Field(None, description="Integration purpose")
    created_at: str = Field(..., description="Created timestamp")
    updated_at: str = Field(..., description="Updated timestamp")


class ProjectDetailData(BaseModel):
    """Complete project detail data."""

    id: str = Field(..., description="Project UUID")
    organization_id: str = Field(..., description="Organization UUID")
    project_id: str = Field(..., description="Human-readable project ID")
    project_title: str = Field(..., description="Project title")
    project_description: str | None = Field(None, description="Project description")
    client: ClientInfo = Field(..., description="Client information")
    project_lead: ProjectLeadInfo | None = Field(None, description="Project lead information")
    status: ProjectStatus = Field(..., description="Project status")
    priority: ProjectPriority = Field(..., description="Project priority")
    project_category: list[str] = Field(default_factory=list, description="Project categories")
    practice_areas: list[str] = Field(default_factory=list, description="Practice areas")
    start_date: date | None = Field(None, description="Start date")
    target_end_date: date | None = Field(None, description="Target end date")
    actual_end_date: date | None = Field(None, description="Actual end date")
    billing_info: BillingInfoDetail | None = Field(None, description="Billing information")
    total_billed: str = Field(..., description="Total billed amount")
    total_hours: str = Field(..., description="Total hours")
    tech_stack: TechStack = Field(..., description="Technology stack")
    project_goals: str | None = Field(None, description="Project goals")
    success_criteria: str | None = Field(None, description="Success criteria")
    additional_ai_context: str | None = Field(None, description="Additional AI context")
    primary_pm_tool: str | None = Field(None, description="Primary PM tool")
    primary_repo_url: str | None = Field(None, description="Primary repository URL")
    tags: list[str] = Field(default_factory=list, description="Tags")
    custom_fields: dict[str, Any] = Field(default_factory=dict, description="Custom fields")
    is_billable: bool = Field(..., description="Is billable")
    is_internal: bool = Field(..., description="Is internal")
    team: TeamInfo | None = Field(None, description="Team information")
    repositories: list[RepositoryInfo] = Field(default_factory=list, description="Repositories")
    integrations: list[IntegrationInfo] = Field(default_factory=list, description="Integrations")
    created_at: str = Field(..., description="Created timestamp")
    updated_at: str = Field(..., description="Updated timestamp")
    created_by: str | None = Field(None, description="Created by user ID")
    updated_by: str | None = Field(None, description="Updated by user ID")


class ProjectListResponse(BaseModel):
    """Response model for project list."""

    data: list[ProjectListItem] = Field(..., description="List of projects")
    total: int = Field(..., description="Total count")
    page: int = Field(..., description="Current page")
    page_size: int = Field(..., description="Page size")
    total_pages: int = Field(..., description="Total pages")
