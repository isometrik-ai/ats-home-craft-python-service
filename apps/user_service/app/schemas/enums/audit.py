"""Enumeration values for audit domain."""

from enum import Enum


class AuditLogActionType(str, Enum):
    """Audit log action filter values."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class AuditLogRiskLevel(str, Enum):
    """Audit log risk level filter values."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
