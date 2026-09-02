"""Enumeration values for crm domain."""

from enum import Enum
from typing import Final


class ClientType(str, Enum):
    """Client type enumeration."""

    PERSON = "person"
    COMPANY = "company"


class ContactStatus(str, Enum):
    """Contact status enumeration (property management)."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class ClientStatus(str, Enum):
    """Client status enumeration (CRM legacy alias)."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PROSPECT = "prospect"
    DELETED = "deleted"


class ContactType(str, Enum):
    """Contact role type (Postgres public.contact_role_type / contact_roles.role_type)."""

    OWNER = "Owner"
    TENANT = "Tenant"
    FAMILY = "Family"
    GUEST = "Guest"
    VENDOR = "Vendor"
    STAFF = "Staff"


class ContactRoleStatus(str, Enum):
    """Lifecycle status for public.contact_roles (Postgres contact_role_status)."""

    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"


class Gender(str, Enum):
    """Gender stored on profile fields (Postgres public.gender enum)."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class BloodGroup(str, Enum):
    """Blood group stored on profile fields (Postgres public.blood_group enum)."""

    A_POSITIVE = "A+"
    A_NEGATIVE = "A-"
    B_POSITIVE = "B+"
    B_NEGATIVE = "B-"
    O_POSITIVE = "O+"
    O_NEGATIVE = "O-"
    AB_POSITIVE = "AB+"
    AB_NEGATIVE = "AB-"


class SetupStepStatus(str, Enum):
    """Wizard step status (Postgres setup_step_status enum)."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ContactOnboardingStep(str, Enum):
    """Contact onboarding wizard step keys."""

    COMPLETE_PROFILE = "complete_profile"
    SELECT_PROPERTIES = "select_properties"
    VEHICLES = "vehicles"
    HOUSEHOLD = "household"
    CHOOSE_UNIT = "choose_unit"
    REVIEW = "review"


class ContactUnitStatus(str, Enum):
    """Contact-unit link status."""

    PENDING = "pending"
    ACTIVE = "active"
    MOVED_OUT = "moved_out"


class ContactUnitRelationship(str, Enum):
    """Relationship of contact to unit."""

    SELF = "self"
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    SIBLING = "sibling"
    IN_LAW = "in_law"
    OTHER = "other"


class VehicleType(str, Enum):
    """Vehicle type."""

    TWO_WHEELER = "two_wheeler"
    FOUR_WHEELER = "four_wheeler"


class VehicleFuelType(str, Enum):
    """Vehicle fuel / request type (Non EV vs EV)."""

    NON_EV = "non_ev"
    EV = "ev"


class VehicleStatus(str, Enum):
    """Vehicle approval status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REMOVED = "removed"


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
    CRM_GRAPHITI_DLQ = "crm.graphiti.dlq.dev"
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
