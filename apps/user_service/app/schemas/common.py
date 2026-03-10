"""Common Schemas Module.

Shared schemas used across multiple modules to avoid circular dependencies.
"""

from pydantic import BaseModel, Field

from apps.user_service.app.schemas.enums import PlanType, PracticeArea


class Address(BaseModel):
    """Address information."""

    address_line: str | None = Field(None, description="Address line")
    city: str | None = Field(None, description="City")
    state: str | None = Field(None, description="State")
    zip_code: str | None = Field(None, description="Zip code")
    country: str = Field(..., description="Country name")


class Subscription(BaseModel):
    """Subscription information."""

    max_users: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of licensed seats for the organization",
    )
    users: int | None = Field(
        default=None,
        ge=0,
        description="Current number of organization members (licensed seats in use)",
    )
    plan_type: PlanType = Field(
        default=PlanType.TRIAL,
        description="Current subscription plan type",
    )
    start_date: str | None = Field(
        default=None,
        description="ISO timestamp when the subscription becomes active",
    )
    end_date: str | None = Field(
        default=None,
        description="ISO timestamp when the subscription expires",
    )


class OrganizationBasicDetails(BaseModel):
    """Model for organization basic details"""

    id: str = Field(..., description="Unique identifier for the organization")
    name: str = Field(..., description="Organization's name")
    domain: str | None = Field(None, description="Organization's domain name")
    logo_url: str | None = Field(None, description="URL to organization's logo")
    description: str | None = Field(None, description="Organization's description")
    company_size: str | None = Field(None, description="Organization's company size")
    address: Address | None = Field(None, description="Organization's address")
    subscription: Subscription | None = Field(None, description="Organization's subscription")
    primary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organization's primary practice areas"
    )
    secondary_practice_areas: list[PracticeArea] | None = Field(
        None, description="Organization's secondary practice areas"
    )
