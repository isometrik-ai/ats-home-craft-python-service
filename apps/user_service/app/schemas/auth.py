"""Auth Schemas Module"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class AccountType(str, Enum):
    """Account type enumeration"""

    PERSONAL = "personal"
    BUSINESS = "business"


class PlanType(str, Enum):
    """Plan type enumeration"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    TRIAL = "trial"


class SessionFilter(BaseModel):
    """Request model for Session Filter"""

    search: str | None = None
    session_status: str | None = None
    login_method: str | None = None
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


class ValidateAccountTrigger(str, Enum):
    """Trigger for validating user account credentials"""

    LOGIN = "LOGIN"
    SIGNUP = "SIGNUP"


class ValidateAccountRequest(BaseModel):
    """Request model for validating user account credentials and checking 2FA status"""

    trigger: ValidateAccountTrigger = Field(..., description="Trigger for authentication")
    email: EmailStr = Field(..., description="Email for authentication")
    password: str | None = Field(None, description="Password for authentication")


class ValidateAccountResponse(BaseModel):
    """Response model for validating user account credentials and checking 2FA status"""

    two_fa_enabled: bool = Field(..., description="Whether 2FA is enabled for the user")


class MemberBody(BaseModel):
    """Request model"""

    email: EmailStr
    full_name: str
    phone: str | None = None
    timezone: str = "UTC"


class VerifyEmailRequest(BaseModel):
    """Request model for Verify Email operations"""

    email: EmailStr


class VerifyEmailResponse(BaseModel):
    """Response model for Verify Email operations"""

    message: str
    email_found: bool
    status: Literal["active", "suspended"] | None = None
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
    phone: str | None = None
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
    phone: str | None = None
    timezone: str | None = Field(alias="timezone")
    org_setup_status_completed: bool = False
    organization_id: str | None = None


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
    isometrik_details: IsometrikDetails | None = None


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


class FirmSize(str, Enum):
    """Firm size options for signup wizard."""

    SOLO_PRACTITIONER = "Solo Practitioner"
    SMALL_FIRM = "Small Firm (2-10 attorneys)"
    MID_SIZE_LARGE_FIRM = "Mid-Size/Large Firm (11-100 attorneys)"
    ENTERPRISE_FIRM = "Enterprise Firm (100+ attorneys)"


class YourRole(str, Enum):
    """User role options in the firm."""

    PARTNER = "partner"
    ASSOCIATE = "associate"
    COUNSEL = "counsel"
    PARALEGAL = "paralegal"
    LEGAL_ASSISTANT = "legal-assistant"
    ADMINISTRATOR = "administrator"
    OTHER = "other"


class ExpectedMembers(str, Enum):
    """Expected team size options."""

    ONE = "1"
    TWO_TO_FIVE = "2-5"
    SIX_TO_TEN = "6-10"
    ELEVEN_TO_TWENTY_FIVE = "11-25"
    TWENTY_SIX_TO_FIFTY = "26-50"
    FIFTY_PLUS = "50+"


class ComplianceStandard(str, Enum):
    """Compliance standards options."""

    HIPAA = "HIPAA"
    GDPR = "GDPR"
    CCPA = "CCPA"
    SOX = "SOX"
    ISO_27001 = "ISO 27001"
    PCI_DSS = "PCI DSS"


