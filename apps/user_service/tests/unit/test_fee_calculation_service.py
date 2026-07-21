"""Unit tests for fee calculation helpers."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import BillingFrequency, MeasurementUnit
from apps.user_service.app.services.fee_calculation_service import (
    FeeRateInput,
    apply_billing_frequency,
    compute_period_fee_minor,
    convert_area_sqft_to_unit,
    convert_major_to_minor,
    convert_minor_to_major,
    convert_unit_area_to_sqft,
)


def test_convert_major_minor_roundtrip() -> None:
    """Major/minor currency conversion should round-trip."""
    assert convert_major_to_minor(12.34) == 1234
    assert convert_minor_to_major(1234) == 12.34


def test_apply_billing_frequency_quarterly() -> None:
    """Quarterly billing should multiply the monthly-equivalent amount by three."""
    assert apply_billing_frequency(1000, BillingFrequency.QUARTERLY) == 3000


def test_compute_period_fee_applies_minimum_floor() -> None:
    """Minimum fee floor should override a lower computed amount."""
    rate = FeeRateInput(
        rate_amount_minor_per_unit=10,
        measurement_unit=MeasurementUnit.SQ_FT,
        billing_frequency=BillingFrequency.MONTHLY,
        minimum_fee_minor=50000,
    )
    result = compute_period_fee_minor(area_sqft=100, rate=rate)
    assert result.period_amount_minor == 50000
    assert result.minimum_applied is True


def test_compute_period_fee_from_area() -> None:
    """Fee amount should scale with unit area and rate."""
    rate = FeeRateInput(
        rate_amount_minor_per_unit=100,
        measurement_unit=MeasurementUnit.SQ_FT,
        billing_frequency=BillingFrequency.MONTHLY,
        minimum_fee_minor=0,
    )
    result = compute_period_fee_minor(area_sqft=1500, rate=rate)
    assert result.period_amount_minor == 150000


def test_area_unit_conversions() -> None:
    """Area conversions between sq m and sq ft should be reversible."""
    sqft = convert_unit_area_to_sqft(10, MeasurementUnit.SQ_M)
    assert round(convert_area_sqft_to_unit(sqft, MeasurementUnit.SQ_M), 2) == 10.0
