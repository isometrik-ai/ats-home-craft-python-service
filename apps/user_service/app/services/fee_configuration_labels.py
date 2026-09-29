"""Plain-language labels for fee heads. Computed on read, never stored."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.user_service.app.schemas.enums.fee_configuration import (
    FeeBillingCycle,
    FeeFrequency,
    FeeHeadCategory,
    FeeHeadKind,
)
from apps.user_service.app.schemas.enums.property import PropertyType

PROPERTY_TYPE_ORDER: tuple[str, ...] = (
    PropertyType.RESIDENTIAL.value,
    PropertyType.PLOTS.value,
    PropertyType.COMMERCIAL.value,
)
PROPERTY_TYPE_LABELS: dict[str, str] = {
    PropertyType.RESIDENTIAL.value: "Apartments",
    PropertyType.PLOTS.value: "Plots",
    PropertyType.COMMERCIAL.value: "Commercial",
}
KIND_ORDER: tuple[str, ...] = (
    FeeHeadKind.MAINTENANCE.value,
    FeeHeadKind.ELECTRICITY.value,
    FeeHeadKind.CLUB.value,
)
CATEGORY_BY_KIND: dict[str, str] = {
    FeeHeadKind.MAINTENANCE.value: FeeHeadCategory.MAINTENANCE.value,
    FeeHeadKind.ELECTRICITY.value: FeeHeadCategory.UTILITY.value,
    FeeHeadKind.CLUB.value: FeeHeadCategory.AMENITY.value,
}
CATEGORY_LABELS: dict[str, str] = {
    FeeHeadCategory.MAINTENANCE.value: "Maintenance",
    FeeHeadCategory.UTILITY.value: "Utility",
    FeeHeadCategory.AMENITY.value: "Amenity",
}
_MONTHS: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_FREQUENCY_LABELS: dict[str, str] = {
    FeeFrequency.MONTHLY.value: "Monthly",
    FeeFrequency.QUARTERLY.value: "Quarterly",
    FeeFrequency.HALF_YEARLY.value: "Half-yearly",
    FeeFrequency.ANNUAL.value: "Annual",
}
_PRO_RATA_PERIOD: dict[str, str] = {
    FeeFrequency.QUARTERLY.value: "quarter",
    FeeFrequency.HALF_YEARLY.value: "half-year",
    FeeFrequency.ANNUAL.value: "year",
}
SAMPLE_AREA_SQFT = Decimal("1000")
SAMPLE_GRID_KWH = Decimal("100")
SAMPLE_DG_KWH = Decimal("50")

LINE_DESCRIPTION_BY_KIND: dict[str, str] = {
    FeeHeadKind.MAINTENANCE.value: (
        "Housekeeping, security, lifts, landscaping and common-area power."
    ),
    FeeHeadKind.ELECTRICITY.value: (
        "Grid electricity and DG backup, billed on the monthly meter reading."
    ),
    FeeHeadKind.CLUB.value: "Clubhouse membership — pool, gym, courts and lounges.",
}
NAME_BY_KIND: dict[str, str] = {
    FeeHeadKind.MAINTENANCE.value: "Maintenance (CAM)",
    FeeHeadKind.ELECTRICITY.value: "Electricity",
    FeeHeadKind.CLUB.value: "Club charges",
}


def money(value: Decimal | str | int | float) -> Decimal:
    """Quantize to paise with half-up rounding."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_str(value: Decimal | str | int | float) -> str:
    """Decimal string with two places, for API money fields."""
    return f"{money(value):.2f}"


def half_up_rupee(value: Decimal) -> Decimal:
    """Nearest rupee, half away from zero. Do not use Python's built-in round."""
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_inr(value: Decimal | str | int | float) -> str:
    """en-IN rupee text with trailing zeros stripped (₹3.25, ₹1,500, ₹8.5)."""
    quantized = money(value)
    sign = "-" if quantized < 0 else ""
    quantized = abs(quantized)
    whole = int(quantized)
    cents = int((quantized - whole) * 100)
    grouped = _group_indian(whole)
    if cents == 0:
        return f"{sign}₹{grouped}"
    frac = f"{cents:02d}".rstrip("0")
    return f"{sign}₹{grouped}.{frac}"


