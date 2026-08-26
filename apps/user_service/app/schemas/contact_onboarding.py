"""Contact onboarding schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contacts import (
    CommunicationPreferences,
    ContactDetailsResponse,
    FlexibleOptionalDate,
)
from apps.user_service.app.schemas.enums import (
    BloodGroup,
    ContactUnitRelationship,
    Gender,
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


class ContactPropertyProjectSummary(BaseModel):
    """Project display fields embedded on a contact property row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str
    name: str
    developer_name: str
    city: str
    state: str
    country: str
    address_line_1: str
    address_line_2: str | None = None
    pin_code: str
    latitude: float | None = None
    longitude: float | None = None
    property_types: list[str] = Field(default_factory=list)


class ContactPropertyUnitResponse(BaseModel):
    """Unit row nested under a project in the properties list."""

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
    assign_date: str | None = None
    created_at: str | None = None
    parking_entitlement: int = Field(default=0, ge=0)


class ContactPropertyProjectGroupResponse(BaseModel):
    """Properties grouped by project for onboarding step 2."""

    model_config = ConfigDict(extra="ignore")

    project: ContactPropertyProjectSummary
    units: list[ContactPropertyUnitResponse] = Field(default_factory=list)


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
    assign_date: str | None = None
    created_at: str | None = None
    parking_entitlement: int = Field(default=0, ge=0)
    project: ContactPropertyProjectSummary | None = None


class ConfirmPropertiesRequest(BaseModel):
    """Confirm selected properties after the profile step."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_ids: list[str] = Field(..., min_length=1)
    default_contact_unit_id: str | None = Field(
        None,
        description="Optional default login unit when confirming multiple properties.",
    )


class CompleteOnboardingRequest(BaseModel):
    """Finalize onboarding for all or a subset of active properties."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional active contact_unit ids to finalize now. Omitted ids are moved back "
            "to pending and can be claimed later via POST /properties/claim."
        ),
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
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = Field(None, max_length=500)
    gender: Gender | None = None
    blood_group: BloodGroup | None = None
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


class ResubmitVehicleRequest(BaseModel):
    """Patch a rejected vehicle and return it to the admin review queue."""

    model_config = ConfigDict(extra="forbid")

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


class VehicleUnitSummary(BaseModel):
    """Unit summary on admin vehicle request rows."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str
    unit_label: str | None = None
    location_label: str | None = None
    property_type: str | None = None
    config_kind: str | None = None
    floor_level_number: int | None = None
    floor_display_name: str | None = None
    config_display_label: str | None = None
    tower_id: str | None = None
    config_id: str | None = None
    status: str
    sort_order: int = 0


class VehicleParkingFacilitySummary(BaseModel):
    """Parking facility summary on admin vehicle request rows."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    location_type: str | None = None
    floor_level: str | None = None
    wing: str | None = None
    tower_id: str | None = None


class VehicleParkingAllotmentSummary(BaseModel):
    """Assigned parking slot summary on admin vehicle request rows."""

    model_config = ConfigDict(extra="ignore")

    id: str
    slot_number: int
    status: str
    facility: VehicleParkingFacilitySummary | None = None


class VehicleOwnerSummary(BaseModel):
    """Unit owner summary on admin vehicle request rows."""

    model_config = ConfigDict(extra="ignore")

    contact_id: str
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None
    profile_photo_url: str | None = None


class VehicleReviewerSummary(BaseModel):
    """Org member who approved or rejected a vehicle request."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar_url: str | None = None


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
    approved_by_user_id: str | None = None
    rejected_by_user_id: str | None = None
    parking_slot_id: str | None = None
    status_updated_at: str
    sort_order: int = 0
    created_at: str
    updated_at: str
    unit: VehicleUnitSummary | None = None
    owner: VehicleOwnerSummary | None = None
    parking_allotment: VehicleParkingAllotmentSummary | None = None
    approved_by: VehicleReviewerSummary | None = None
    rejected_by: VehicleReviewerSummary | None = None


class ReviewVehicleRequest(BaseModel):
    """Admin review of a resident vehicle registration request."""

    model_config = ConfigDict(extra="forbid")

    status: VehicleStatus
    parking_slot_id: str | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_review(self) -> ReviewVehicleRequest:
        """Enforce reason on reject; parking slot is optional on approve."""
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


class DeleteProjectVehicleRequest(BaseModel):
    """Admin removes a project vehicle with a documented reason."""

    model_config = ConfigDict(extra="forbid")

    rejection_reason: str = Field(..., min_length=1, max_length=500)


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
    gender: Gender | None = None
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


class AdminAssignUnitRequest(BaseModel):
    """Admin pre-allotment of a unit to a contact."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    assign_date: date
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
    assign_date: str | None = None
    created_at: str | None = None


class OnboardingPromptResponse(BaseModel):
    """Optional home-screen action for the contact (nothing here blocks app usage)."""

    model_config = ConfigDict(extra="allow")

    type: str
    contact_unit_id: str | None = None
    unit_id: str | None = None


