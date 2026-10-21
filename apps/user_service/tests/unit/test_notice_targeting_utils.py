"""Unit tests for notice All-recipient targeting helpers."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import (
    NoticeRecipientGroup,
    NoticeRecipientInputGroup,
)
from apps.user_service.app.utils.notice_targeting_utils import (
    NOTICE_PERSISTED_RECIPIENT_GROUPS,
    expand_recipient_groups,
    is_all_targeting_selection,
    normalize_recipient_groups_input,
)


def test_expand_all_selects_every_recipient_group():
    """Success: All expands to Owner, Tenant, Staff, and Security."""
    assert expand_recipient_groups(["All"]) == list(NOTICE_PERSISTED_RECIPIENT_GROUPS)
    assert expand_recipient_groups(["all"]) == list(NOTICE_PERSISTED_RECIPIENT_GROUPS)
    assert expand_recipient_groups([NoticeRecipientInputGroup.ALL]) == [
        "Owner",
        "Tenant",
        "Staff",
        "Security",
    ]


def test_expand_all_with_partial_selection_still_selects_every_group():
    """Success: All plus another group still means every recipient."""
    assert expand_recipient_groups(["All", "Owner"]) == list(NOTICE_PERSISTED_RECIPIENT_GROUPS)


def test_expand_keeps_explicit_groups_in_order():
    """Success: explicit groups are persisted as selected, without All."""
    assert expand_recipient_groups(["Tenant", "Owner", "Tenant"]) == ["Tenant", "Owner"]
    assert expand_recipient_groups([NoticeRecipientGroup.STAFF]) == ["Staff"]


def test_expand_empty_or_missing_groups():
    """Failure/empty: missing recipients stay empty so publish validation can fail."""
    assert expand_recipient_groups(None) == []
    assert expand_recipient_groups([]) == []


def test_is_all_targeting_selection():
    assert is_all_targeting_selection(["All"]) is True
    assert is_all_targeting_selection(["Owner"]) is False
    assert is_all_targeting_selection(None) is False


def test_normalize_recipient_groups_input_accepts_all_casing():
    """Success: all/ALL normalize to the canonical All shortcut."""
    assert normalize_recipient_groups_input(["all"]) == ["All"]
    assert normalize_recipient_groups_input(["ALL"]) == ["All"]
    assert normalize_recipient_groups_input(["Owner"]) == ["Owner"]
    assert normalize_recipient_groups_input(None) is None
