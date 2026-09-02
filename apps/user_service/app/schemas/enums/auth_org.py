"""Enumeration values for auth org domain."""

from enum import Enum


class UserStatus(str, Enum):
    """Enumeration for user account status"""

    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class AccountType(str, Enum):
    """Account type enumeration"""

    PERSONAL = "personal"
    BUSINESS = "business"


class UserEventStatus(str, Enum):
    """User event statuses."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanType(str, Enum):
    """Plan type enumeration"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    TRIAL = "trial"


class ValidateAccountTrigger(str, Enum):
    """Trigger for validating user account credentials"""

    LOGIN = "LOGIN"
    SIGNUP = "SIGNUP"


class SelectOrganizationType(str, Enum):
    """Type of user for select-organization;
    determines which membership source to validate against."""

    CLIENT = "client"
    ORGANIZATION_MEMBER = "organization_member"


# ============================================================================
# ORGANIZATION ENUMS
# ============================================================================


class DeleteRequestStatus(str, Enum):
    """Enumeration for organization delete request statuses."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class OrganizationStatus(str, Enum):
    """Enumeration for organization statuses."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    DELETED = "deleted"


class OrganizationMemberStatus(str, Enum):
    """Enumeration for organization member statuses."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"
    PENDING = "pending"
    INVITED = "invited"
    DELETED = "deleted"


class OrganizationMemberRole(str, Enum):
    """Enumeration for organization roles (owner or member)."""

    OWNER = "owner"
    MEMBER = "member"


class EmailTemplateType(str, Enum):
    """Email template kind: full layout shell or trigger body fragment."""

    TRIGGER = "trigger"
    LAYOUT = "layout"


class EmailTemplateStatus(str, Enum):
    """Email template publish state (enforced in API; stored as text in DB)."""

    DRAFT = "draft"
    PUBLISHED = "published"


class SuperadminOrganizationListStatus(str, Enum):
    """Derived / filter status for superadmin organization listing."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_DELETION = "pending_deletion"


class SuperadminOrganizationListSortField(str, Enum):
    """Allowed sort columns for superadmin organization list."""

    CREATED_AT = "created_at"
    NAME = "name"
    MEMBER_COUNT = "member_count"


class SuperadminOrganizationListSortOrder(str, Enum):
    """Sort direction for superadmin organization list."""

    ASC = "asc"
    DESC = "desc"


# ============================================================================
# INVITE ENUMS
# ============================================================================


class InviteStatus(str, Enum):
    """Enumeration for invitation statuses."""

    PENDING = "pending"
    ACCEPTED = "accepted"


class HouseholdInvitationStatus(str, Enum):
    """Household invitation status (Postgres household_invitation_status enum)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DECLINED = "declined"


class HouseholdMemberStatus(str, Enum):
    """Derived household member status for API responses."""

    INVITED = "invited"
    JOINED = "joined"
    REVOKED = "revoked"


class InviteAcceptAuthKind(str, Enum):
    """How the invitee was authenticated when accepting an organization invitation."""

    NEW_SIGNUP = "new_signup"
    EXISTING_WITH_PASSWORD = "existing_with_password"
    EXISTING_PASSWORDLESS = "existing_passwordless"


INVITE_ACCEPT_MSG_KEY_NEW_ACCOUNT = "invitations.success.invitation_accepted_new_account"
INVITE_ACCEPT_MSG_KEY_SIGNED_IN = "invitations.success.invitation_accepted_signed_in"

INVITE_ACCEPT_SUCCESS_MESSAGE_KEYS: dict[InviteAcceptAuthKind, str] = {
    InviteAcceptAuthKind.NEW_SIGNUP: INVITE_ACCEPT_MSG_KEY_NEW_ACCOUNT,
    InviteAcceptAuthKind.EXISTING_WITH_PASSWORD: INVITE_ACCEPT_MSG_KEY_SIGNED_IN,
    InviteAcceptAuthKind.EXISTING_PASSWORDLESS: INVITE_ACCEPT_MSG_KEY_SIGNED_IN,
}


# ============================================================================
# ADMIN ACCESS MANAGEMENT ENUMS
# ============================================================================


class SessionStatus(str, Enum):
    """Enumeration for session statuses."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    TERMINATED = "terminated"
    LOGGED_OUT = "logged_out"


class LoginMethod(str, Enum):
    """Enumeration for login methods."""

    PASSWORD = "password"
    SSO = "sso"
    MFA = "mfa"
    UNKNOWN = "unknown"


class RoleType(str, Enum):
    """Enumeration for role types."""

    SYSTEM = "system"
    CUSTOM = "custom"


# ============================================================================
# TEAM ENUMS
# ============================================================================


class TeamRoles(str, Enum):
    """Team member roles"""

    LEAD = "LEAD"
    MEMBER = "MEMBER"
    TECH_LEAD = "TECH LEAD"
    PROJECT_LEAD = "PROJECT LEAD"


# ============================================================================
# VERIFICATION ENUMS
# ============================================================================


class VerificationType(str, Enum):
    """Verification type enumeration"""

    EMAIL = "EMAIL"
    PHONE_NUMBER = "PHONE_NUMBER"


class VerificationTrigger(str, Enum):
    """Verification trigger/purpose enumeration"""

    SIGNUP_EMAIL_VERIFICATION = "SIGNUP_EMAIL_VERIFICATION"
    SIGNUP_PHONE_VERIFICATION = "SIGNUP_PHONE_VERIFICATION"
    EMAIL_UPDATE = "EMAIL_UPDATE"
    PHONE_NUMBER_UPDATE = "PHONE_NUMBER_UPDATE"


# ============================================================================
# SIGNUP WIZARD ENUMS
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
    TECHNOLOGY = "Technology"
    HEALTHCARE = "Healthcare"
    LEGAL = "Legal"
    FINANCE = "Finance"
    MANUFACTURING = "Manufacturing"
    RETAIL = "Retail"
    CONSULTING = "Consulting"
    AGRICULTURE = "Agriculture"
    FARMING = "Farming"


# ============================================================================
