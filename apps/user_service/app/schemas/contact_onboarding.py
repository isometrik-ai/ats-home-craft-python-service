"""Contact onboarding schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contacts import (
    CommunicationPreferences,
    ContactDetailsResponse,
    FlexibleOptionalDate,
)
from apps.user_service.app.schemas.enums import (
    ContactBloodGroup,
    ContactGender,
    ContactOnboardingStep,
    ContactUnitRelationship,
    VehicleType,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


def _validate_exactly_one_primary_phone(phones: list[Phone]) -> list[Phone]:
    """Require exactly one primary phone in the list."""
    primary_count = sum(1 for phone in phones if phone.is_primary)
    if primary_count != 1:
        raise ValidationException(
            message_key="contacts.errors.exactly_one_primary_phone",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return phones


class ContactUnitSummaryResponse(BaseModel):
    """Contact-unit row with unit display fields."""

    model_config = ConfigDict(extra="ignore")

    id: str
    unit_id: str
    project_id: str
    contact_id: str
    code: str
    unit_label: str | None = None
    tower_name: str | None = None
    floor_name: str | None = None
    config_label: str | None = None
    status: str
    is_primary: bool = False
    is_default_login: bool = False
    relationship: str = "self"
    contact_type: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class ConfirmPropertiesRequest(BaseModel):
    """Confirm selected properties during onboarding step 1."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_ids: list[str] = Field(..., min_length=1)


class CompleteProfileRequest(BaseModel):
    """Complete profile step payload."""

    model_config = ConfigDict(extra="forbid")

    prefix: str | None = Field(None, max_length=50)
    first_name: str = Field(..., max_length=100)
    last_name: str | None = Field(None, max_length=100)
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = Field(None, max_length=500)
    gender: ContactGender | None = None
    blood_group: ContactBloodGroup | None = None
    communication_preferences: CommunicationPreferences | None = None
    emails: list[Email] | None = Field(None, max_length=20)
    phones: list[Phone] | None = Field(None, max_length=20)

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone] | None) -> list[Phone] | None:
        """Validate exactly one primary phone."""
        if phones is None:
            return phones
        return _validate_exactly_one_primary_phone(phones)


class CreateVehicleRequest(BaseModel):
    """Register a vehicle."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    vehicle_type: VehicleType
    registration_number: str = Field(..., min_length=1, max_length=20)
    make: str | None = Field(None, max_length=100)
    model: str | None = Field(None, max_length=100)
    color: str | None = Field(None, max_length=50)
    photo_path: str | None = Field(None, max_length=500)


class UpdateVehicleRequest(BaseModel):
    """Patch a vehicle."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    vehicle_type: VehicleType | None = None
    registration_number: str | None = Field(None, min_length=1, max_length=20)
    make: str | None = Field(None, max_length=100)
    model: str | None = Field(None, max_length=100)
    color: str | None = Field(None, max_length=50)
    photo_path: str | None = Field(None, max_length=500)


class VehicleResponse(BaseModel):
    """Vehicle row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    contact_id: str
    unit_id: str
    vehicle_type: str
    registration_number: str
    make: str | None = None
    model: str | None = None
    color: str | None = None
    photo_path: str | None = None
    status: str
    sort_order: int = 0
    created_at: str
    updated_at: str


class CreateHouseholdMemberRequest(BaseModel):
    """Add a family member to a unit."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    first_name: str = Field(..., max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phones: list[Phone] = Field(..., min_length=1, max_length=20)
    relationship: ContactUnitRelationship
    portal_access: bool = False

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone]) -> list[Phone]:
        """Validate exactly one primary phone."""
        return _validate_exactly_one_primary_phone(phones)


class SetDefaultUnitRequest(BaseModel):
    """Choose default login unit."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_id: str


class CompleteStepRequest(BaseModel):
    """Mark an optional step complete (e.g. vehicles with zero items)."""

    model_config = ConfigDict(extra="forbid")

    step_key: ContactOnboardingStep


class AdminAssignUnitRequest(BaseModel):
    """Admin pre-allotment of a unit to a contact."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    is_primary: bool = False
    relationship: ContactUnitRelationship = ContactUnitRelationship.SELF


class OnboardingStepResponse(BaseModel):
    """Single wizard step."""

    step_key: str
    status: str
    completed_at: str | None = None


class OnboardingStatusResponse(BaseModel):
    """Wizard progress."""

    setup_current_step: str | None
    is_completed: bool
    steps: list[OnboardingStepResponse]


class HouseholdMemberResponse(BaseModel):
    """Family member linked to a unit."""

    contact_id: str
    contact_unit_id: str
    unit_id: str
    first_name: str | None = None
    last_name: str | None = None
    relationship: str
    portal_access: bool = False
    phones: list[Any] = Field(default_factory=list)


class OnboardingReviewResponse(BaseModel):
    """Review screen aggregate."""

    contact: ContactDetailsResponse
    units: list[ContactUnitSummaryResponse]
    vehicles: list[VehicleResponse]
    household: list[HouseholdMemberResponse]
    steps: list[OnboardingStepResponse]
