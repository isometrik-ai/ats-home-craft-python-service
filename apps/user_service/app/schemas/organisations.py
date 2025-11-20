# pylint: disable=invalid-name,E0213
"""
Organisation Schemas Module

This module contains all Pydantic models and schemas related to organisation management.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from fastapi import HTTPException, status
from apps.user_service.app.schemas.common import PaginationBase, SimpleResponse
from apps.user_service.app.schemas.auth import CompanyData, PlanType, User
from apps.user_service.app.schemas import ResponseModel, validate_path_field


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
        plan_type (str): Type of plan (starter, professional, enterprise)
        status (str): Organisation's current status (active, suspended, trial)
        max_users (int): Maximum number of users allowed
        timezone (str): Organisation's timezone setting
        created_at (Optional[str]): ISO timestamp when organisation was created
        updated_at (Optional[str]): ISO timestamp when organisation was last updated
        member_count (int): Number of active members in the organisation
        user_role (Optional[str]): Current user's role in this organisation
    """

    organization_id: str = Field(
        ..., description="Unique identifier for the organisation"
    )
    name: str = Field(..., description="Organisation's name")
    slug: str = Field(..., description="URL-friendly slug for the organisation")
    domain: Optional[str] = Field(None, description="Organisation's domain name")
    logo_url: Optional[str] = Field(None, description="URL to organisation's logo")
    plan_type: str = Field(
        ..., description="Type of plan (starter, professional, enterprise)"
    )
    status: str = Field(
        ..., description="Organisation's current status (active, suspended, trial)"
    )
    max_users: int = Field(..., description="Maximum number of users allowed")
    timezone: str = Field(default="UTC", description="Organisation's timezone setting")
    created_at: Optional[str] = Field(
        None, description="ISO timestamp when organisation was created"
    )
    updated_at: Optional[str] = Field(
        None, description="ISO timestamp when organisation was last updated"
    )
    member_count: int = Field(
        default=0, description="Number of active members in the organisation"
    )
    # user_role: Optional[str] = Field(
    #     None, description="Current user's role in this organisation"
    # )

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


class OrganisationListResponse(PaginationBase, ResponseModel):
    """Response model for organisation list operations

    This is the standard response wrapper for organisation list endpoints.

    Attributes:
        message (str): Response message describing the operation result
        data (List[OrganisationInfo]): List of organisations if successful
        total_count (int): Total number of organisations
        page (int): Current page number
        page_size (int): Number of items per page
    """

    data: List[OrganisationInfo] = Field(
        ..., description="List of organisations if successful"
    )
    total_count: int = Field(..., description="Total number of organisations")

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


class OrganisationResponse(SimpleResponse):
    """Response model for basic organisation operations."""


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

    name: str = Field(
        ..., min_length=2, max_length=255, description="Organisation's name"
    )
    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="URL-friendly slug for the organisation",
    )
    domain: Optional[str] = Field(None, description="Organisation's domain name")
    logo_url: Optional[str] = Field(None, description="URL to organisation's logo")
    plan_type: str = Field(
        default="starter",
        description="Type of plan (starter, professional, enterprise)",
    )
    max_users: int = Field(default=10, description="Maximum number of users allowed")
    timezone: str = Field(
        default="UTC", description="Organisation's timezone preference"
    )

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

    name: Optional[str] = Field(
        None, min_length=2, max_length=255, description="Updated organisation name"
    )
    slug: Optional[str] = Field(
        None, min_length=2, max_length=100, description="Updated slug"
    )
    domain: Optional[str] = Field(None, description="Updated domain name")
    logo_url: Optional[str] = Field(None, description="Updated logo URL")
    plan_type: Optional[str] = Field(None, description="Updated plan type")
    max_users: Optional[int] = Field(None, description="Updated maximum users")
    timezone: Optional[str] = Field(None, description="Updated timezone preference")

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v):
        """Validate logo_url is a valid path if provided (no URLs or base64 allowed)"""
        return validate_path_field(v, "logo_url")

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


class UpdateOrganisationResponse(ResponseModel):
    """Response model for organisation update operations

    Attributes:
        message (str): Response message
        data (Optional[OrganisationInfo]): Updated organisation data
    """

    data: Optional[OrganisationInfo] = Field(
        None, description="Updated organisation data"
    )

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
                }
            }
        }
    )


class OrganisationDetailResponse(ResponseModel):
    """Response model for organisation detail operations

    Attributes:
        message (str): Response message describing the operation result
        data (Optional[OrganisationInfo]): Organisation data if successful
    """

    data: Optional[OrganisationInfo] = Field(
        None, description="Organisation data if successful"
    )

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
                }
            }
        }
    )


        # email (EmailStr): User's email address for signup (required)
        # password (str): User's password for signup (required)
# class CreateOrganisationWithUser(BaseModel):
#     """Request model for creating a new organisation with user after signup

#     This schema is used when creating an organization.

#     Attributes:
#         user_id (str): User's ID (required)
#         full_name (str): User's full name (required)
#         name (str): Organisation's name (required)
#         slug (str): URL-friendly slug for the organisation (required)
#         domain (Optional[str]): Organisation's domain name
#         logo_url (Optional[str]): URL to organisation's logo
#         plan_type (str): Type of plan (starter, professional, enterprise)
#         max_users (int): Maximum number of users allowed
#         timezone (str): Organisation's timezone preference
#         phone (Optional[str]): User's phone number
#     """