class AuditingFrequency(str, Enum):
    """Auditing frequency options."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    BI_ANNUAL = "bi-annual"
    ANNUAL = "annual"


class EncryptionRequirement(str, Enum):
    """Encryption requirements options."""

    AES_256_ENCRYPTION = "AES-256 Encryption"
    TLS_1_3_FOR_DATA_IN_TRANSIT = "TLS 1.3 for Data in Transit"
    FULL_DISK_ENCRYPTION = "Full Disk Encryption"
    ENTERPRISE_KEY_MANAGEMENT = "Enterprise Key Management"


class SupportServiceOption(str, Enum):
    """Support service options."""

    DEDICATED_SUPPORT_24_7 = "24/7 Dedicated Support"
    DEDICATED_ACCOUNT_MANAGER = "Dedicated Account Manager"
    PRIORITY_TRAINING_ONBOARDING = "Priority Training & Onboarding"


class CustomizationOption(str, Enum):
    """Customization options."""

    CUSTOM_BRANDING = "Custom Branding"
    WHITE_LABELING = "White Labeling"
    ADVANCED_API_ACCESS = "Advanced API Access"


class CustomIntegration(str, Enum):
    """Custom integration options."""

    SALESFORCE_CRM = "Salesforce CRM"
    MICROSOFT_SHAREPOINT = "Microsoft SharePoint"
    WORKDAY = "Workday"
    NETSUTE = "NetSuite"
    CUSTOM_ERP_SYSTEM = "Custom ERP System"
    LEGACY_SYSTEMS = "Legacy Systems"


class CustomReporting(str, Enum):
    """Custom reporting options."""

    EXECUTIVE_DASHBOARD = "Executive Dashboard"
    COMPLIANCE_REPORTS = "Compliance Reports"
    PERFORMANCE_ANALYTICS = "Performance Analytics"
    FINANCIAL_REPORTS = "Financial Reports"
    RESOURCE_UTILIZATION_REPORTS = "Resource Utilization Reports"
    CUSTOM_KPI_TRACKING = "Custom KPI Tracking"


class PracticeArea(str, Enum):
    """Primary practice area options."""

    LITIGATION = "Litigation"
    CORPORATE_LAW = "Corporate Law"
    REAL_ESTATE = "Real Estate"
    FAMILY_LAW = "Family Law"
    CRIMINAL_LAW = "Criminal Law"
    PERSONAL_INJURY = "Personal Injury"
    EMPLOYMENT_LAW = "Employment Law"
    INTELLECTUAL_PROPERTY = "Intellectual Property"
    TAX_LAW = "Tax Law"
    IMMIGRATION_LAW = "Immigration Law"
    BANKRUPTCY = "Bankruptcy"
    ESTATE_PLANNING = "Estate Planning"
    ENVIRONMENTAL_LAW = "Environmental Law"
    HEALTHCARE_LAW = "Healthcare Law"
    SECURITIES_LAW = "Securities Law"


class Specialization(str, Enum):
    """Specialization options."""

    MEDIATION = "Mediation"
    ARBITRATION = "Arbitration"
    CLASS_ACTION = "Class Action"
    WHITE_COLLAR_DEFENSE = "White Collar Defense"
    MERGERS_ACQUISITIONS = "Mergers & Acquisitions"
    VENTURE_CAPITAL = "Venture Capital"
    REGULATORY_COMPLIANCE = "Regulatory Compliance"
    INTERNATIONAL_LAW = "International Law"


class PreferredIntegration(str, Enum):
    """Preferred integration options."""

    MICROSOFT_OFFICE_365 = "Microsoft Office 365"
    GOOGLE_WORKSPACE = "Google Workspace"
    MICROSOFT_OUTLOOK = "Microsoft Outlook"
    SALESFORCE = "Salesforce"
    SLACK = "Slack"
    MICROSOFT_TEAMS = "Microsoft Teams"
    DROPBOX = "Dropbox"
    BOX = "Box"
    ONEDRIVE = "OneDrive"
    QUICKBOOKS = "QuickBooks"
    CLIO = "Clio"
    MYCASE = "MyCase"
    PRACTICE_SUITE = "PracticeSuite"
    LAWPAY = "LawPay"


class User(BaseModel):
    """User information."""

    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str | None = Field(None, min_length=1, max_length=50)
    phone: str | None = Field(None, min_length=1, max_length=20)
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


class Address(BaseModel):
    """Address information."""

    address_line: str | None = Field(None, min_length=1, max_length=100)
    city: str | None = Field(None, min_length=1, max_length=100)
    state: str | None = Field(None, min_length=1, max_length=100)
    zip_code: str | None = Field(None, min_length=1, max_length=7)
    country: str = Field(..., min_length=1, max_length=100)


class Subscription(BaseModel):
    """Subscription information."""

    max_users: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of licensed seats for the organization",
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
    primary_practice_areas: list[PracticeArea] = Field(..., min_length=1, max_length=3)
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
        """Ensure company website starts with http:// or https://."""
        if value and not value.startswith(("http://", "https://")):
            return f"https://{value}"
        return value

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
