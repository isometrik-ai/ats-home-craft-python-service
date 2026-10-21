"""Expand notice create/update targeting sentinels (e.g. select all)."""

from __future__ import annotations

from collections.abc import Sequence

from apps.user_service.app.schemas.enums import NoticeRecipientGroup

NOTICE_TARGETING_ALL = "All"

NOTICE_PERSISTED_RECIPIENT_GROUPS: tuple[str, ...] = tuple(
    group.value for group in NoticeRecipientGroup
)


def _group_value(group: object) -> str:
    """Return the string value of a recipient group or raw input."""
    value = getattr(group, "value", group)
    return str(value).strip()


def is_all_targeting_selection(values: Sequence[object] | None) -> bool:
    """Return True when the list includes the reserved All sentinel."""
    if not values:
        return False
    return any(_group_value(value).lower() == NOTICE_TARGETING_ALL.lower() for value in values)


def normalize_recipient_group_value(value: object) -> object:
    """Map the All shortcut to its canonical API value."""
    if isinstance(value, str) and value.strip().lower() == NOTICE_TARGETING_ALL.lower():
        return NOTICE_TARGETING_ALL
    return value


def normalize_recipient_groups_input(recipient_groups: object) -> object:
    """Normalize All/all items in a recipient_groups payload."""
    if not isinstance(recipient_groups, list):
        return recipient_groups
    return [normalize_recipient_group_value(group) for group in recipient_groups]


def expand_recipient_groups(groups: Sequence[object] | None) -> list[str]:
    """Expand All to every persisted recipient group; otherwise unique ordered values."""
    if not groups:
        return []
    if is_all_targeting_selection(groups):
        return list(NOTICE_PERSISTED_RECIPIENT_GROUPS)
    seen: set[str] = set()
    expanded: list[str] = []
    for group in groups:
        value = _group_value(group)
        if not value or value in seen:
            continue
        seen.add(value)
        expanded.append(value)
    return expanded
