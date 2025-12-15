"""Organisation Schemas Module.

This module contains all Pydantic models and schemas related to organisation management.
These schemas are used for request/response validation and API documentation.
"""

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.auth import (
    Address,
    CompanyData,
    ComplianceSecurity,
    EnterpriseFeatures,
    PracticeArea,
    PreferredIntegration,
    Specialization,
    Subscription,
    TeamSetup,
    User,
)


class OrganisationInfo(BaseModel):
    """Model for organisation information

    This model contains all organisation information including basic details,
    plan information, and user-specific data.

    Attributes:
        organization_id (str): Unique identifier for the organisation
        name (str): Organisation's name
        slug (str): URL-friendly slug for the organisation
        domain (Optional[str]): Organisation's domain name
        logo_url (Optional[str]): URL to organisation's logo
        status (str): Organisation's current status (active, suspended, trial)
        subscription (Optional[Subscription]): Subscription information
        timezone (str): Organisation's timezone setting
        created_at (Optional[str]): ISO timestamp when organisation was created
        updated_at (Optional[str]): ISO timestamp when organisation was last updated
        member_count (int): Number of active members in the organisation
        user_role (Optional[str]): Current user's role in this organisation
    """

    organization_id: str = Field(..., description="Unique identifier for the organisation")
    name: str = Field(..., description="Organisation's name")
    slug: str = Field(..., description="URL-friendly slug for the organisation")
    domain: str | None = Field(None, description="Organisation's domain name")
    logo_url: str | None = Field(None, description="URL to organisation's logo")
    subscription: Subscription | None = Field(
        default=None,
        description="Subscription information stored in the dedicated column",
    )
    status: str = Field(..., description="Organisation's current status (active, suspended, trial)")
    timezone: str = Field(default="UTC", description="Organisation's timezone setting")
    created_at: str | None = Field(None, description="ISO timestamp when organisation was created")
    updated_at: str | None = Field(
        None, description="ISO timestamp when organisation was last updated"
    )
    member_count: int = Field(default=0, description="Number of active members in the organisation")
    address: Address | None = Field(None, description="Organisation's address")
    primary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organisation's primary practice areas"
    )
    secondary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organisation's secondary practice areas"
    )
    specializations: list[Specialization] | None = Field(
        None, description="Organisation's specializations"
    )
    preferred_integration: list[PreferredIntegration] | None = Field(
        None, description="Organisation's preferred integrations"
    )
    need_help_importing_data: bool | None = Field(
        None, description="Organisation's need help importing data"
    )
    need_migration_assistance: bool | None = Field(
        None, description="Organisation's need migration assistance"
    )
    compliance_security: ComplianceSecurity | None = Field(
        None, description="Organisation's compliance security"
    )
    enterprise_features: EnterpriseFeatures | None = Field(
        None, description="Organisation's enterprise features"
    )
    team_setup: TeamSetup | None = Field(None, description="Organisation's team setup")
    description: str | None = Field(None, description="Organisation's description")
    company_size: str | None = Field(None, description="Organisation's company size")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "name": "Acme Corporation",
                "slug": "acme-corp",
                "domain": "acme.com",
                "logo_url": "example.com/logo.png",
                "plan_type": "professional",
                "status": "active",
                "max_users": 100,
                "timezone": "UTC",
                "created_at": "2024-12-19T10:00:00Z",
                "updated_at": "2024-12-19T15:30:00Z",
                "member_count": 25,
                "user_role": "Administrator",
            }
        }
    )


