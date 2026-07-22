"""Fee calculation helpers (preview + invoice amount)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.user_service.app.schemas.enums import BillingFrequency, MeasurementUnit

SQFT_PER_SQM = 10.7639
SQFT_PER_GAJ = 9.0


def convert_major_to_minor(amount: float) -> int:
    """Convert major currency units to minor (paise)."""
    return int(round(amount * 100))


def convert_minor_to_major(amount_minor: int) -> float:
    """Convert minor units to major currency."""
    return round(amount_minor / 100, 2)


def convert_unit_area_to_sqft(area: float, unit: MeasurementUnit | str) -> float:
    """Convert an area expressed in the given unit into sqft."""
    unit_value = unit.value if isinstance(unit, MeasurementUnit) else unit
    if unit_value == MeasurementUnit.SQ_FT.value:
        return area
    if unit_value == MeasurementUnit.SQ_M.value:
        return area * SQFT_PER_SQM
    if unit_value == MeasurementUnit.GAJ.value:
        return area * SQFT_PER_GAJ
    return area


def convert_area_sqft_to_unit(area_sqft: float, unit: MeasurementUnit) -> float:
    """Convert sqft area into the rate measurement unit."""
    if unit == MeasurementUnit.SQ_FT:
        return area_sqft
    if unit == MeasurementUnit.SQ_M:
        return area_sqft / SQFT_PER_SQM
    if unit == MeasurementUnit.GAJ:
        return area_sqft / SQFT_PER_GAJ
    return area_sqft


def apply_billing_frequency(amount_minor: int, frequency: BillingFrequency | str) -> int:
    """Scale a monthly-equivalent amount to the billing frequency."""
    freq = frequency.value if isinstance(frequency, BillingFrequency) else frequency
    multipliers = {
        BillingFrequency.MONTHLY.value: 1,
        BillingFrequency.QUARTERLY.value: 3,
        BillingFrequency.HALF_YEARLY.value: 6,
        BillingFrequency.ANNUALLY.value: 12,
    }
    return int(round(amount_minor * multipliers.get(freq, 1)))


@dataclass(frozen=True)
class FeeRateInput:
    """Normalized rate inputs for calculation."""

    rate_amount_minor_per_unit: int
    measurement_unit: MeasurementUnit | str
    billing_frequency: BillingFrequency | str
    minimum_fee_minor: int = 0


@dataclass(frozen=True)
class FeePreviewResult:
    """Computed fee preview."""

    area_sqft: float
    area_in_rate_unit: float
    period_amount_minor: int
    minimum_applied: bool


def compute_period_fee_minor(
    *,
    area_sqft: float,
    rate: FeeRateInput,
) -> FeePreviewResult:
    """Compute charge for a billing period using ADR 0005 formula."""
    unit = (
        rate.measurement_unit
        if isinstance(rate.measurement_unit, MeasurementUnit)
        else MeasurementUnit(rate.measurement_unit)
    )
    area_in_rate_unit = convert_area_sqft_to_unit(area_sqft, unit)
    raw_minor = int(round(rate.rate_amount_minor_per_unit * area_in_rate_unit))
    period_minor = apply_billing_frequency(raw_minor, rate.billing_frequency)
    minimum_applied = period_minor < rate.minimum_fee_minor
    charge_minor = max(period_minor, rate.minimum_fee_minor)
    return FeePreviewResult(
        area_sqft=area_sqft,
        area_in_rate_unit=round(area_in_rate_unit, 4),
        period_amount_minor=charge_minor,
        minimum_applied=minimum_applied,
    )


def resolve_area_sqft_from_unit_row(row: dict[str, Any]) -> float | None:
    """Resolve billable area from a joined unit row."""
    if row.get("carpet_area_sqft") is not None:
        return float(row["carpet_area_sqft"])
    if row.get("area_sqft") is not None:
        return float(row["area_sqft"])
    if row.get("plot_size_sqft") is not None:
        return float(row["plot_size_sqft"])
    return None


def fee_rate_input_from_row(row: dict[str, Any]) -> FeeRateInput:
    """Build FeeRateInput from a project_fee_rates row."""
    return FeeRateInput(
        rate_amount_minor_per_unit=int(row["rate_amount_minor_per_unit"]),
        measurement_unit=row["measurement_unit"],
        billing_frequency=row["billing_frequency"],
        minimum_fee_minor=int(row.get("minimum_fee_minor") or 0),
    )
