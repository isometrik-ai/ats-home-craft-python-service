"""Fee configuration enums. Mirror Postgres fee_* types (ADR 0018)."""

from enum import Enum


class FeeHeadKind(str, Enum):
    """Immutable fee head key."""

    MAINTENANCE = "maintenance"
    ELECTRICITY = "electricity"
    CLUB = "club"


class FeeHeadCategory(str, Enum):
    """Derived category. Not accepted on write."""

    MAINTENANCE = "maintenance"
    UTILITY = "utility"
    AMENITY = "amenity"


class FeeHeadStatus(str, Enum):
    """Whether the next billing run includes the fee head."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class FeeFrequency(str, Enum):
    """How often a fee is billed."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    ANNUAL = "annual"


class FeeBillingCycle(str, Enum):
    """Which months a non-monthly fee lands in."""

    CALENDAR_YEAR = "calendar_year"
    FINANCIAL_YEAR = "financial_year"
    CUSTOM = "custom"
    PRO_RATA = "pro_rata"


class FeeStartRule(str, Enum):
    """When the fee starts being raised."""

    FIRST_OF_NEXT_MONTH = "first_of_next_month"
    UNIT_POSSESSION_DATE = "unit_possession_date"
    SPECIFIC_DATE = "specific_date"


__all__ = [
    "FeeBillingCycle",
    "FeeFrequency",
    "FeeHeadCategory",
    "FeeHeadKind",
    "FeeHeadStatus",
    "FeeStartRule",
]