class OrganisationListResponse(BaseModel):
    """Response model for organisation list operations

    This is the standard response wrapper for organisation list endpoints.

    Attributes:
        message (str): Response message describing the operation result
        data (List[OrganisationInfo]): List of organisations if successful
        total_count (int): Total number of organisations
        page (int): Current page number
        page_size (int): Number of items per page
    """

    data: list[OrganisationInfo] = Field(..., description="List of organisations if successful")
    total_count: int = Field(..., description="Total number of organisations")
    message: str = Field(..., description="Response message describing the operation result")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total number of pages")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organizations retrieved successfully",
                "data": [
                    {
                        "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "name": "Apple Corporation",
                        "slug": "acme-corp",
                        "domain": "apple.com",
                        "logo_url": "https://example.com/logo.png",
                        "plan_type": "professional",
                        "status": "active",
                        "max_users": 100,
                        "timezone": "UTC",
                        "created_at": "2024-12-19T10:00:00Z",
                        "updated_at": "2024-12-19T15:30:00Z",
                        "member_count": 25,
                        "user_role": "Administrator",
                    }
                ],
                "total_count": 1,
                "page": 1,
                "page_size": 20,
            }
        }
    )


class OrganisationResponse(BaseModel):
    """Response model for basic organisation operations."""

    message: str = Field(..., description="Response message describing the operation result")
    status: str = Field(..., description="Response status (success or error)")


class CreateOrganisationRequest(BaseModel):
    """Request model for creating a new organisation

    Attributes:
        name (str): Organisation's name (required)
        slug (str): URL-friendly slug for the organisation (required)
        domain (Optional[str]): Organisation's domain name
        logo_url (Optional[str]): URL to organisation's logo
        plan_type (str): Type of plan (starter, professional, enterprise)
        max_users (int): Maximum number of users allowed
        timezone (str): Organisation's timezone preference
    """

    name: str = Field(..., min_length=2, max_length=255, description="Organisation's name")
    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="URL-friendly slug for the organisation",
    )
    domain: str | None = Field(None, description="Organisation's domain name")
    logo_url: str | None = Field(None, description="URL to organisation's logo")
    plan_type: str = Field(
        default="starter",
        description="Type of plan (starter, professional, enterprise)",
    )
    max_users: int = Field(default=10, description="Maximum number of users allowed")
    timezone: str = Field(default="UTC", description="Organisation's timezone preference")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Samsung Electronics",
                "slug": "acme-corp",
                "domain": "samsung.com",
                "logo_url": "https://demo.com/logo.png",
                "plan_type": "professional",
                "max_users": 100,
                "timezone": "UTC",
            }
        }
    )


class UpdateOrganisationRequest(BaseModel):
    """Request model for updating organisation information

    All fields are optional for partial updates.

    Attributes:
        name (Optional[str]): Updated organisation name
        slug (Optional[str]): Updated slug
        domain (Optional[str]): Updated domain name
        logo_url (Optional[str]): Updated logo URL
        plan_type (Optional[str]): Updated plan type
        max_users (Optional[int]): Updated maximum users
        timezone (Optional[str]): Updated timezone preference
    """

    name: str | None = Field(
        None, min_length=2, max_length=255, description="Updated organisation name"
    )
    slug: str | None = Field(None, min_length=2, max_length=100, description="Updated slug")
    domain: str | None = Field(None, description="Updated domain name")
    logo_url: str | None = Field(None, description="Updated logo URL")
    plan_type: str | None = Field(None, description="Updated plan type")
    max_users: int | None = Field(None, description="Updated maximum users")
    timezone: str | None = Field(None, description="Updated timezone preference")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Acme Corporation",
                "domain": "newacme.com",
                "plan_type": "enterprise",
                "max_users": 200,
            }
        }
    )


class UpdateOrganisationResponse(BaseModel):
    """Response model for organisation update operations

    Attributes:
        message (str): Response message
        data (Optional[OrganisationInfo]): Updated organisation data
    """

    data: OrganisationInfo | None = Field(None, description="Updated organisation data")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organisation updated successfully",
                "data": {
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "name": "Updated Acme Corporation",
                    "slug": "acme-corp",
                    "domain": "newacme.com",
                    "plan_type": "enterprise",
                    "status": "active",
                    "max_users": 200,
                    "timezone": "UTC",
                },
            }
        }
    )


