"""Organization Schemas Module.

This module contains all Pydantic models and schemas related to organization management.
These schemas are used for request/response validation and API documentation.
"""

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.auth import (
    CompanyData,
    ComplianceSecurity,
    EnterpriseFeatures,
    PreferredIntegration,
    Specialization,
    TeamSetup,
    User,
)
from apps.user_service.app.schemas.common import (
    NonEmptyStr,
    OrganizationAddress,
    Subscription,
)
from apps.user_service.app.schemas.enums import (
    DeleteRequestStatus,
    OrganizationStatus,
)


class OrganizationInfo(BaseModel):
    """Model for organization information

    This model contains all organization information including basic details,
    plan information, and user-specific data.

    Attributes:
        organization_id (str): Unique identifier for the organization
        name (str): Organization's name
        slug (str): URL-friendly slug for the organization
        domain (Optional[str]): Organization's domain name
        logo_url (Optional[str]): URL to organization's logo
        status (str): Organization's current status (active, suspended, trial)
        subscription (Optional[Subscription]): Subscription information
        timezone (str): Organization's timezone setting
        created_at (Optional[str]): ISO timestamp when organization was created
        updated_at (Optional[str]): ISO timestamp when organization was last updated
        member_count (int): Number of active members in the organization
        user_role (Optional[str]): Current user's role in this organization
    """

    organization_id: str = Field(..., description="Unique identifier for the organization")
    name: str = Field(..., description="Organization's name")
    slug: str = Field(..., description="URL-friendly slug for the organization")
    domain: str | None = Field(None, description="Organization's domain name")
    logo_url: str | None = Field(None, description="URL to organization's logo")
    subscription: Subscription | None = Field(
        default=None,
        description="Subscription information stored in the dedicated column",
    )
    status: OrganizationStatus = Field(..., description="Organization's current status")
    timezone: str = Field(default="UTC", description="Organization's timezone setting")
    created_at: str | None = Field(None, description="ISO timestamp when organization was created")
    updated_at: str | None = Field(
        None, description="ISO timestamp when organization was last updated"
    )
    member_count: int = Field(default=0, description="Number of active members in the organization")
    address: OrganizationAddress | None = Field(None, description="Organization's address")
    primary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's primary practice areas"
    )
    secondary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's secondary practice areas"
    )
    specializations: list[Specialization] | None = Field(
        None, description="Organization's specializations"
    )
    preferred_integration: list[PreferredIntegration] | None = Field(
        None, description="Organization's preferred integrations"
    )
    need_help_importing_data: bool | None = Field(
        None, description="Organization's need help importing data"
    )
    need_migration_assistance: bool | None = Field(
        None, description="Organization's need migration assistance"
    )
    compliance_security: ComplianceSecurity | None = Field(
        None, description="Organization's compliance security"
    )
    enterprise_features: EnterpriseFeatures | None = Field(
        None, description="Organization's enterprise features"
    )
    team_setup: TeamSetup | None = Field(None, description="Organization's team setup")
    description: str | None = Field(None, description="Organization's description")
    company_size: str | None = Field(None, description="Organization's company size")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "name": "Acme Corporation",
                "slug": "acme-corp",
                "domain": "acme.com",
                "logo_url": "example.com/logo.png",
                "plan_type": "professional",
                "status": OrganizationStatus.ACTIVE.value,
                "max_users": 100,
                "timezone": "UTC",
                "created_at": "2024-12-19T10:00:00Z",
                "updated_at": "2024-12-19T15:30:00Z",
                "member_count": 25,
                "user_role": "Administrator",
            }
        }
    )


class OrganizationListResponse(BaseModel):
    """Response model for organization list operations

    This is the standard response wrapper for organization list endpoints.

    Attributes:
        message (str): Response message describing the operation result
        data (List[OrganizationInfo]): List of organizations if successful
        total_count (int): Total number of organizations
        page (int): Current page number
        page_size (int): Number of items per page
    """

    data: list[OrganizationInfo] = Field(..., description="List of organizations if successful")
    total_count: int = Field(..., description="Total number of organizations")
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
                        "status": OrganizationStatus.ACTIVE.value,
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


class OrganizationResponse(BaseModel):
    """Response model for basic organization operations."""

    message: str = Field(..., description="Response message describing the operation result")
    status: str = Field(..., description="Response status (success or error)")


