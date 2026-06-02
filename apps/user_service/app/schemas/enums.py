"""Shared Enums Module.

This module contains all enumeration classes used across multiple schema modules.
This prevents circular import issues and provides a centralized location for all enums.
"""

from enum import Enum
from typing import Final

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


class ClientEnrichmentStatus(str, Enum):
    """Client enrichment status enumeration."""

    REQUESTED = "requested"
    COMPLETED = "completed"


class ClientEventType(str, Enum):
    """Kafka event type names emitted for client lifecycle changes."""

    CREATED = "clients.created"
    UPDATED = "clients.updated"
    DELETED = "clients.deleted"
    ENRICHMENT_REQUESTED = "clients.enrichment_requested"


class ContactEventType(str, Enum):
    """Kafka event type names emitted for contact lifecycle changes."""

    CREATED = "contacts.created"
    UPDATED = "contacts.updated"
    DELETED = "contacts.deleted"
    ENRICHMENT_REQUESTED = "contacts.enrichment_requested"


class CompanyEventType(str, Enum):
    """Kafka event type names emitted for company lifecycle changes."""

    CREATED = "companies.created"
    UPDATED = "companies.updated"
    DELETED = "companies.deleted"
    ENRICHMENT_REQUESTED = "companies.enrichment_requested"


class LeadEventType(str, Enum):
    """Kafka event type names emitted for lead lifecycle changes."""

    CREATED = "leads.created"
    UPDATED = "leads.updated"
    DELETED = "leads.deleted"


class OrganizationEventType(str, Enum):
    """Organization lifecycle events published to Kafka."""

    ENRICHMENT_REQUESTED = "organizations.enrichment.requested"


class KafkaTopics(str, Enum):
    """Kafka topics used by this service.

    Caller code should provide topic lists explicitly (no env/settings
    defaults) to avoid accidental publishing to the wrong topic.
    """

    CRM_EVENTS = "crm.events.dev"
    ORG_ENRICHMENT = "org.enrichment.dev"


class UiColor(str, Enum):
    """Everyday color names for UI (stages, badges, charts). Values are lowercase for API/DB."""

    BLACK = "black"
    WHITE = "white"
    GRAY = "gray"
    SILVER = "silver"
    RED = "red"
    MAROON = "maroon"
    ORANGE = "orange"
    YELLOW = "yellow"
    GOLD = "gold"
    GREEN = "green"
    OLIVE = "olive"
    LIME = "lime"
    TEAL = "teal"
    CYAN = "cyan"
    BLUE = "blue"
    NAVY = "navy"
    PURPLE = "purple"
    VIOLET = "violet"
    MAGENTA = "magenta"
    PINK = "pink"
    BROWN = "brown"
    BEIGE = "beige"
    CORAL = "coral"


class LeadStatus(str, Enum):
    """Lead status enumeration."""

    PROSPECT = "prospect"
    QUALIFIED = "qualified"
    CONSULTATION = "consultation"
    PROPOSAL = "proposal"
    CONVERTED = "converted"
    LOST = "lost"


# Per-stage default copy for new orgs (AI-facing; stored on ``lead_stages.description``).
DEFAULT_ORGANIZATION_LEAD_STAGES: Final[tuple[tuple[LeadStatus, UiColor, str], ...]] = (
    (
        LeadStatus.PROSPECT,
        UiColor.GRAY,
        "Initial interest identified; not yet qualified or engaged.",
    ),
    (
        LeadStatus.QUALIFIED,
        UiColor.BLUE,
        "Meets key qualification criteria; ready for active sales engagement.",
    ),
    (
        LeadStatus.CONSULTATION,
        UiColor.YELLOW,
        "Consultation or discovery session scheduled or completed to assess needs and fit.",
    ),
    (
        LeadStatus.PROPOSAL,
        UiColor.ORANGE,
        "Proposal or quotation shared; pending client review and decision.",
    ),
    (
        LeadStatus.CONVERTED,
        UiColor.GREEN,
        "Successfully closed; lead has converted into a client or signed engagement.",
    ),
    (
        LeadStatus.LOST,
        UiColor.RED,
        "Opportunity closed without conversion; no further action expected.",
    ),
)


class LeadsListMode(str, Enum):
    """List mode for ``POST /leads/list``: paginated list vs kanban grouped by stage."""

    LIST = "list"
    KANBAN = "kanban"


class DealType(str, Enum):
    """Deal classification for leads (stored as lowercase text in ``public.leads``)."""

    NEW_BUSINESS = "New Business"
    EXISTING_BUSINESS = "Existing Business"


