"""Enumeration values for billing notices domain."""

from enum import Enum


class PushPlatform(str, Enum):
    """Mobile/web push platform enumeration."""

    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


# ============================================================================
# NOTICE BOARD ENUMS — mirror Postgres notice_* enums
# ============================================================================


class NoticeStatus(str, Enum):
    """Notice lifecycle status (Postgres notice_status enum)."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    LIVE = "live"
    DELETED = "deleted"


class NoticeCategory(str, Enum):
    """Notice category (Postgres notice_category enum)."""

    MAINTENANCE = "maintenance"
    SECURITY = "security"
    EVENT = "event"
    BILLING = "billing"
    EMERGENCY = "emergency"
    GENERAL = "general"


class NoticeScopeType(str, Enum):
    """Audience scope (Postgres notice_scope_type enum)."""

    WHOLE_SOCIETY = "whole_society"
    BY_TOWER = "by_tower"


class NoticeRecipientGroup(str, Enum):
    """Recipient group (Postgres notice_recipient_group enum)."""

    OWNER = "Owner"
    TENANT = "Tenant"
    STAFF = "Staff"
    SECURITY = "Security"


class NoticeRecipientInputGroup(str, Enum):
    """Create/update recipient selection, including the All shortcut."""

    ALL = "All"
    OWNER = "Owner"
    TENANT = "Tenant"
    STAFF = "Staff"
    SECURITY = "Security"


class NoticePinDuration(str, Enum):
    """Banner pin duration (Postgres notice_pin_duration enum)."""

    MANUAL = "manual"
    HOURS_24 = "24h"
    HOURS_72 = "72h"


class NoticePublishMode(str, Enum):
    """Create/update publish intent."""

    DRAFT = "draft"
    NOW = "now"
    SCHEDULE = "schedule"
    SERIES_START = "series_start"


class NoticeSeriesStatus(str, Enum):
    """Recurring template lifecycle (Postgres notice_series_status)."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NoticeRecurrenceFrequency(str, Enum):
    """Recurrence frequency (Postgres notice_recurrence_frequency)."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"


class NoticeRecurrenceEndType(str, Enum):
    """Recurrence end rule (Postgres notice_recurrence_end_type)."""

    NEVER = "never"
    ON_DATE = "on_date"
    AFTER_COUNT = "after_count"


class NoticeSeriesAction(str, Enum):
    """PATCH action for recurring templates."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


class NoticeListStatus(str, Enum):
    """Admin list tab filter."""

    ALL = "all"
    LIVE = "live"
    SCHEDULED = "scheduled"
    DELETED = "deleted"


NOTICE_CATEGORY_LABELS: dict[str, str] = {
    NoticeCategory.MAINTENANCE.value: "Maintenance",
    NoticeCategory.SECURITY.value: "Security",
    NoticeCategory.EVENT.value: "Event",
    NoticeCategory.BILLING.value: "Billing",
    NoticeCategory.EMERGENCY.value: "Emergency",
    NoticeCategory.GENERAL.value: "General",
}

NOTICE_MAX_PIN_SLOTS = 6
NOTICE_MAX_TITLE_LENGTH = 70
NOTICE_MAX_DESCRIPTION_LENGTH = 600
NOTICE_MAX_ATTACHMENTS = 4
NOTICE_MAX_CONTACT_RECIPIENTS = 200
NOTICE_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
NOTICE_ALLOWED_ATTACHMENT_MIMES = frozenset({"image/jpeg", "image/png"})
NOTICE_SCHEDULE_MAX_DAYS = 62


# ============================================================================
