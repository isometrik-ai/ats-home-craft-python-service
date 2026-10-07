"""Enumeration values for resident buy and sell (ADR 0019)."""

from enum import Enum


class MarketplaceListingKind(str, Enum):
    """Postgres marketplace_listing_kind."""

    SALE = "sale"
    GIVEAWAY = "giveaway"


class MarketplaceListingStatus(str, Enum):
    """Postgres marketplace_listing_status."""

    DRAFT = "draft"
    LIVE = "live"
    SOLD = "sold"
    EXPIRED = "expired"
    REMOVED = "removed"


class MarketplaceItemCondition(str, Enum):
    """Postgres marketplace_item_condition."""

    LIKE_NEW = "like_new"
    LIGHTLY_USED = "lightly_used"
    WELL_USED = "well_used"
    NEEDS_REPAIR = "needs_repair"


class MarketplaceReportReason(str, Enum):
    """Postgres marketplace_report_reason."""

    NOT_ALLOWED = "not_allowed"
    BUSINESS_OR_BROKER = "business_or_broker"
    SOLD_BUT_LISTED = "sold_but_listed"
    SOMETHING_ELSE = "something_else"


class MarketplaceReportStatus(str, Enum):
    """Postgres marketplace_report_status."""

    OPEN = "open"
    UPHELD = "upheld"
    DISMISSED = "dismissed"


class MarketplaceSaleRating(str, Enum):
    """Postgres marketplace_sale_rating."""

    SMOOTH = "smooth"
    FINE = "fine"
    HAD_TROUBLE = "had_trouble"


class MarketplaceSort(str, Enum):
    """Listing feed sort."""

    NEWEST = "newest"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    CLOSEST = "closest"


class MarketplacePriceBand(str, Enum):
    """Fixed price chips."""

    FREE = "free"
    UNDER_5000 = "under_5000"
    BAND_5000_20000 = "5000_20000"
    ABOVE_20000 = "above_20000"


class MarketplaceWhere(str, Enum):
    """Browse scope relative to the caller's unit."""

    MY_TOWER = "my_tower"
    MY_SOCIETY = "my_society"
    NEARBY = "nearby"


class MarketplaceMineStatus(str, Enum):
    """My listings tabs."""

    ALL = "all"
    LIVE = "live"
    DRAFT = "draft"
    SOLD = "sold"
    PAST = "past"


class MarketplaceListingAction(str, Enum):
    """Seller actions that share POST /listings/{id}/actions."""

    PUBLISH = "publish"
    REMOVE = "remove"
    RESTORE = "restore"
    RENEW = "renew"
    RELIST = "relist"


class MarketplaceReportDecision(str, Enum):
    """Committee decision on one report."""

    UPHOLD = "uphold"
    DISMISS = "dismiss"
