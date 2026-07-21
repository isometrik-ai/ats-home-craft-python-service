"""Contact onboarding schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    VehicleFuelType,
    VehicleStatus,
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
    """Confirm selected properties after the profile step."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_ids: list[str] = Field(..., min_length=1)
    default_contact_unit_id: str | None = Field(
        None,
        description="Optional default login unit when confirming multiple properties.",
    )


class ConfirmedPropertyItem(BaseModel):
    """One contact_unit row confirmed or claimed."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: str


class ClaimPropertiesRequest(BaseModel):
    """Claim pending properties after onboarding is complete."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_ids: list[str] = Field(..., min_length=1)


class ClaimPropertiesResponse(BaseModel):
    """Result of claiming one or more post-onboarding properties."""

    model_config = ConfigDict(extra="forbid")

    items: list[ConfirmedPropertyItem]
    requires_default_unit: bool = False


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
    photo_paths: list[str] = Field(default_factory=list, max_length=10)
    fuel_type: VehicleFuelType | None = None

    @field_validator("photo_paths")
    @classmethod
    def validate_photo_paths(cls, photo_paths: list[str]) -> list[str]:
        """Validate storage paths for vehicle images."""
        for path in photo_paths:
            if not path or len(path) > 500:
                raise ValidationException(
                    message_key="contact_onboarding.errors.invalid_vehicle_photo_path",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return photo_paths


class UpdateVehicleRequest(BaseModel):
    """Patch a vehicle."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    vehicle_type: VehicleType | None = None
    registration_number: str | None = Field(None, min_length=1, max_length=20)
    make: str | None = Field(None, max_length=100)
    model: str | None = Field(None, max_length=100)
    color: str | None = Field(None, max_length=50)
    photo_paths: list[str] | None = Field(None, max_length=10)
    fuel_type: VehicleFuelType | None = None

    @field_validator("photo_paths")
    @classmethod
    def validate_photo_paths(cls, photo_paths: list[str] | None) -> list[str] | None:
        """Validate storage paths for vehicle images."""
        if photo_paths is None:
            return photo_paths
        for path in photo_paths:
            if not path or len(path) > 500:
                raise ValidationException(
                    message_key="contact_onboarding.errors.invalid_vehicle_photo_path",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return photo_paths


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
    photo_paths: list[str] = Field(default_factory=list)
    fuel_type: str | None = None
    status: str
    rejection_reason: str | None = None
    parking_slot_id: str | None = None
    status_updated_at: str
    sort_order: int = 0
    created_at: str
    updated_at: str


class ReviewVehicleRequest(BaseModel):
    """Admin review of a resident vehicle registration request."""

    model_config = ConfigDict(extra="forbid")

    status: VehicleStatus
    parking_slot_id: str | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_review(self) -> ReviewVehicleRequest:
        """Enforce slot on approve and reason on reject."""
        if self.status == VehicleStatus.APPROVED and not self.parking_slot_id:
            raise ValidationException(
                message_key="contact_onboarding.errors.parking_slot_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.status == VehicleStatus.REJECTED and not self.rejection_reason:
            raise ValidationException(
                message_key="contact_onboarding.errors.rejection_reason_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.status == VehicleStatus.PENDING:
            raise ValidationException(
                message_key="contact_onboarding.errors.invalid_vehicle_review_status",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if self.status == VehicleStatus.REMOVED:
            raise ValidationException(
                message_key="contact_onboarding.errors.invalid_vehicle_review_status",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class VehicleModelOption(BaseModel):
    """Vehicle model picker option."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class VehicleBrandOption(BaseModel):
    """Vehicle brand with nested models."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    models: list[VehicleModelOption] = Field(default_factory=list)


class VehicleColorOption(BaseModel):
    """Vehicle color picker option."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class VehicleCatalogResponse(BaseModel):
    """Static vehicle catalog for brand/model/color pickers."""

    model_config = ConfigDict(extra="forbid")

    vehicle_type: str
    brands: list[VehicleBrandOption] = Field(default_factory=list)
    colors: list[VehicleColorOption] = Field(default_factory=list)


class CreateHouseholdMemberRequest(BaseModel):
    """Add a family member to a unit."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    first_name: str = Field(..., max_length=100)
    last_name: str | None = Field(None, max_length=100)
    gender: ContactGender | None = None
    phones: list[Phone] = Field(..., min_length=1, max_length=20)
    emails: list[Email] | None = Field(None, max_length=20)
    relationship: ContactUnitRelationship
    portal_access: bool = False

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone]) -> list[Phone]:
        """Validate exactly one primary phone."""
        return _validate_exactly_one_primary_phone(phones)


class UpdateHouseholdMemberRequest(BaseModel):
    """Patch a household member."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    relationship: ContactUnitRelationship | None = None
    portal_access: bool | None = None

    @model_validator(mode="after")
    def validate_non_empty_patch(self) -> UpdateHouseholdMemberRequest:
        """Require at least one field in the patch body."""
        if not self.model_dump(exclude_unset=True):
            raise ValidationException(
                message_key="contact_onboarding.errors.household_member_update_empty",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class SetDefaultUnitRequest(BaseModel):
    """Choose default login unit."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_id: str


class CompleteUnitStepRequest(BaseModel):
    """Complete a unit-scoped onboarding step (vehicles or household)."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_id: str


class CompleteStepRequest(BaseModel):
    """Mark an optional step complete (unit-scoped for vehicles/household)."""

    model_config = ConfigDict(extra="forbid")

    step_key: ContactOnboardingStep
    contact_unit_id: str | None = Field(
        None,
        description="Required when step_key is vehicles or household.",
    )

    @model_validator(mode="after")
    def validate_unit_step(self) -> CompleteStepRequest:
        """Unit-scoped steps require contact_unit_id."""
        unit_steps = {
            ContactOnboardingStep.VEHICLES.value,
            ContactOnboardingStep.HOUSEHOLD.value,
        }
        if self.step_key.value in unit_steps and not self.contact_unit_id:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_step_requires_contact_unit",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class AdminAssignUnitRequest(BaseModel):
    """Admin pre-allotment of a unit to a contact."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    is_primary: bool = False
    relationship: ContactUnitRelationship = ContactUnitRelationship.SELF


class ContactUnitAssignmentResponse(BaseModel):
    """One contact_units row with unit display fields (admin or resident list)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    unit_id: str
    project_id: str
    contact_id: str
    code: str = ""
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
    created_at: str | None = None


class OnboardingStepResponse(BaseModel):
    """Single wizard step."""

    step_key: str
    status: str
    completed_at: str | None = None


class UnitOnboardingStepResponse(BaseModel):
    """Single unit-scoped wizard step."""

    step_key: str
    status: str
    completed_at: str | None = None


class UnitOnboardingProgressResponse(BaseModel):
    """Per-unit vehicles/household progress."""

    contact_unit_id: str
    unit_id: str
    unit_code: str | None = None
    steps: list[UnitOnboardingStepResponse] = Field(default_factory=list)


class OnboardingStatusResponse(BaseModel):
    """Wizard progress."""

    setup_current_step: str | None
    current_contact_unit_id: str | None = None
    is_completed: bool
    steps: list[OnboardingStepResponse]
    unit_onboarding: list[UnitOnboardingProgressResponse] = Field(default_factory=list)


class HouseholdMemberResponse(BaseModel):
    """Family member linked to a unit."""

    contact_id: str
    contact_unit_id: str
    unit_id: str
    first_name: str | None = None
    last_name: str | None = None
    relationship: str
    portal_access: bool = False
    member_status: str
    phones: list[Any] = Field(default_factory=list)
    emails: list[Any] = Field(default_factory=list)
    invite_url: str | None = None
    invitation_sent_at: str | None = None
    invitation_expires_at: str | None = None
    invitation_status: str | None = None
    can_resend_invitation: bool = False


class AcceptHouseholdInvitationRequest(BaseModel):
    """Accept a household invitation via SMS deep-link token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)
    password: str = Field(..., description="Password for the new household member account")

    @classmethod
    @field_validator("password")
    def validate_password(cls, value: str) -> str:
        """Validate password meets minimum length requirements."""
        if len(value) < 6:
            raise ValidationException(
                message_key="errors.password_too_short",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        return value


class ValidateHouseholdInvitationRequest(BaseModel):
    """Validate a household invitation token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)


class DeclineHouseholdInvitationRequest(BaseModel):
    """Decline a household invitation via SMS deep-link token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)


class HouseholdInvitationValidateResponse(BaseModel):
    """Invitation details shown before acceptance."""

    invitee_name: str | None = None
    organization_name: str | None = None
    phone_masked: str | None = None
    expires_at: str | None = None


class HouseholdInvitationDeclineResponse(BaseModel):
    """Result after a household invitation is declined by the invitee."""

    contact_id: str
    organization_id: str
    contact_unit_id: str
    invitation_status: str
    contact_deleted: bool = False


class HouseholdInvitationUserInfo(BaseModel):
    """Authenticated household member after invitation acceptance."""

    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    phone_isd_code: str | None = None


class HouseholdInvitationAcceptResponse(BaseModel):
    """Result after a household invitation is accepted."""

    contact_id: str
    organization_id: str
    contact_unit_id: str
    member_status: str
    phone_masked: str | None = None
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    expires_at: datetime | None = None
    user: HouseholdInvitationUserInfo


class OnboardingReviewResponse(BaseModel):
    """Review screen aggregate."""

    contact: ContactDetailsResponse
    units: list[ContactUnitSummaryResponse]
    vehicles: list[VehicleResponse]
    household: list[HouseholdMemberResponse]
    steps: list[OnboardingStepResponse]
