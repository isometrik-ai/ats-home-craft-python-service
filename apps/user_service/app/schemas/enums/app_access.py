"""App access control enums. Mirror Postgres app_access_* types (ADR 0019)."""

from enum import Enum


class AppAccessStatus(str, Enum):
    """Last evaluation of a unit's resident-app hold."""

    BLOCKED = "blocked"
    NOTDUE = "notdue"
    WATCH = "watch"
    RELEASED = "released"
    CLEAR = "clear"


class AppAccessMatchMode(str, Enum):
    """How amount and days combine when both conditions are on."""

    ALL = "all"
    ANY = "any"


class AppAccessOverrideKind(str, Enum):
    """Staff one-off override for a unit."""

    HOLD = "hold"
    RELEASE = "release"


class AppAccessBlockedBy(str, Enum):
    """Who first put the unit on hold."""

    RULE = "rule"
    HAND = "hand"


__all__ = [
    "AppAccessBlockedBy",
    "AppAccessMatchMode",
    "AppAccessOverrideKind",
    "AppAccessStatus",
]