class OnboardingStatusResponse(BaseModel):
    """Onboarding prompts for the authenticated contact."""

    profile_complete: bool = False
    pending_unit_count: int = 0
    active_unit_count: int = 0
    requires_default_unit: bool = False
    prompts: list[OnboardingPromptResponse] = Field(default_factory=list)
    is_completed: bool


class OnboardingReviewResponse(BaseModel):
    """Review screen aggregate before finalize."""

    contact: ContactDetailsResponse
    units: list[ContactUnitSummaryResponse]
    vehicles: list[VehicleResponse]
    household: list[HouseholdMemberResponse]


class CompleteOnboardingResponse(OnboardingStatusResponse):
    """Result after POST /contact-onboarding/complete."""

    completed_contact_unit_ids: list[str] = Field(default_factory=list)
    deferred_contact_unit_ids: list[str] | None = None


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


class HouseholdSummaryCountsResponse(BaseModel):
    """Dashboard counts for the household manage screen."""

    model_config = ConfigDict(extra="ignore")

    unit_id: str
    family_count: int = 0
    daily_help_count: int = 0
    vehicles_count: int = 0
    tenant_count: int = 0


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
    invitation_status: str | None = None
    already_accepted: bool = False


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


class AddHouseholdMemberResponse(BaseModel):
    """Result of adding a household member."""

    model_config = ConfigDict(extra="ignore")

    contact_id: str
    contact_unit_id: str
    member_status: str
    invitation_id: str | None = None
    phone_masked: str | None = None
    invite_url: str | None = None


class RemoveHouseholdMemberResponse(BaseModel):
    """Result of removing a household member."""

    model_config = ConfigDict(extra="forbid")

    contact_unit_id: str
    contact_id: str
    contact_deleted: bool = False


class HouseholdInvitationSentResponse(BaseModel):
    """Result after creating or resending a household invitation."""

    model_config = ConfigDict(extra="ignore")

    invitation_id: str
    contact_unit_id: str
    member_status: str
    phone_masked: str | None = None
    invite_url: str | None = None


class OnboardingStatusApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/status."""

    status: str
    message: str
    statusCode: int
    code: str
    data: OnboardingStatusResponse


class OnboardingReviewApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/review."""

    status: str
    message: str
    statusCode: int
    code: str
    data: OnboardingReviewResponse


class CompleteOnboardingApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/complete."""

    status: str
    message: str
    statusCode: int
    code: str
    data: CompleteOnboardingResponse


class ContactPropertiesListApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/properties."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[ContactPropertyProjectGroupResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ClaimPropertiesApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/properties/confirm and /claim."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ClaimPropertiesResponse


class ContactProfileApiResponse(BaseModel):
    """API envelope for GET/PATCH /contact-onboarding/profile."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ContactDetailsResponse


class VehicleCatalogApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/vehicles/options."""

    status: str
    message: str
    statusCode: int
    code: str
    data: VehicleCatalogResponse


class VehicleListApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/vehicles."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[VehicleResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class VehicleDetailApiResponse(BaseModel):
    """API envelope for vehicle detail and mutation endpoints."""

    status: str
    message: str
    statusCode: int
    code: str
    data: VehicleResponse


class ContactOnboardingMessageApiResponse(BaseModel):
    """API envelope for endpoints that return success without a data payload."""

    status: str
    message: str
    statusCode: int
    code: str


class HouseholdMemberListApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/household."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[HouseholdMemberResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class HouseholdSummaryApiResponse(BaseModel):
    """API envelope for GET /contact-onboarding/household/summary."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdSummaryCountsResponse


class AddHouseholdMemberApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/household."""

    status: str
    message: str
    statusCode: int
    code: str
    data: AddHouseholdMemberResponse


class HouseholdMemberApiResponse(BaseModel):
    """API envelope for household member update and invitation revoke."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdMemberResponse


class HouseholdInvitationValidateApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/household/invitations/validate."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdInvitationValidateResponse


class HouseholdInvitationAcceptApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/household/invitations/accept."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdInvitationAcceptResponse


class HouseholdInvitationDeclineApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/household/invitations/decline."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdInvitationDeclineResponse


class HouseholdInvitationSentApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/household/{id}/resend-invitation."""

    status: str
    message: str
    statusCode: int
    code: str
    data: HouseholdInvitationSentResponse


class RemoveHouseholdMemberApiResponse(BaseModel):
    """API envelope for DELETE /contact-onboarding/household/{id}."""

    status: str
    message: str
    statusCode: int
    code: str
    data: RemoveHouseholdMemberResponse


class SetDefaultUnitApiResponse(BaseModel):
    """API envelope for POST /contact-onboarding/default-unit."""

    status: str
    message: str
    statusCode: int
    code: str
    data: ContactUnitSummaryResponse
