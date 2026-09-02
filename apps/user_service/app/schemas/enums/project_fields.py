"""Enumeration values for project fields domain."""

from enum import Enum


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
    USER = "user"


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


# ============================================================================