class CreateOrganizationRequest(BaseModel):
    """Request model for creating a new organization

    Attributes:
        name (str): Organization's name (required)
        slug (str): URL-friendly slug for the organization (required)
        domain (Optional[str]): Organization's domain name
        logo_url (Optional[str]): URL to organization's logo
        plan_type (str): Type of plan (starter, professional, enterprise)
        max_users (int): Maximum number of users allowed
        timezone (str): Organization's timezone preference
    """

    name: str = Field(..., min_length=2, max_length=255, description="Organization's name")
    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="URL-friendly slug for the organization",
    )
    domain: str | None = Field(None, description="Organization's domain name")
    logo_url: str | None = Field(None, description="URL to organization's logo")
    plan_type: str = Field(
        default="starter",
        description="Type of plan (starter, professional, enterprise)",
    )
    max_users: int = Field(default=10, description="Maximum number of users allowed")
    timezone: str = Field(default="UTC", description="Organization's timezone preference")

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


class UpdateOrganizationRequest(BaseModel):
    """Request model for updating organization information

    All fields are optional for partial updates.

    Attributes:
        name (Optional[str]): Updated organization name
        slug (Optional[str]): Updated slug
        domain (Optional[str]): Updated domain name
        logo_url (Optional[str]): Updated logo URL
        plan_type (Optional[str]): Updated plan type
        max_users (Optional[int]): Updated maximum users
        timezone (Optional[str]): Updated timezone preference
    """

    name: str | None = Field(
        None, min_length=2, max_length=255, description="Updated organization name"
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


class UpdateOrganizationResponse(BaseModel):
    """Response model for organization update operations

    Attributes:
        message (str): Response message
        data (Optional[OrganizationInfo]): Updated organization data
    """

    data: OrganizationInfo | None = Field(None, description="Updated organization data")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organization updated successfully",
                "data": {
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "name": "Updated Acme Corporation",
                    "slug": "acme-corp",
                    "domain": "newacme.com",
                    "plan_type": "enterprise",
                    "status": OrganizationStatus.ACTIVE.value,
                    "max_users": 200,
                    "timezone": "UTC",
                },
            }
        }
    )


class OrganizationDetailResponse(BaseModel):
    """Response model for organization detail operations

    Attributes:
        message (str): Response message describing the operation result
        data (Optional[OrganizationInfo]): Organization data if successful
    """

    data: OrganizationInfo | None = Field(None, description="Organization data if successful")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organization retrieved successfully",
                "data": {
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "name": "Sony Corporation",
                    "slug": "acme-corp",
                    "domain": "sony.com",
                    "plan_type": "professional",
                    "status": OrganizationStatus.ACTIVE.value,
                    "max_users": 100,
                    "timezone": "UTC",
                    "user_role": "Administrator",
                },
            }
        }
    )


class NewOrganizationBody(BaseModel):
    """Request body for creating a new organization along with an initial user.

    This model is used when a new organization is being registered and an initial user
    (such as the owner or admin) is created at the same time.

    Attributes:
        user_data (Optional[User]): Information about the initial user to be created.
        company_data (Optional[CompanyData]): Information about the organization/company.
    """

    user_data: User | None = None
    company_data: CompanyData