def format_percent(value: Decimal | str | int | float) -> str:
    """Percent label with trailing zeros stripped (18%, 18.5%)."""
    quantized = money(value)
    text = f"{quantized:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _group_indian(whole: int) -> str:
    """Group a whole number with Indian digit separators."""
    digits = str(whole)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while head:
        parts.append(head[-2:])
        head = head[:-2]
    return ",".join(reversed(parts)) + "," + tail


def period_months(frequency: str, anchor_month: int) -> list[int]:
    """Month numbers (1–12) when a non-monthly fee is raised, starting at the anchor."""
    if frequency == FeeFrequency.QUARTERLY.value:
        steps = (0, 3, 6, 9)
    elif frequency == FeeFrequency.HALF_YEARLY.value:
        steps = (0, 6)
    else:
        steps = (0,)
    return [((anchor_month - 1 + step) % 12) + 1 for step in steps]


def anchor_month_for_cycle(cycle: str, requested: int | None) -> int | None:
    """Server-owned anchor. Calendar is January, financial is April."""
    if cycle == FeeBillingCycle.CALENDAR_YEAR.value:
        return 1
    if cycle == FeeBillingCycle.FINANCIAL_YEAR.value:
        return 4
    if cycle == FeeBillingCycle.CUSTOM.value:
        return requested
    return None


def billing_months_sentence(
    *,
    frequency: str,
    billing_cycle: str | None,
    anchor_month: int | None,
) -> tuple[list[int] | None, str | None]:
    """Month list and the editor sentence. Monthly fees have neither."""
    if frequency == FeeFrequency.MONTHLY.value or billing_cycle is None:
        return None, None
    if billing_cycle == FeeBillingCycle.PRO_RATA.value:
        period = _PRO_RATA_PERIOD.get(frequency, "period")
        return None, f"Raised in each unit's possession month, then every {period}."
    if anchor_month is None:
        return None, None
    months = period_months(frequency, anchor_month)
    names = ", ".join(_MONTHS[month - 1] for month in months)
    return months, f"Raised in {names} each year."


def frequency_label(
    *,
    kind: str,
    frequency: str,
    billing_cycle: str | None,
    anchor_month: int | None,
) -> str:
    """List column. Electricity is always monthly, on reading."""
    if kind == FeeHeadKind.ELECTRICITY.value:
        return "Monthly · on reading"
    base = _FREQUENCY_LABELS.get(frequency, frequency)
    if frequency == FeeFrequency.MONTHLY.value:
        return base
    if billing_cycle == FeeBillingCycle.PRO_RATA.value:
        return f"{base} · by possession"
    if anchor_month is None:
        return base
    months = period_months(frequency, anchor_month)
    names = ", ".join(_MONTHS[month - 1] for month in months)
    return f"{base} · {names}"


def _enabled_scopes(scopes: list[dict]) -> list[dict]:
    """Return enabled scopes in Apartments, Plots, Commercial order."""
    ordered = sorted(
        scopes,
        key=lambda row: PROPERTY_TYPE_ORDER.index(str(row["property_type"])),
    )
    return [row for row in ordered if row.get("enabled")]


def _lead_scope(scopes: list[dict]) -> dict | None:
    """Return the residential scope when it is enabled, otherwise the first enabled scope."""
    enabled = _enabled_scopes(scopes)
    if not enabled:
        return None
    for row in enabled:
        if row["property_type"] == PropertyType.RESIDENTIAL.value:
            return row
    return enabled[0]


def charge_summary(*, kind: str, scopes: list[dict], charge: dict | None) -> str:
    """Plain-language how-it-is-charged cell."""
    if kind == FeeHeadKind.MAINTENANCE.value:
        return _maintenance_summary(scopes)
    if kind == FeeHeadKind.ELECTRICITY.value:
        body = charge or {}
        return (
            f"{format_inr(body.get('grid_fixed_amount') or 0)} + "
            f"{format_inr(body.get('grid_unit_rate') or 0)}/kWh · DG "
            f"{format_inr(body.get('dg_fixed_amount') or 0)} + "
            f"{format_inr(body.get('dg_unit_rate') or 0)}/kWh"
        )
    amount = (charge or {}).get("amount") or 0
    return f"{format_inr(amount)} / month"


