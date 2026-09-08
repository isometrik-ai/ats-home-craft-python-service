"""Request/response schemas for per-project role templates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.admin_access_management import PermissionItem


class ProjectRoleItem(BaseModel):
    """Project role summary."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    slug: str
    name: str
    description: str | None = None
    is_system: bool = True
    permission_count: int = 0
    member_count: int = 0


class ProjectRoleDetailItem(ProjectRoleItem):
    """Project role with assigned permissions."""

    permission_ids: list[str] = Field(default_factory=list)
    permissions: list[PermissionItem] = Field(default_factory=list)


class UpdateProjectRoleRequest(BaseModel):
    """Update project role metadata and permissions."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    permission_ids: list[str] | None = None


class CreateProjectRoleRequest(BaseModel):
    """Create a custom project role template."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        description="Optional snake_case slug; generated from name when omitted.",
    )
    description: str | None = Field(default=None, max_length=500)
    permission_ids: list[str] = Field(default_factory=list)


class ProjectMyPermissionsResponse(BaseModel):
    """Effective permissions for the current user on a project."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    project_role_id: str | None = None
    role_slug: str | None = None
    role_name: str | None = None
    is_org_wide: bool = False
    project_permissions: list[str] = Field(default_factory=list)
    effective_permissions: list[str] = Field(default_factory=list)


class ProjectRoleListApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/roles."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ProjectRoleItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProjectRoleDetailApiResponse(BaseModel):
    """API envelope for project role detail, create, and update."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ProjectRoleDetailItem


class ProjectRoleDeletedApiResponse(BaseModel):
    """API envelope for DELETE /projects/{project_id}/roles/{project_role_id}."""

    status: str
    message: str
    statusCode: int
    code: str


class ProjectAssignablePermissionListApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/roles/permissions."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[PermissionItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProjectMyPermissionsApiResponse(BaseModel):
    """API envelope for GET /projects/{project_id}/my-permissions."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ProjectMyPermissionsResponse