class OrganisationDetailResponse(BaseModel):
    """Response model for organisation detail operations

    Attributes:
        message (str): Response message describing the operation result
        data (Optional[OrganisationInfo]): Organisation data if successful
    """

    data: OrganisationInfo | None = Field(None, description="Organisation data if successful")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organisation retrieved successfully",
                "data": {
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "name": "Sony Corporation",
                    "slug": "acme-corp",
                    "domain": "sony.com",
                    "plan_type": "professional",
                    "status": "active",
                    "max_users": 100,
                    "timezone": "UTC",
                    "user_role": "Administrator",
                },
            }
        }
    )


class NewOrganisationBody(BaseModel):
    """Request body for creating a new organisation along with an initial user.

    This model is used when a new organisation is being registered and an initial user
    (such as the owner or admin) is created at the same time.

    Attributes:
        user_data (Optional[User]): Information about the initial user to be created.
        company_data (Optional[CompanyData]): Information about the organisation/company.
    """

    user_data: User | None = None
    company_data: CompanyData


class CreateOrganisationWithUserResponse(BaseModel):
    """Response model for creating organisation with user signup

    Attributes:
        message (str): Response message describing the operation result
        data (dict): Created organisation and user data
    """

    data: dict[str, Any] = Field(..., description="Created organisation and user data")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organisation and user created successfully",
                "data": {
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "user_id": "550e8400-e29b-41d4-a716-446655440000",
                    "organization_name": "Acme Corporation",
                    "user_email": "admin@acme.com",
                    "role_name": "admin",
                },
            }
        }
    )


class OrganizationUpdate(BaseModel):
    """Payload for organisation *owners or standard admins*.
    Every field is optional so the caller may patch only what they need.
    """

    # ─── Brand & profile ───
    name: str | None = Field(
        None,
        max_length=255,
        description="Organisation's display name",
    )
    logo_url: str | None = Field(
        None,
        description=(
            "Path to the organisation's logo image "
            "(e.g., 'house-of-apps-legal-ai/org-id/filename.jpg')"
        ),
    )
    industry: str | None = Field(
        None,
        max_length=100,
        description="Industry or vertical the organisation operates in",
    )
    company_size: str | None = Field(
        None,
        max_length=50,
        description="Company size bracket (e.g. '1-10', '11-50')",
    )
    description: str | None = Field(
        None,
        description="Short description or mission statement of the organisation",
    )
    referral_source: str | None = Field(
        None,
        max_length=100,
        description="How the organisation first heard about the platform",
    )
    address: Address | None = Field(None, description="Organisation's address")
    primary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organisation's primary practice areas"
    )
    secondary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organisation's secondary practice areas"
    )
    specializations: list[Specialization] | None = Field(
        None, description="Organisation's specializations"
    )
    preferred_integration: list[PreferredIntegration] | None = Field(
        None, description="Organisation's preferred integrations"
    )
    compliance_security: ComplianceSecurity | None = Field(
        None, description="Organisation's compliance security"
    )
    enterprise_features: EnterpriseFeatures | None = Field(
        None, description="Organisation's enterprise features"
    )
    team_setup: TeamSetup | None = Field(None, description="Organisation's team setup")

    timezone: str | None = Field(
        None,
        max_length=50,
        description="Default timezone for the organisation",
    )

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        str_min_length=1,
    )


class OrganizationAdminUpdate(OrganizationUpdate):
    """Superset for *platform or billing admins*.

    Inherits the regular editable fields and adds sensitive columns.
    """

    slug: str | None = Field(
        None,
        description="URL-friendly slug used in organisation-specific links",
    )
    domain: str | None = Field(
        None,
        max_length=255,
        description="Primary domain name associated with the organisation",
    )
    status: str | None = Field(
        None,
        description="Organisation's account status (active, suspended, trial)",
    )

    @model_validator(mode="after")
    def check_at_least_one_field(self):
        """Check if at least one field has a non-None value."""
        # Get all field values dynamically & Check if at least one value is not None
        if all(getattr(self, field) is None for field in self.__pydantic_fields__):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one field must have a non-None value",
            )
        return self
