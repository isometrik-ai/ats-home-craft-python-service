"""Schemas for superadmin organization APIs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.auth import SelectOrganizationResponse
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


class SuperadminImpersonationResponse(BaseModel):
    """Session for the organization owner after superadmin impersonation.

    Token timing fields mirror ``AuthService.login`` / ``AuthResponse`` (``session.expires_in``,
    ``session.expires_at``) for consistent client handling with ``POST /v1/auth/login``.
    """

    access_token: str = Field(..., description="Bearer access token for the owner session")
    refresh_token: str | None = Field(
        None, description="Refresh token when issued by auth provider"
    )
    expires_in: int | None = Field(
        None,
        description="Access token lifetime in seconds (same as login: session.expires_in)",
    )
    expires_at: datetime | None = Field(
        None,
        description="Access token expiry (same as login: session.expires_at)",
    )
    token_type: str = Field(default="bearer", description="OAuth token type")
    organization_id: str = Field(..., description="Organization UUID acted on")
    organization_name: str | None = Field(None, description="Organization display name")
    impersonated_user_id: str | None = Field(
        None,
        description="Auth user id of the organization owner whose session was issued",
    )
    select_organization: SelectOrganizationResponse | None = Field(
        None,
        description=(
            "Organization context for the impersonated session (same shape as "
            "POST /v1/auth/select-org and set-password carry-over)"
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ...",
                "refresh_token": "v1...",
                "expires_in": 3600,
                "expires_at": "2026-01-15T12:00:00",
                "token_type": "bearer",
                "organization_id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_name": "Acme Legal",
                "impersonated_user_id": "660e8400-e29b-41d4-a716-446655440001",
            }
        }
    )
