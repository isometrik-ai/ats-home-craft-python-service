"""Enumeration values for resident services domain."""

from enum import Enum


class MoveEventType(str, Enum):
    """Move event type (Postgres move_event_type enum)."""

    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"


class MoveEventListBucket(str, Enum):
    """List filter buckets for GET /move-events (omit = All)."""

    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"


class TenantRequestStatus(str, Enum):
    """Tenant request header status (Postgres tenant_request_status enum)."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_REVIEW = "pending_review"
    AWAITING_RESUBMISSION = "awaiting_resubmission"
    READY_TO_APPROVE = "ready_to_approve"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class TenantRequestDocumentType(str, Enum):
    """Required document slots on a tenant request."""

    ID_PROOF = "id_proof"
    RENTAL_AGREEMENT = "rental_agreement"
    POLICE_VERIFICATION = "police_verification"


class ContactUnitDocumentType(str, Enum):
    """Ownership documents linked to a contact-unit allotment.

    Validated in the API; persisted as text in contact_unit_documents.document_type.
    To add a type: extend this enum and migrate the CHECK constraint on the column.
    """

    LEASE = "lease"
    TAX_RECEIPT = "tax_receipt"
    OWNERSHIP_CERTIFICATE = "ownership_certificate"
    OTHER = "other"


class TenantRequestDocumentStatus(str, Enum):
    """Per-document review status."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class TenantRequestEventType(str, Enum):
    """Append-only tenant request timeline events."""

    CREATED = "created"
    SUBMITTED = "submitted"
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_VERIFIED = "document_verified"
    DOCUMENT_REJECTED = "document_rejected"
    RESUBMITTED = "resubmitted"
    READY_TO_APPROVE = "ready_to_approve"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    TENANT_INVITE_SENT = "tenant_invite_sent"


class TenantRequestListBucket(str, Enum):
    """Admin list filter buckets for GET /tenant-requests."""

    PENDING_REVIEW = "pending_review"
    AWAITING_RESUBMISSION = "awaiting_resubmission"
    READY_TO_APPROVE = "ready_to_approve"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES: tuple[TenantRequestDocumentType, ...] = (
    TenantRequestDocumentType.ID_PROOF,
    TenantRequestDocumentType.RENTAL_AGREEMENT,
    TenantRequestDocumentType.POLICE_VERIFICATION,
)


# ============================================================================
# DAILY HELP ENUMS — mirror Postgres daily_help_* enums (ADR 0013)
# ============================================================================


class DailyHelpStatus(str, Enum):
    """Daily help profile lifecycle status."""

    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class DailyHelpCategoryStatus(str, Enum):
    """Project category catalog row status."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class DailyHelpDocumentType(str, Enum):
    """Document slot type on a daily help profile."""

    PHOTO = "photo"
    ID_PROOF = "id_proof"
    POLICE_VERIFICATION = "police_verification"
    OTHER = "other"


class DailyHelpEventType(str, Enum):
    """Append-only daily help audit events."""

    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUBMITTED = "resubmitted"
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    DOCUMENT_ADDED = "document_added"
    DOCUMENT_REMOVED = "document_removed"
    PASS_ISSUED = "pass_issued"
    PASS_CANCELLED = "pass_cancelled"
    DELETED = "deleted"
    RESTORED = "restored"
    HOUSEHOLD_LINKED = "household_linked"
    HOUSEHOLD_REMOVED = "household_removed"
    ATTENDANCE_MARKED_ABSENT = "attendance_marked_absent"


class DailyHelpActorType(str, Enum):
    """Who recorded a daily help event."""

    STAFF = "staff"
    RESIDENT = "resident"
    SYSTEM = "system"


class DailyHelpHouseholdLinkStatus(str, Enum):
    """Resident household link status."""

    ACTIVE = "active"
    REMOVED = "removed"


class DailyHelpAvailabilityPeriod(str, Enum):
    """Free-time slot period label."""

    MORNING = "morning"
    EVENING = "evening"
    FULL_DAY = "full_day"
    OTHER = "other"


class DailyHelpRatingTrait(str, Enum):
    """Resident rating trait tags."""

    VERY_PUNCTUAL = "very_punctual"
    QUITE_REGULAR = "quite_regular"
    EXCEPTIONAL_SERVICE = "exceptional_service"
    GREAT_ATTITUDE = "great_attitude"


DEFAULT_DAILY_HELP_CATEGORY_NAMES: tuple[str, ...] = (
    "Maid",
    "Cook",
    "Nanny",
    "Driver",
    "Milk Delivery",
    "Newspaper",
    "Laundry",
    "Gardener",
    "Car Cleaner",
    "Other",
)


# ============================================================================
# WALK-IN ENUMS — mirror Postgres walk_in_* enums (ADR 0008)
# ============================================================================


class WalkInStatus(str, Enum):
    """Walk-in visit header status (Postgres walk_in_status enum)."""

    AWAITING = "awaiting"
    APPROVED = "approved"
    ENTERED = "entered"
    EXITED = "exited"
    CANCELLED = "cancelled"


class WalkInVisitUnitStatus(str, Enum):
    """Per-flat approval status on a walk-in visit."""

    AWAITING = "awaiting"
    APPROVED = "approved"
    REJECTED = "rejected"


class WalkInEventType(str, Enum):
    """Append-only walk-in timeline events."""

    REQUESTED = "requested"
    VISIT_UNIT_APPROVED = "visit_unit_approved"
    VISIT_UNIT_REJECTED = "visit_unit_rejected"
    ENTERED = "entered"
    EXITED = "exited"
    CANCELLED = "cancelled"


class WalkInActorType(str, Enum):
    """Who recorded a walk-in timeline event."""

    STAFF = "staff"
    RESIDENT = "resident"
    SYSTEM = "system"


# ============================================================================
