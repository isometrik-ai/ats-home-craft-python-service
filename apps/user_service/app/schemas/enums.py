"""Shared Enums Module.

This module contains all enumeration classes used across multiple schema modules.
This prevents circular import issues and provides a centralized location for all enums.
"""

from enum import Enum

# ============================================================================
# USER & AUTHENTICATION ENUMS
# ============================================================================


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


# ============================================================================
# INVITE ENUMS
# ============================================================================


class InviteStatus(str, Enum):
    """Enumeration for invitation statuses."""

    PENDING = "pending"
    ACCEPTED = "accepted"


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


# ============================================================================
# CLIENT ENUMS
# ============================================================================


class ClientType(str, Enum):
    """Client type enumeration."""

    PERSON = "person"
    COMPANY = "company"


class ClientStatus(str, Enum):
    """Client status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PROSPECT = "prospect"
    DELETED = "deleted"


class ClientUserStatus(str, Enum):
    """Client user status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class LeadStatus(str, Enum):
    """Lead status enumeration."""

    PROSPECT = "prospect"
    QUALIFIED = "qualified"
    CONSULTATION = "consultation"
    PROPOSAL = "proposal"
    CONVERTED = "converted"
    LOST = "lost"


class IntakeStage(str, Enum):
    """Intake stage enumeration."""

    INITIAL_CONTACT = "Initial Contact"
    QUALIFICATION = "Qualification"
    CONFLICT_CHECK = "Conflict Check"
    CONSULTATION = "Consultation"
    PROPOSAL = "Proposal"
    ONBOARDING = "Onboarding"
    COMPLETED = "Completed"


class AddressType(str, Enum):
    """Address type enumeration."""

    WORK = "work"
    HOME = "home"
    BILLING = "billing"
    SHIPPING = "shipping"
    OTHER = "other"


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
# ISOMETRIK ENUMS
# ============================================================================


class IsometrikRole(str, Enum):
    """Isometrik user role enumeration."""

    CLIENT = "client"
    OWNER = "owner"
    MEMBER = "member"


# ============================================================================
# PROJECT ENUMS
# ============================================================================


class ProjectStatus(str, Enum):
    """Project status enumeration."""

    DISCOVERY = "discovery"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class ProjectPriority(str, Enum):
    """Project priority enumeration."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class BillingType(str, Enum):
    """Billing type enumeration."""

    TIME_AND_MATERIALS = "time_and_materials"
    FIXED_PRICE = "fixed_price"
    MONTHLY_RETAINER = "monthly_retainer"
    MILESTONE_BASED = "milestone_based"
    HYBRID = "hybrid"
    VALUE_BASED = "value_based"


class PaymentTerms(str, Enum):
    """Payment terms enumeration."""

    NET_15 = "Net 15"
    NET_30 = "Net 30"
    NET_45 = "Net 45"
    NET_60 = "Net 60"
    DUE_ON_RECEIPT = "Due on receipt"


class RepositoryPlatform(str, Enum):
    """Repository platform enumeration."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    AZURE_DEVOPS = "azure_devops"


class IntegrationType(str, Enum):
    """Integration type enumeration."""

    JIRA = "jira"
    ASANA = "asana"
    LINEAR = "linear"
    CLICKUP = "clickup"
    MONDAY = "monday"
    TRELLO = "trello"
    NOTION = "notion"


class SyncDirection(str, Enum):
    """Sync direction enumeration."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BIDIRECTIONAL = "bidirectional"


class ConnectionStatus(str, Enum):
    """Connection status enumeration."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"
    PENDING_AUTH = "pending_auth"


# ============================================================================
# CUSTOM FIELDS ENUMS
# ============================================================================


class SupportedCurrency(str, Enum):
    """Supported currency codes for currency field type."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"
    CHF = "CHF"
    CNY = "CNY"
    INR = "INR"
    SGD = "SGD"
    AED = "AED"
    BRL = "BRL"
    MXN = "MXN"
    ZAR = "ZAR"
    KRW = "KRW"
    NZD = "NZD"
    SEK = "SEK"
    NOK = "NOK"
    DKK = "DKK"
    HKD = "HKD"


class EntityType(str, Enum):
    """Entity type enumeration for custom fields."""

    COMPANY = "company"
    CONTACT = "contact"
    LEAD = "lead"


class FieldType(str, Enum):
    """Field type enumeration for custom fields."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    YES_NO = "yes_no"
    URL = "url"
    LONG_TEXT = "long_text"
    RICH_TEXT = "rich_text"
    DROPDOWN = "dropdown"
    RANGE_SLIDER = "range_slider"
    CURRENCY = "currency"
    FILE_UPLOAD = "file_upload"
    IMAGE = "image"
    ADDRESS = "address"
    OBJECT = "object"
    LIST = "list"


class AcceptedFileTypes(str, Enum):
    """Accepted file type options for file_upload fields (UI dropdown options)."""

    ANY = "any"  # All Files
    PDF_ONLY = "pdf_only"
    DOCUMENTS = "documents"  # .pdf, .doc, .docx
    SPREADSHEETS = "spreadsheets"  # .xls, .xlsx, .csv
    HTML = "html"
    IMAGES = "images"
    CUSTOM = "custom"  # Custom extensions
