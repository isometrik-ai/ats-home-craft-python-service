"""Enumeration values for community events domain."""

from enum import Enum

from apps.user_service.app.schemas.enums.property import FacilityType


class CommunityEventCategory(str, Enum):
    """Event category (Postgres community_event_category enum)."""

    SOCIAL = "social"
    WORKSHOP = "workshop"
    SPORTS = "sports"
    CULTURAL = "cultural"
    AGM = "agm"


class CommunityEventType(str, Enum):
    """Free or paid event (Postgres community_event_type enum)."""

    FREE = "free"
    PAID = "paid"


class CommunityEventChildTicketMode(str, Enum):
    """Child ticket pricing mode (Postgres community_event_child_ticket_mode enum)."""

    NOT_APPLICABLE = "not_applicable"
    FREE = "free"
    PRICED = "priced"


class CommunityEventPublishStatus(str, Enum):
    """Publish lifecycle (Postgres community_event_publish_status enum)."""

    DRAFT = "draft"
    PUBLISHED = "published"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CommunityEventRecordStatus(str, Enum):
    """Soft delete record status (Postgres community_event_record_status enum)."""

    ACTIVE = "active"
    DELETED = "deleted"


class CommunityEventBookingStatus(str, Enum):
    """Booking status (Postgres community_event_booking_status enum)."""

    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    CANCELLED = "cancelled"


class CommunityEventPaymentStatus(str, Enum):
    """Payment status (Postgres community_event_payment_status enum)."""

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    PAID = "paid"
    WAIVED = "waived"


class CommunityEventAuditAction(str, Enum):
    """Audit log action (Postgres community_event_audit_action enum)."""

    CREATED = "created"
    UPDATED = "updated"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    DELETED = "deleted"
    RESTORED = "restored"
    BOOKING_CREATED = "booking_created"
    BOOKING_CANCELLED = "booking_cancelled"
    MARKED_PAID = "marked_paid"
    MARKED_WAIVED = "marked_waived"
    GATE_VERIFIED = "gate_verified"


class CommunityEventPublishMode(str, Enum):
    """Create/publish intent."""

    DRAFT = "draft"
    PUBLISH = "publish"


class CommunityEventListTab(str, Enum):
    """Admin list tab filter."""

    ALL = "all"
    DRAFT = "draft"
    PUBLISHED = "published"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class ResidentEventTimeframe(str, Enum):
    """Resident list timeframe filter."""

    UPCOMING = "upcoming"
    PAST = "past"


ALLOWED_EVENT_FACILITY_TYPES: frozenset[str] = frozenset(
    {
        FacilityType.EVENTS.value,
        FacilityType.SPORTS.value,
        FacilityType.RECREATION.value,
        FacilityType.SERVICES.value,
    }
)

COMMUNITY_EVENT_CATEGORY_LABELS: dict[str, str] = {
    CommunityEventCategory.SOCIAL.value: "Social",
    CommunityEventCategory.WORKSHOP.value: "Workshop",
    CommunityEventCategory.SPORTS.value: "Sports",
    CommunityEventCategory.CULTURAL.value: "Cultural",
    CommunityEventCategory.AGM.value: "AGM",
}

COMMUNITY_EVENT_MAX_TITLE_LENGTH = 120
COMMUNITY_EVENT_MAX_DESCRIPTION_LENGTH = 2000
COMMUNITY_EVENT_MAX_GALLERY = 10
COMMUNITY_EVENT_MAX_MEDIA_BYTES = 5 * 1024 * 1024
COMMUNITY_EVENT_ALLOWED_MEDIA_MIMES = frozenset({"image/jpeg", "image/png"})
COMMUNITY_EVENT_EXPORT_MAX_ROWS = 10_000
CONTACTS_EXPORT_MAX_ROWS = 10_000

# ============================================================================