class Priority(str, Enum):
    """Lead priority (stored as lowercase text in ``public.leads``)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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


class LeadCurrency(str, Enum):
    """ISO 4217 currency codes for lead amounts.

    Includes the current (active) alpha-3 currency codes plus the ISO 4217 "X" codes
    that represent funds/metals/special placeholders.
    """

    AED = "AED"
    AFN = "AFN"
    ALL = "ALL"
    AMD = "AMD"
    ANG = "ANG"
    AOA = "AOA"
    ARS = "ARS"
    AUD = "AUD"
    AWG = "AWG"
    AZN = "AZN"
    BAM = "BAM"
    BBD = "BBD"
    BDT = "BDT"
    BGN = "BGN"
    BHD = "BHD"
    BIF = "BIF"
    BMD = "BMD"
    BND = "BND"
    BOB = "BOB"
    BRL = "BRL"
    BSD = "BSD"
    BTN = "BTN"
    BWP = "BWP"
    BYN = "BYN"
    BZD = "BZD"
    CAD = "CAD"
    CDF = "CDF"
    CHF = "CHF"
    CLP = "CLP"
    CNY = "CNY"
    COP = "COP"
    CRC = "CRC"
    CUP = "CUP"
    CVE = "CVE"
    CZK = "CZK"
    DJF = "DJF"
    DKK = "DKK"
    DOP = "DOP"
    DZD = "DZD"
    EGP = "EGP"
    ERN = "ERN"
    ETB = "ETB"
    EUR = "EUR"
    FJD = "FJD"
    FKP = "FKP"
    GBP = "GBP"
    GEL = "GEL"
    GHS = "GHS"
    GIP = "GIP"
    GMD = "GMD"
    GNF = "GNF"
    GTQ = "GTQ"
    GYD = "GYD"
    HKD = "HKD"
    HNL = "HNL"
    HTG = "HTG"
    HUF = "HUF"
    IDR = "IDR"
    ILS = "ILS"
    INR = "INR"
    IQD = "IQD"
    IRR = "IRR"
    ISK = "ISK"
    JMD = "JMD"
    JOD = "JOD"
    JPY = "JPY"
    KES = "KES"
    KGS = "KGS"
    KHR = "KHR"
    KMF = "KMF"
    KPW = "KPW"
    KRW = "KRW"
    KWD = "KWD"
    KYD = "KYD"
    KZT = "KZT"
    LAK = "LAK"
    LBP = "LBP"
    LKR = "LKR"
    LRD = "LRD"
    LSL = "LSL"
    LYD = "LYD"
    MAD = "MAD"
    MDL = "MDL"
    MGA = "MGA"
    MKD = "MKD"
    MMK = "MMK"
    MNT = "MNT"
    MOP = "MOP"
    MRU = "MRU"
    MUR = "MUR"
    MVR = "MVR"
    MWK = "MWK"
    MXN = "MXN"
    MYR = "MYR"
    MZN = "MZN"
    NAD = "NAD"
    NGN = "NGN"
    NIO = "NIO"
    NOK = "NOK"
    NPR = "NPR"
    NZD = "NZD"
    OMR = "OMR"
    PAB = "PAB"
    PEN = "PEN"
    PGK = "PGK"
    PHP = "PHP"
    PKR = "PKR"
    PLN = "PLN"
    PYG = "PYG"
    QAR = "QAR"
    RON = "RON"
    RSD = "RSD"
    RUB = "RUB"
    RWF = "RWF"
    SAR = "SAR"
    SBD = "SBD"
    SCR = "SCR"
    SDG = "SDG"
    SEK = "SEK"
    SGD = "SGD"
    SHP = "SHP"
    SLE = "SLE"
    SOS = "SOS"
    SRD = "SRD"
    SSP = "SSP"
    STN = "STN"
    SYP = "SYP"
    SZL = "SZL"
    THB = "THB"
    TJS = "TJS"
    TMT = "TMT"
    TND = "TND"
    TOP = "TOP"
    TRY = "TRY"
    TTD = "TTD"
    TWD = "TWD"
    TZS = "TZS"
    UAH = "UAH"
    UGX = "UGX"
    USD = "USD"
    UYU = "UYU"
    UZS = "UZS"
    VES = "VES"
    VND = "VND"
    VUV = "VUV"
    WST = "WST"
    XAF = "XAF"
    XCD = "XCD"
    XOF = "XOF"
    XPF = "XPF"
    YER = "YER"
    ZAR = "ZAR"
    ZMW = "ZMW"
    ZWL = "ZWL"


class EntityType(str, Enum):
    """Entity type enumeration for custom fields."""

    COMPANY = "company"
    CONTACT = "contact"
    LEAD = "lead"
    PROJECT = "project"


class EntityListStatus(str, Enum):
    """List lifecycle state displayed in the UI tabs."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class EntityTable(str, Enum):
    """Database table names for CRM entity types."""

    CONTACTS = "contacts"
    COMPANIES = "companies"
    LEADS = "leads"


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


# ============================================================================
# CONTACTS IMPORT ENUMS
# ============================================================================


class ContactsImportFileType(str, Enum):
    """Supported file types for contacts bulk import."""

    CSV = "csv"
    XLSX = "xlsx"


class ContactsImportMode(str, Enum):
    """Row write mode for contacts import."""

    UPSERT = "upsert"
    INSERT_ONLY = "insert_only"


class ContactsImportDedupeKey(str, Enum):
    """Dedupe key when mode is upsert."""

    EMAIL = "email"


class ContactsImportJobStatus(str, Enum):
    """Import job lifecycle (stored on ``import_jobs.status``)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ContactsImportType(str, Enum):
    """``import_type`` / ``import_jobs.import_type`` for this pipeline."""

    CONTACTS = "contacts"


class ContactsImportEventAction(str, Enum):
    """Kafka metadata payload ``action`` (create vs retry)."""

    CREATE = "create"
    RETRY = "retry"


class ContactsImportKafkaStream(str, Enum):
    """Kafka topic and outbox ``event_type`` for contacts import (same string for both)."""

    CONTACTS_IMPORT_REQUESTED = "contacts.import.requested"
