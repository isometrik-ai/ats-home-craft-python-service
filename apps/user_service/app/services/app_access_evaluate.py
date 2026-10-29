"""Pure evaluation of a unit's app-access hold (ADR 0019)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from apps.user_service.app.schemas.enums.app_access import (
    AppAccessBlockedBy,
    AppAccessMatchMode,
    AppAccessOverrideKind,
    AppAccessStatus,
)
from apps.user_service.app.services.fee_configuration_labels import money, money_str

_ZERO = Decimal("0.00")


def format_inr(amount: Decimal) -> str:
    """Rupee amount for staff reason copy."""
    quantized = money(amount)
    text = f"{quantized:,.2f}".rstrip("0").rstrip(".")
    return f"₹{text}"


def evaluate_unit_status(
    *,
    outstanding: Decimal,
    days_past_due: int,
    has_open_invoice: bool,
    override_kind: str | None,
    enabled: bool,
    by_amount: bool,
    amount_threshold: Decimal,
    by_days: bool,
    days_threshold: int,
    match_mode: str,
) -> dict[str, Any]:
    """Return status, reason, blocked_by for one unit.

    Amount is at or above the threshold. Days is strictly more than the threshold.
    """
    # pylint: disable=too-complex
    owed = money(outstanding)
    threshold = money(amount_threshold)
    days = max(int(days_past_due or 0), 0)

    if override_kind == AppAccessOverrideKind.HOLD.value:
        if owed <= _ZERO:
            return _result(AppAccessStatus.CLEAR, "Nothing outstanding")
        return _result(
            AppAccessStatus.BLOCKED,
            "Blocked by hand",
            blocked_by=AppAccessBlockedBy.HAND.value,
        )
    if override_kind == AppAccessOverrideKind.RELEASE.value:
        return _result(
            AppAccessStatus.RELEASED,
            "Released by hand, rule skipped",
        )
    if owed <= _ZERO:
        return _result(AppAccessStatus.CLEAR, "Nothing outstanding")
    if not has_open_invoice or days == 0:
        return _result(
            AppAccessStatus.NOTDUE,
            "Due today, not overdue yet" if days == 0 else "Not yet due",
        )
    if not enabled:
        return _result(AppAccessStatus.CLEAR, "Blocking is switched off")
    if not by_amount and not by_days:
        return _result(AppAccessStatus.CLEAR, "No condition is switched on")

    hit_amount = by_amount and owed >= threshold
    hit_days = by_days and days > days_threshold
    both = by_amount and by_days
    if both:
        blocked = (
            (hit_amount and hit_days)
            if match_mode == AppAccessMatchMode.ALL.value
            else (hit_amount or hit_days)
        )
    else:
        blocked = hit_amount or hit_days

    parts: list[str] = []
    if by_amount:
        cmp_word = "at or above" if hit_amount else "below"
        parts.append(f"{format_inr(owed)} is {cmp_word} {format_inr(threshold)}")
    if by_days:
        cmp_word = "more" if hit_days else "not more"
        parts.append(f"{days} days past due is {cmp_word} than {days_threshold}")
    joiner = " and " if both and match_mode == AppAccessMatchMode.ALL.value else " or "
    why = joiner.join(parts) if parts else "Under the rule"
    return _result(
        AppAccessStatus.BLOCKED if blocked else AppAccessStatus.WATCH,
        why,
        blocked_by=AppAccessBlockedBy.RULE.value if blocked else None,
    )


def reason_key_for(*, status: str, blocked_by: str | None) -> str:
    """Resident copy key for the hold screen."""
    if status != AppAccessStatus.BLOCKED.value:
        return "app_access.hold.none"
    if blocked_by == AppAccessBlockedBy.HAND.value:
        return "app_access.hold.manual"
    return "app_access.hold.rule"


def empty_rule() -> dict[str, Any]:
    """Document when the project has no rule row."""
    return {
        "exists": False,
        "enabled": False,
        "by_amount": False,
        "amount_threshold": money_str(_ZERO),
        "by_days": False,
        "days_threshold": 15,
        "match_mode": AppAccessMatchMode.ALL.value,
        "version": 0,
    }


def _result(
    status: AppAccessStatus,
    reason: str,
    *,
    blocked_by: str | None = None,
) -> dict[str, Any]:
    """Pack the evaluate outcome."""
    return {
        "status": status.value,
        "reason": reason,
        "blocked_by": blocked_by if status == AppAccessStatus.BLOCKED else None,
    }
