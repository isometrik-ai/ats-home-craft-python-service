"""Schemas for superadmin organization APIs."""

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import (
    PlanType,
    SuperadminOrganizationListSortField,
    SuperadminOrganizationListSortOrder,
    SuperadminOrganizationListStatus,
)


class SuperadminOrgOwnerAdmin(BaseModel):
    """Owner admin summary for superadmin org list rows."""

    user_id: str | None = Field(None, description="Auth user id of the org owner")
    full_name: str | None = Field(None, description="Display name from member profile")
    email: str | None = Field(None, description="Owner email")


class SuperadminOrganizationListItem(BaseModel):
    """One row for GET /superadmin/organizations."""

    organization_id: str
    name: str
    admin: SuperadminOrgOwnerAdmin
    member_count: int = Field(ge=0)
    plan_type: str = Field(
        ...,
        description="Raw subscription.plan_type from organizations.subscription JSON",
    )
    status: SuperadminOrganizationListStatus
    created_at: str | None = Field(None, description="ISO timestamp when organization was created")

    model_config = ConfigDict(from_attributes=True)


class SuperadminOrganizationListResult(BaseModel):
    """Service-layer paginated list (mapped to HTTP via list_response)."""

    items: list[SuperadminOrganizationListItem]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    message: str


class SuperadminOrganizationListQueryParams(BaseModel):
    """Validated query bundle for superadmin org list"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    search: str | None = Field(
        None,
        description="Search org name or owner name/email",
    )
    plan: PlanType | None = Field(None, description="Filter by subscription.plan_type")
    status: SuperadminOrganizationListStatus | None = Field(
        None, description="Filter by derived list status"
    )
    sort: SuperadminOrganizationListSortField = Field(
        default=SuperadminOrganizationListSortField.CREATED_AT,
        description="Sort field",
    )
    order: SuperadminOrganizationListSortOrder = Field(
        default=SuperadminOrganizationListSortOrder.DESC,
        description="Sort direction",
    )
