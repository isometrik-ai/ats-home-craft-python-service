"""Request/response schemas for project member management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import ProjectMemberStatus


class AssignProjectMemberRequest(BaseModel):
    """Assign an organization member to a project."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., description="Auth user id of the org member to assign.")
    project_role_id: str = Field(..., description="Project role template id for this project.")


class UpdateProjectMemberRequest(BaseModel):
    """Update a project member role or status."""

    model_config = ConfigDict(extra="forbid")

    project_role_id: str | None = None
    status: ProjectMemberStatus | None = None


class ProjectMemberResponse(BaseModel):
    """Project member with org profile fields."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    user_id: str
    project_role_id: str
    role_slug: str
    role_name: str | None = None
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

    role_slug: str | None = Field(
        default=None,
        description="Filter by project role slug (community_admin, security, etc.).",
    )
    status: ProjectMemberStatus | None = Field(
        default=None,
        description="Filter by assignment status. When omitted, suspended members are excluded.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        description="Case-insensitive match on member email or name.",
    )


class ProjectMemberListApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/members."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ProjectMemberResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProjectMemberApiResponse(BaseModel):
    """API envelope for assign and update project member."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ProjectMemberResponse


class ProjectMemberRemovedApiResponse(BaseModel):
    """API envelope for DELETE /projects/{project_id}/members/{user_id}."""

    status: str
    message: str
    statusCode: int
    code: str