class CreateOrganizationWithUserResponse(BaseModel):
    """Response model for creating organization with user signup

    Attributes:
        message (str): Response message describing the operation result
        data (dict): Created organization and user data
    """

    data: dict[str, Any] = Field(..., description="Created organization and user data")
    message: str = Field(..., description="Response message describing the operation result")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Organization and user created successfully",
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
    """Payload for organization *owners or standard admins*.
    Every field is optional so the caller may patch only what they need.
    """

    # ─── Brand & profile ───
    name: str | None = Field(
        None,
        max_length=255,
        description="Organization's display name",
    )
    logo_url: str | None = Field(
        None,
        description=(
            "Path to the organization's logo image "
            "(e.g., 'house-of-apps-legal-ai/org-id/filename.jpg')"
        ),
    )
    industry: str | None = Field(
        None,
        max_length=100,
        description="Industry or vertical the organization operates in",
    )
    company_size: str | None = Field(
        None,
        max_length=50,
        description="Company size bracket (e.g. '1-10', '11-50')",
    )
    description: str | None = Field(
        None,
        description="Short description or mission statement of the organization",
    )
    referral_source: str | None = Field(
        None,
        max_length=100,
        description="How the organization first heard about the platform",
    )
    address: OrganizationAddress | None = Field(None, description="Organization's address")
    primary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's primary practice areas"
    )
    secondary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's secondary practice areas"
    )
    specializations: list[Specialization] | None = Field(
        None, description="Organization's specializations"
    )
    preferred_integration: list[PreferredIntegration] | None = Field(
        None, description="Organization's preferred integrations"
    )
    compliance_security: ComplianceSecurity | None = Field(
        None, description="Organization's compliance security"
    )
    enterprise_features: EnterpriseFeatures | None = Field(
        None, description="Organization's enterprise features"
    )
    team_setup: TeamSetup | None = Field(None, description="Organization's team setup")

    timezone: str | None = Field(
        None,
        max_length=50,
        description="Default timezone for the organization",
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
        description="URL-friendly slug used in organization-specific links",
    )
    domain: str | None = Field(
        None,
        max_length=255,
        description="Primary domain name associated with the organization",
    )
    status: OrganizationStatus | None = Field(
        None,
        description="Organization's account status (active, suspended, trial)",
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


class DeleteRequestInfo(BaseModel):
    """Model for delete request information.

    Attributes:
        request_id (str): Unique identifier for the delete request
        organization_id (str): Organization ID
        requester_id (str): User ID of the requester
        status (str): Request status (pending, approved, rejected, cancelled, completed)
        requested_at (str): ISO timestamp when request was created
        reviewed_at (str | None): ISO timestamp when review was made
        processed_at (str | None): ISO timestamp when request was processed
        approver_id (str | None): User ID of the approver
        review_reason (str | None): Reason for the review
        created_at (str): ISO timestamp when record was created
        updated_at (str): ISO timestamp when record was last updated
    """

    request_id: str = Field(..., description="Unique identifier for the delete request")
    organization_id: str = Field(..., description="Organization ID")
    requester_id: str = Field(..., description="User ID of the requester")
    status: DeleteRequestStatus = Field(..., description="Request status")
    requested_at: str = Field(..., description="ISO timestamp when request was created")
    reviewed_at: str | None = Field(None, description="ISO timestamp when review was made")
    processed_at: str | None = Field(None, description="ISO timestamp when request was processed")
    approver_id: str | None = Field(None, description="User ID of the approver")
    review_reason: str | None = Field(None, description="Reason for the review")
    created_at: str = Field(..., description="ISO timestamp when record was created")
    updated_at: str = Field(..., description="ISO timestamp when record was last updated")


class DeleteRequestListResponse(BaseModel):
    """Response model for delete request list operations.

    Attributes:
        message (str): Response message describing the operation result
        data (list[DeleteRequestInfo]): List of delete requests
        total_count (int): Total number of delete requests
        page (int): Current page number
        page_size (int): Number of items per page
        total_pages (int): Total number of pages
    """

    message: str = Field(..., description="Response message describing the operation result")
    data: list[DeleteRequestInfo] = Field(..., description="List of delete requests")
    total_count: int = Field(..., description="Total number of delete requests")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total number of pages")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data": [
                    {
                        "request_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "organization_id": "550e8400-e29b-41d4-a716-446655440000",
                        "requester_id": "660e8400-e29b-41d4-a716-446655440001",
                        "status": DeleteRequestStatus.PENDING.value,
                        "requested_at": "2024-12-19T10:00:00Z",
                        "reviewed_at": None,
                        "processed_at": None,
                        "approver_id": None,
                        "review_reason": None,
                        "created_at": "2024-12-19T10:00:00Z",
                        "updated_at": "2024-12-19T10:00:00Z",
                    }
                ],
                "total_count": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
            }
        }
    )


class ApproveRejectDeleteRequestBody(BaseModel):
    """Request body for approving or rejecting a delete request.

    Attributes:
        is_accepted (bool): True to approve, False to reject
        reason (str): Reason for the decision
    """

    is_accepted: bool = Field(..., description="True to approve deletion, False to reject")
    reason: str = Field(..., min_length=1, max_length=1000, description="Reason for the decision")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "is_accepted": True,
                "reason": "Organization deletion approved after review",
            }
        }
    )
