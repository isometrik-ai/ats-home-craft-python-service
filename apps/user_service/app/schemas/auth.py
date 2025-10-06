# pylint: disable=invalid-name,E0213,C0301
"""
Auth Schemas Module

"""

import base64
import random
import hashlib
from enum import Enum
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field, model_validator, EmailStr, field_validator
from fastapi import HTTPException, status

from apps.user_service.app.schemas import _bad_request

# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================


class AccountType(str, Enum):
    """Account type enumeration"""

    PERSONAL = "personal"
    BUSINESS = "business"


class PlanType(str, Enum):
    """Plan type enumeration"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


# ============================================================================
# REQUEST MODELS
# ============================================================================


class SessionFilter(BaseModel):
    """Request model for Session Filter"""

    search: Optional[str] = None
    session_status: Optional[str] = None
    login_method: Optional[str] = None
    limit: int = 20
    offset: int = 0


class AuthLogin(BaseModel):
    """Request model for user login"""

    email: EmailStr = Field(..., examples=["test@example.com"])
    password: str


class MemberBody(BaseModel):
    """Request model"""

    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    timezone: str = "UTC"


class VerifyEmailRequest(BaseModel):
    """Request model for Verify Email operations"""

    email: EmailStr


class VerifyEmailResponse(BaseModel):
    """Response model for Verify Email operations"""

    # status_code: int
    message: str
    email_found: bool
    status: Optional[str]  # 'active', 'suspended', or None
    can_login: bool


class SignupRequest(BaseModel):
    """Main User signup request data model"""

    email: EmailStr
    password: str = Field(..., min_length=6)
    first_name: str = Field(..., min_length=2)
    last_name: Optional[str] = Field(None, min_length=2)
    # job_title: Optional[str] = None
    phone: Optional[str] = None
    timezone: Optional[str] = Field(default="UTC",max_length=3)

    @classmethod
    @field_validator("first_name")
    def validate_name_fields(cls, v):
        """Validate name fields are not empty and meet minimum length requirements"""
        if not v or not v.strip():
            raise ValueError("Name fields cannot be empty")
        if len(v.strip()) < 2:
            raise ValueError("Name must be at least 2 characters long")
        return v.strip()

    @classmethod
    @field_validator("password")
    def validate_password(cls, v):
        """Validate password meets minimum length requirements"""
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters long")
        return v


    # @classmethod
    # @field_validator("company_data")
    # def validate_company_data(cls, v, info):
    #     """Validate company data for business account type."""
    #     values = info.data
    #     if values.get("account_type") == AccountType.BUSINESS and not v:
    #         raise ValueError("Company data is required for business accounts")
    #     return v


# ============================================================================
# RESPONSE MODELS
# ============================================================================


class UserInfo(BaseModel):
    """User information model"""

    id: str
    email: str
    # full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class OrganizationInfo(BaseModel):
    """Organization information model"""

    id: str
    name: str
    slug: str
    account_type: str
    plan_type: str
    status: str


class AuthResponse(BaseModel):
    """Response model for authentication operations"""

    access_token: str
    user: UserInfo


class SignupResponse(BaseModel):
    """Response model for signup operations"""

    # status_code: int
    message: str
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
    def validate_password(cls, v):
        """Validate password meets minimum length requirements"""
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters long")
        return v


class PasswordResponse(BaseModel):
    """Response model for set/reset password operations"""

    # status_code: int
    message: str

class ForgotPasswordRequest(BaseModel):
    """Request model for forgot password operations"""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Response model for forgot password operations"""

    # status_code: int
    message: str

# """
# Signup Wizard Schemas

# This module contains Pydantic schemas for the signup wizard API endpoint.
# Includes validation for firm information, practice areas, and enterprise features.

# Author: AI Assistant
# Date: 2024-12-19
# Last Updated: 2024-12-19
# """

# ============================================================================
# ENUMS
# ============================================================================

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


# ============================================================================
# SCHEMAS
# ============================================================================

class User(BaseModel):
    """User information."""
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)
    phone: Optional[str] = Field(None, min_length=1, max_length=20)
    timezone: Optional[str] = Field(None, min_length=1, max_length=50)

class TeamSetup(BaseModel):
    """Team setup information."""
    your_role: YourRole
    expected_members: ExpectedMembers


class ComplianceSecurity(BaseModel):
    """Compliance and security requirements."""
    required_compliance_standards: List[ComplianceStandard]
    data_retention_period: str = Field(..., description="Data retention period (e.g., '7 years')")
    auditing_frequency: AuditingFrequency
    encryption_requirements: List[EncryptionRequirement]
    compliance_officer_email: str = Field(
        ..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )
    additional_requirements: Optional[str] = None


