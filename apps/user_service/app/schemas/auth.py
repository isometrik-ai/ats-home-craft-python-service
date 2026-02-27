"""Auth Schemas Module"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from apps.user_service.app.schemas.common import Address, OrganizationBasicDetails
from apps.user_service.app.schemas.enums import (
    AuditingFrequency,
    ComplianceStandard,
    CustomIntegration,
    CustomizationOption,
    CustomReporting,
    EncryptionRequirement,
    ExpectedMembers,
    FirmSize,
    LoginMethod,
    PracticeArea,
    PreferredIntegration,
    SelectOrganizationType,
    SessionStatus,
    Specialization,
    SupportServiceOption,
    UserStatus,
    ValidateAccountTrigger,
    YourRole,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class SessionFilter(BaseModel):
    """Request model for Session Filter"""

    search: str | None = None
    session_status: SessionStatus | None = None
    login_method: LoginMethod | None = None
    limit: int = 20
    offset: int = 0


class AuthLogin(BaseModel):
    """Request model for user login"""

    email: EmailStr = Field(..., examples=["test@example.com"])
    password: str
    verification_id: str | None = Field(
        None, description="Verification code ID for 2FA (required if 2FA is enabled)"
    )
    verification_code: str | None = Field(
        None, description="Verification code for 2FA (required if 2FA is enabled)"
    )


class ValidateAccountRequest(BaseModel):
    """Request model for validating user account credentials and checking 2FA status"""

    trigger: ValidateAccountTrigger = Field(..., description="Trigger for authentication")
    email: EmailStr = Field(..., description="Email for authentication")
    password: str | None = Field(None, description="Password for authentication")


class ValidateAccountResponse(BaseModel):
    """Response model for validating user account credentials and checking 2FA status"""

    two_fa_enabled: bool = Field(..., description="Whether 2FA is enabled for the user")


class VerifyEmailResponse(BaseModel):
    """Response model for Verify Email operations"""

    message: str
    email_found: bool
    status: UserStatus | None = None
    can_login: bool


class SignupRequest(BaseModel):
    """Main User signup request data model"""

    email: EmailStr
    password: str = Field(..., min_length=6)
    salutation: Literal["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Adv."] | None = Field(
        None, description="Salutation for the user"
    )
    first_name: str = Field(..., min_length=2)
    last_name: str | None = Field(None, min_length=2)
    # job_title: Optional[str] = None
    phone_number: str | None = Field(None, min_length=1, max_length=20)
    phone_isd_code: str | None = Field(None, min_length=1, max_length=5)
    timezone: str | None = Field(default="UTC", max_length=3)
    verification_id: str = Field(
        ..., description="Verification code ID from verification-code/send endpoint"
    )
    verification_code: str = Field(..., description="Verification code to verify email")

    @classmethod
    @field_validator("password")
    def validate_password(cls, value) -> str:
        """Validate password meets minimum length requirements"""
        if len(value) < 6:
            raise ValidationException(
                message_key="errors.password_too_short",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        return value


class UserInfo(BaseModel):
    """User information model"""

    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    phone_isd_code: str | None = None
    timezone: str | None = Field(alias="timezone")
    org_setup_status_completed: bool = False


class OrganizationInfo(BaseModel):
    """Organization information model"""

    id: str
    name: str
    slug: str
    account_type: str
    plan_type: str
    status: str


class IsometrikDetails(BaseModel):
    """Isometrik details model"""

    user_id: str = Field(None, description="Isometrik user ID")
    token: str = Field(None, description="Isometrik token")
    license_key: str = Field(None, description="Isometrik license key")
    user_secret: str = Field(None, description="Isometrik user secret")
    app_secret: str = Field(None, description="Isometrik app secret")


class AuthResponse(BaseModel):
    """Response model for authentication operations"""

    access_token: str
    refresh_token: str
    expires_in: int
    expires_at: datetime
    user: UserInfo
    organizations: list[OrganizationBasicDetails] = Field(
        default_factory=list, description="List of user's active organizations"
    )


class RefreshSessionResponse(BaseModel):
    """Response model for refresh session operations"""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    expires_at: datetime | None = None
    token_refreshed: bool


class SignupResponse(BaseModel):
    """Response model for signup operations"""

    message: str = Field(..., description="Response message describing the operation result")
    data: dict


class SetPasswordRequest(BaseModel):
    """Request model for set password operations"""

    password: str


class ResetPasswordRequest(BaseModel):
    """Request model for reset password operations

    The token should be the access_token extracted from the password reset email URL.
    Email URL format: http://localhost:3000/#access_token=eyJhbGciOiJIUzI1NiIs...
                      &expires_at=1758009136&expires_in=3600...
                      &refresh_token=4bz3ixdhgdbv&token_type=bearer&type=recovery
    """

    token: str  # access_token from the password reset email URL
    new_password: str

    @classmethod
    @field_validator("new_password")
    def validate_password(cls, value):
        """Validate password meets minimum length requirements"""
        if len(value) < 6:
            raise ValidationException(
                message_key="errors.password_too_short",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        return value


class PasswordResponse(BaseModel):
    """Response model for set/reset password operations"""

    message: str = Field(..., description="Response message describing the operation result")


class ForgotPasswordRequest(BaseModel):
    """Request model for forgot password operations"""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Response model for forgot password operations"""

    message: str = Field(..., description="Response message describing the operation result")