def _maintenance_summary(scopes: list[dict]) -> str:
    """Return the maintenance charge summary from the lead property type."""
    lead = _lead_scope(scopes)
    if lead is None:
        return "Not billed"
    rate = money(lead.get("rate_per_sqft") or 0)
    minimum = money(lead.get("minimum_amount") or 0)
    if minimum == 0:
        text = f"{format_inr(rate)} / sq ft · no minimum"
    else:
        text = f"{format_inr(rate)} / sq ft · min {format_inr(minimum)}"
    enabled = _enabled_scopes(scopes)
    varies = any(
        money(row.get("rate_per_sqft") or 0) != rate
        or money(row.get("minimum_amount") or 0) != minimum
        for row in enabled
    )
    if varies:
        text = f"{text} · varies by type"
    return text


def tax_label(*, applicable: bool, rate_percent: Decimal | str | None) -> str:
    """List column. An em dash when tax is off."""
    if not applicable or rate_percent is None:
        return "—"
    return format_percent(rate_percent)


def _sample_taxable(*, kind: str, scopes: list[dict], charge: dict | None) -> Decimal:
    """Return the sample taxable amount used in the late-fee sentence."""
    if kind == FeeHeadKind.MAINTENANCE.value:
        lead = _lead_scope(scopes)
        if lead is None:
            return Decimal("0")
        rate = money(lead.get("rate_per_sqft") or 0)
        minimum = money(lead.get("minimum_amount") or 0)
        return max(rate * SAMPLE_AREA_SQFT, minimum)
    if kind == FeeHeadKind.ELECTRICITY.value:
        body = charge or {}
        return (
            money(body.get("grid_fixed_amount") or 0)
            + money(body.get("dg_fixed_amount") or 0)
            + SAMPLE_GRID_KWH * money(body.get("grid_unit_rate") or 0)
            + SAMPLE_DG_KWH * money(body.get("dg_unit_rate") or 0)
        )
    return money((charge or {}).get("amount") or 0)


def late_fee_example(
    *,
    kind: str,
    scopes: list[dict],
    charge: dict | None,
    tax: dict,
    late_fee: dict,
) -> str:
    """Example sentence for the late-payment panel."""
    mode = str(late_fee.get("mode") or "none")
    if mode == "none":
        return "Dues simply carry forward."
    if mode == "flat":
        steps = sorted(late_fee.get("steps") or [], key=lambda step: int(step["days_overdue"]))
        if not steps:
            return "Dues simply carry forward."
        last = steps[-1]
        days = int(last["days_overdue"]) + 5
        return f"A bill {days} days overdue carries {format_inr(last['amount'])}."
    taxable = _sample_taxable(kind=kind, scopes=scopes, charge=charge)
    if tax.get("applicable") and tax.get("rate_percent") is not None:
        rate = money(tax["rate_percent"])
        bill = half_up_rupee(taxable * (Decimal("1") + rate / Decimal("100")))
    else:
        bill = half_up_rupee(taxable)
    annual = money(late_fee.get("annual_percent") or 0)
    monthly = half_up_rupee(bill * annual / Decimal("100") / Decimal("12"))
    return (
        f"A {format_inr(bill)} bill left unpaid picks up {format_inr(monthly)} "
        "for each month it stays overdue."
    )


def _plural(count: int, singular: str, plural: str) -> str:
    """Return the singular word when the count is one."""
    return singular if count == 1 else plural


def settings_summary(
    *,
    payment_retry_count: int,
    payment_retry_interval_days: int,
    pre_due_reminder_count: int,
    pre_due_reminder_interval_days: int,
) -> str:
    """Sentence under the four dunning numbers."""
    if payment_retry_count == 0:
        retry = "Failed payments are not retried."
    else:
        retry = (
            f"A failed payment is retried {payment_retry_count} "
            f"{_plural(payment_retry_count, 'time', 'times')}, "
            f"{payment_retry_interval_days} "
            f"{_plural(payment_retry_interval_days, 'day', 'days')} apart."
        )
    if pre_due_reminder_count == 0:
        reminder = "No reminders go out before the due date."
    else:
        reminder = (
            f"{pre_due_reminder_count} "
            f"{_plural(pre_due_reminder_count, 'reminder', 'reminders')} "
            "go out before the due date, "
            f"{pre_due_reminder_interval_days} "
            f"{_plural(pre_due_reminder_interval_days, 'day', 'days')} apart."
        )
    return f"{retry} {reminder} Exhausted retries escalate to the billing team."