class PrimaryContactInformation(BaseModel):
    """Primary contact information for enterprise firms."""
    contact_name: str = Field(..., min_length=1, max_length=100)
    contact_email: str = Field(..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    contact_phone: Optional[str] = Field(None, min_length=1, max_length=20)


class EnterpriseFeatures(BaseModel):
    """Enterprise-specific features and requirements."""
    expected_number_of_users: int = Field(
        ..., ge=100, description="Must be 100 or more for enterprise")
    preferred_go_live_date: Optional[str] = Field(None, pattern=r'^\d{2}/\d{2}/\d{4}$')
    support_service_options: List[SupportServiceOption]
    sla_requirements: List[str] = Field(default_factory=list)
    customization_options: List[CustomizationOption]
    custom_integration: List[CustomIntegration]
    custom_reporting: List[CustomReporting]
    primary_contact_information: PrimaryContactInformation



class CompanyData(BaseModel):
    """Company signup data model"""

    company_name: str
    company_website: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    max_users: Optional[int] = None
    referral_source: Optional[str] = None
    primary_practice_areas: List[PracticeArea] = Field(..., min_length=1, max_length=3)
    secondary_practice_areas: Optional[List[PracticeArea]] = None
    specializations: Optional[List[Specialization]] = None
    team_setup: Optional[TeamSetup] = None
    preferred_integration: Optional[List[PreferredIntegration]] = None
    need_help_importing_data: Optional[bool] = False
    need_migration_assistance: Optional[bool] = False
    compliance_security: Optional[ComplianceSecurity] = None
    enterprise_features: Optional[EnterpriseFeatures] = None


    @classmethod
    @field_validator("company_name")
    def validate_company_name(cls, v):
        """Validate non-empty company name with minimum 2 characters."""
        if not v or not v.strip():
            raise ValueError("Company name cannot be empty")
        if len(v.strip()) < 2:
            raise ValueError("Company name must be at least 2 characters long")
        return v.strip()

    @classmethod
    @field_validator("company_website")
    def validate_website(cls, v):
        """Ensure company website starts with http:// or https://."""
        if v and not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v

    @model_validator(mode='after')
    def validate_enterprise_features_and_practice_areas(self):
        """Validate enterprise features and practice areas based on firm size."""
        match self.company_size:
            # Solo Practitioner validations
            case FirmSize.SOLO_PRACTITIONER:
                if self.need_help_importing_data is not False:
                    _bad_request('need_help_importing_data is not applicable for Solo Practitioner')
                if self.need_migration_assistance is not False:
                    _bad_request('need_migration_assistance is not applicable for Solo Practitioner')
                if self.compliance_security is not None:
                    _bad_request('compliance_security is not applicable for Solo Practitioner')
                if self.preferred_integration is not None:
                    _bad_request('preferred_integration is not applicable for Solo Practitioner')
                if self.team_setup is not None:
                    _bad_request('team_setup is not applicable for Solo Practitioner')
                if self.enterprise_features is not None:
                    _bad_request('enterprise_features is not applicable for Solo Practitioner')

            # Small Firm (2-10 attorneys) validations
            case FirmSize.SMALL_FIRM:
                if self.enterprise_features is not None:
                    _bad_request('enterprise_features is not applicable for Small Firm (2-10 attorneys)')
                if self.compliance_security is not None:
                    _bad_request('compliance_security is not applicable for Small Firm (2-10 attorneys)')

            # Mid-Size/Large Firm (11-100 attorneys) validations
            case FirmSize.MID_SIZE_LARGE_FIRM:
                if self.enterprise_features is not None:
                    _bad_request('enterprise_features is not applicable for Mid-Size/Large Firm (11-100 attorneys)')

            # Enterprise Firm validations
            case FirmSize.ENTERPRISE_FIRM:
                if self.enterprise_features is None:
                    _bad_request('enterprise_features are required for Enterprise Firm (100+ attorneys)')

            case _:
                pass

        # Validate secondary practice areas don't overlap with primary ones
        if self.secondary_practice_areas is not None:
            overlap = set(self.primary_practice_areas) & set(self.secondary_practice_areas)
            if overlap:
                deatil_string = 'Secondary practice areas cannot overlap with primary ones: '
                deatil_string += str(list(overlap))
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=deatil_string
                )

        return self



class SignupWizardResponse(BaseModel):
    """Signup wizard response."""
    # status_code: int
    message: str
    data: Dict[str, Any]
    validation_passed: bool = True


def generate_pkce_pair():
    """Generates a PKCE code verifier and code challenge."""
    # Generate a secure random string for the code verifier (RFC 7636).
    # It must be between 43 and 128 characters long. We generate 32 bytes,
    # which is 43 URL-safe base64 characters.
    verifier_bytes = random.randbytes(32)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b'=').decode('utf-8')

    # Hash the code verifier using SHA256.
    challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()

    # Base64-URL-encode the SHA256 hash.
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b'=').decode('utf-8')

    return code_verifier, code_challenge

# Example usage
CODE_VERIFIER, CODE_CHALLENGE = generate_pkce_pair()

print(f"Code Verifier: {CODE_VERIFIER}")
print(f"Code Challenge: {CODE_CHALLENGE}")