class ChangePasswordRequest(BaseModel):
    """Request model for change password operations"""

    current_password: str = Field(..., description="Current password for verification")
    new_password: str = Field(..., min_length=6, description="New password to set")

    @classmethod
    @field_validator("new_password")
    def validate_password(cls, value):
        """Validate password meets minimum length requirements"""
        if len(value) < 6:
            raise ValidationException(
                message_key="errors.password_too_short",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        return value


class ChangePasswordResponse(BaseModel):
    """Response model for change password operations"""

    message: str = Field(..., description="Response message describing the operation result")


class SelectOrganizationRequest(BaseModel):
    """Request model for selecting organization"""

    organization_id: str = Field(..., description="Organization ID to select")
    user_type: SelectOrganizationType = Field(
        default=SelectOrganizationType.ORGANIZATION_MEMBER,
        description="Type of user to select organization for",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "organization_id": "1234567890",
                "user_type": "organization_member",
            }
        }
    }


class SelectOrganizationResponse(BaseModel):
    """Response model for selecting organization"""

    isometrik_details: IsometrikDetails | None = Field(
        None, description="Isometrik details for the organization"
    )


class ValidateTokenResponse(BaseModel):
    """Response model for token validation endpoint"""

    organization_id: str | None = Field(
        None, description="Organization ID associated with the session"
    )


class User(BaseModel):
    """User information."""

    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str | None = Field(None, min_length=1, max_length=50)
    phone_number: str | None = Field(None, min_length=1, max_length=20)
    phone_isd_code: str | None = Field(None, min_length=1, max_length=5)
    timezone: str | None = Field(None, min_length=1, max_length=50)


class TeamSetup(BaseModel):
    """Team setup information."""

    your_role: YourRole
    expected_members: ExpectedMembers


class ComplianceSecurity(BaseModel):
    """Compliance and security requirements."""

    required_compliance_standards: list[ComplianceStandard]
    data_retention_period: str = Field(..., description="Data retention period (e.g., '7 years')")
    auditing_frequency: AuditingFrequency
    encryption_requirements: list[EncryptionRequirement]
    compliance_officer_email: str = Field(
        ..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    )
    additional_requirements: str | None = None


class PrimaryContactInformation(BaseModel):
    """Primary contact information for enterprise firms."""

    contact_name: str = Field(..., min_length=1, max_length=100)
    contact_email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    contact_phone: str | None = Field(None, min_length=1, max_length=20)


class EnterpriseFeatures(BaseModel):
    """Enterprise-specific features and requirements."""

    expected_number_of_users: int = Field(
        ..., ge=100, description="Must be 100 or more for enterprise"
    )
    preferred_go_live_date: str | None = Field(None, pattern=r"^\d{2}/\d{2}/\d{4}$")
    support_service_options: list[SupportServiceOption]
    sla_requirements: list[str] = Field(default_factory=list)
    customization_options: list[CustomizationOption]
    custom_integration: list[CustomIntegration]
    custom_reporting: list[CustomReporting]
    primary_contact_information: PrimaryContactInformation


class CompanyData(BaseModel):
    """Company signup data model"""

    company_name: str
    company_website: str | None = None
    industry: str | None = None
    company_size: FirmSize | None = None
    description: str | None = None
    logo_url: str | None = None
    address: Address | None = None
    referral_source: str | None = None
    primary_practice_areas: list[PracticeArea] = Field(..., description="Primary practice areas")
    secondary_practice_areas: list[PracticeArea] | None = None
    specializations: list[Specialization] | None = None
    team_setup: TeamSetup | None = None
    preferred_integration: list[PreferredIntegration] | None = None
    need_help_importing_data: bool | None = False
    need_migration_assistance: bool | None = False
    compliance_security: ComplianceSecurity | None = None
    enterprise_features: EnterpriseFeatures | None = None

    @classmethod
    @field_validator("company_website")
    def validate_website(cls, value):
        """Enforce https scheme for company website."""
        if not value:
            return value

        if value.startswith("https://"):
            return value

        if value.startswith("http://"):
            return f"https://{value[len('http://') :]}"

        return f"https://{value}"

    @model_validator(mode="after")
    def validate_enterprise_features_and_practice_areas(self):
        """Validate enterprise features and practice areas based on firm size."""

        # Validate secondary practice areas don't overlap with primary ones
        if self.secondary_practice_areas is not None:
            overlap = set(self.primary_practice_areas) & set(self.secondary_practice_areas)
            if overlap:
                detail_string = ", ".join(overlap)
                raise ValidationException(
                    message_key="errors.practice_areas_overlap",
                    custom_code=CustomStatusCode.INVALID_DATA,
                    params={"practice_areas": detail_string},
                )

        return self


class SignupWizardResponse(BaseModel):
    """Signup wizard response."""

    data: dict[str, Any]
    message: str = Field(..., description="Response message describing the operation result")
    validation_passed: bool = True