#     # email: EmailStr = Field(..., description="User's email address for signup")
#     # password: str = Field(..., min_length=8, description="User's password for signup")
#     full_name: str = Field(
#         ..., min_length=2, max_length=255, description="User's full name"
#     )
#     name: str = Field(
#         ..., min_length=2, max_length=255, description="Organisation's name"
#     )
#     slug: str = Field(
#         ...,
#         min_length=2,
#         max_length=100,
#         description="URL-friendly slug for the organisation",
#     )
#     domain: Optional[str] = Field(None, description="Organisation's domain name")
#     logo_url: Optional[str] = Field(None, description="URL to organisation's logo")
#     plan_type: str = Field(
#         default="starter",
#         description="Type of plan (starter, professional, enterprise)",
#     )
#     max_users: int = Field(default=10, description="Maximum number of users allowed")
#     timezone: str = Field(
#         default="UTC", description="Organisation's timezone preference"
#     )
#     phone: Optional[str] = Field(None, description="User's phone number")

#     model_config = ConfigDict(
#         json_schema_extra={
#             "example": {
#                 "user_id": "550e8400-e29b-41d4-a716-446655440000",
#                 # "email": "admin@acme.com",
#                 # "password": "SecurePassword123!",
#                 "full_name": "John Doe",
#                 "name": "Acme Corporation",
#                 "slug": "acme-corp",
#                 "domain": "acme.com",
#                 "logo_url": "https://example.com/logo.png",
#                 "plan_type": "professional",
#                 "max_users": 100,
#                 "timezone": "UTC",
#                 "phone": "+1234567890",
#             }
#         }
#     )

class NewOrganisationBody(BaseModel):
    """Request body for creating a new organisation along with an initial user.

    This model is used when a new organisation is being registered and an initial user
    (such as the owner or admin) is created at the same time.

    Attributes:
        user_data (Optional[User]): Information about the initial user to be created.
        company_data (Optional[CompanyData]): Information about the organisation/company.
        plan_type (PlanType): The subscription plan type for the organisation (default: starter).
    """
    user_data: Optional[User] = None
    company_data: CompanyData
    plan_type: PlanType = PlanType.STARTER


class CreateOrganisationWithUserResponse(ResponseModel):
    """Response model for creating organisation with user signup

    Attributes:
        message (str): Response message describing the operation result
        data (dict): Created organisation and user data
    """

    data: dict = Field(..., description="Created organisation and user data")

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
                }
            }
        }
    )


class OrganizationUpdate(BaseModel):
    """
    Payload for organisation *owners or standard admins*.
    Every field is optional so the caller may patch only what they need.
    """

    # ─── Brand & profile ───
    name: Optional[str] = Field(
        None,
        max_length=255,
        description="Organisation's display name",
    )
    logo_url: Optional[str] = Field(
        None,
        description="Path to the organisation's logo image (e.g., 'house-of-apps-legal-ai/org-id/filename.jpg')",
    )

    @field_validator("logo_url", mode="before")
    @classmethod
    def validate_logo_url(cls, v):
        """Validate logo_url is a valid path if provided (no URLs or base64 allowed)"""
        return validate_path_field(v, "logo_url")

    industry: Optional[str] = Field(
        None,
        max_length=100,
        description="Industry or vertical the organisation operates in",
    )
    company_size: Optional[str] = Field(
        None,
        max_length=50,
        description="Company size bracket (e.g. '1-10', '11-50')",
    )
    description: Optional[str] = Field(
        None,
        description="Short description or mission statement of the organisation",
    )
    referral_source: Optional[str] = Field(
        None,
        max_length=100,
        description="How the organisation first heard about the platform",
    )

    # ─── Preferences ───
    settings: Optional[dict] = Field(
        None,
        description="Custom JSON settings that apply organisation-wide",
        examples=[{}]
    )
    timezone: Optional[str] = Field(
        None,
        max_length=50,
        description="Default timezone for the organisation",
    )

    model_config = ConfigDict(
        extra = "forbid",
        str_strip_whitespace = True,
        str_min_length = 1,
    )

class OrganizationAdminUpdate(OrganizationUpdate):
    """
    Superset for *platform or billing admins*.
    Inherits the regular editable fields and adds sensitive columns.
    """

    slug: Optional[str] = Field(
        None,
        description="URL-friendly slug used in organisation-specific links",
    )
    domain: Optional[str] = Field(
        None,
        max_length=255,
        description="Primary domain name associated with the organisation",
    )
    plan_type: Optional[str] = Field(
        None,
        description="Subscription plan type (starter, professional, enterprise)",
    )
    status: Optional[str] = Field(
        None,
        description="Organisation's account status (active, suspended, trial)",
    )
    max_users: Optional[int] = Field(
        None,
        ge=1,
        description="Maximum number of users allowed under the current plan",
    )

    @model_validator(mode='after')
    def check_at_least_one_field(self):
        """
        Check if at least one field has a non-None value.
        """
        # Get all field values dynamically & Check if at least one value is not None
        if all(getattr(self, field) is None for field in self.__pydantic_fields__):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one field must have a non-None value")
        return self
