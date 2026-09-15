"""Enumeration values for billing notices domain."""

from enum import Enum


class BillingFrequency(str, Enum):
    """Maintenance fee billing frequency (Postgres billing_frequency enum)."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    ANNUALLY = "annually"


class FeeStartTrigger(str, Enum):
    """When unit maintenance billing starts (Postgres fee_start_trigger enum)."""

    ONBOARDING_DATE = "onboarding_date"
    POSSESSION_DATE = "possession_date"
    FIRST_OF_NEXT_MONTH = "first_of_next_month"
    AFTER_ONE_YEAR = "after_one_year"
    AFTER_DAYS = "after_days"


class BillingCycleType(str, Enum):
    """Project billing cycle alignment (Postgres billing_cycle_type enum)."""

    CALENDAR_YEAR = "calendar_year"
    FINANCIAL_YEAR = "financial_year"
    PRO_RATA = "pro_rata"


class ExhaustedRetryAction(str, Enum):
    """Action after payment retries are exhausted."""

    ESCALATE_TO_BILLING_TEAM = "escalate_to_billing_team"


class MaintenanceFeeInvoiceStatus(str, Enum):
    """Maintenance fee invoice status (Postgres maintenance_fee_invoice_status enum)."""

    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    OVERDUE = "overdue"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class MaintenanceFeeInvoiceEventType(str, Enum):
    """Maintenance fee invoice timeline events."""

    ISSUED = "issued"
    REMINDER_SENT = "reminder_sent"
    PAYMENT_ATTEMPTED = "payment_attempted"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


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
NOTICE_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
NOTICE_ALLOWED_ATTACHMENT_MIMES = frozenset({"image/jpeg", "image/png"})
NOTICE_SCHEDULE_MAX_DAYS = 62


# ============================================================================
