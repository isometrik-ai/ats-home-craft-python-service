"""Request/response schemas for project member management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import ProjectMemberRole, ProjectMemberStatus


class AssignProjectMemberRequest(BaseModel):
    """Assign an organization member to a project."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., description="Auth user id of the org member to assign.")
    role: ProjectMemberRole = Field(default=ProjectMemberRole.COMMUNITY_ADMIN)


class UpdateProjectMemberRequest(BaseModel):
    """Update a project member role or status."""

    model_config = ConfigDict(extra="forbid")

    role: ProjectMemberRole | None = None
    status: ProjectMemberStatus | None = None


class ProjectMemberResponse(BaseModel):
    """Project member with org profile fields."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    user_id: str
    role: ProjectMemberRole | str
    status: ProjectMemberStatus | str
    joined_at: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    org_role_id: str | None = None
    member_role: str | None = None


class ListProjectMembersQuery(BaseModel):
    """Query params for listing project members."""

    model_config = ConfigDict(extra="forbid")

    role: ProjectMemberRole | None = Field(
        default=None,
        description="Filter by project member role (community_admin, security, etc.).",
    )
    status: ProjectMemberStatus | None = Field(
        default=None,
        description=("Filter by assignment status. When omitted, suspended members are excluded."),
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        description="Case-insensitive match on member email or name.",
    )
