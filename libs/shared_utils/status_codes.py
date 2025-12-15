"""Status codes for custom responses"""

from enum import Enum


class CustomStatusCode(str, Enum):
    """Status codes for custom responses"""

    # Success codes (2xxx)
    SUCCESS = "2000"
    CREATED = "2001"
    UPDATED = "2002"
    DELETED = "2003"
    PARTIAL_SUCCESS = "2004"
    NO_CONTENT = "2005"
    ACCEPTED = "2006"
    PROCESSING = "2007"

    # Auth codes (3xxx)
    AUTH_SUCCESS = "3000"
    LOGGED_OUT = "3001"
    PASSWORD_CHANGED = "3002"
    EMAIL_VERIFIED = "3003"

    # Error codes (4xxx)
    BAD_REQUEST = "4000"
    UNAUTHORIZED = "4001"
    FORBIDDEN = "4002"
    NOT_FOUND = "4003"
    VALIDATION_ERROR = "4004"
    DUPLICATE_ENTRY = "4005"
    CONFLICT = "4006"
    RATE_LIMITED = "4007"
    RATE_LIMIT_EXCEEDED = "4008"
    GONE = "4009"

    # Data errors (5xxx)
    INVALID_DATA = "5000"
    MISSING_FIELDS = "5001"
    INVALID_FORMAT = "5002"

    # Server errors (6xxx)
    INTERNAL_SERVER_ERROR = "6000"
    SERVICE_UNAVAILABLE = "6001"
    DATABASE_ERROR = "6002"
    EXTERNAL_SERVICE_ERROR = "6003"

    # Business logic errors (7xxx)
    BUSINESS_RULE_VIOLATION = "7000"
    INSUFFICIENT_FUNDS = "7001"
    QUOTA_EXCEEDED = "7002"

    # External service errors (8xxx)
    EXTERNAL_SERVICE_BAD_REQUEST = "8000"
    EXTERNAL_SERVICE_RATE_LIMIT = "8001"
