"""Unit tests for fee configuration schema validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import (
    BillingFrequency,
    FeeStartTrigger,
    MeasurementUnit,
    UnitConfigKind,
)
from apps.user_service.app.schemas.fee_configuration import (
    FeeConfigurationRate,
    FeeConfigurationSettings,
)


def test_fee_configuration_settings_rejects_invalid_retry_interval():
    """Retry interval must match allowed dropdown values."""
    with pytest.raises(ValidationError, match="retry_interval_days"):
        FeeConfigurationSettings(retry_interval_days=5)


def test_fee_configuration_settings_rejects_invalid_reminder_interval():
    """Reminder interval must match allowed dropdown values."""
    with pytest.raises(ValidationError, match="reminder_interval_days"):
        FeeConfigurationSettings(reminder_interval_days=4)


def test_fee_configuration_settings_accepts_allowed_intervals():
    """Allowed interval values pass validation."""
    settings = FeeConfigurationSettings(retry_interval_days=7, reminder_interval_days=1)
    assert settings.retry_interval_days == 7
    assert settings.reminder_interval_days == 1


def test_fee_configuration_rate_requires_offset_for_after_days():
    """after_days trigger requires start_offset_days."""
    with pytest.raises(ValidationError, match="start_offset_days is required"):
        FeeConfigurationRate(
            unit_config_kind=UnitConfigKind.APARTMENT,
            rate_amount=10.0,
            measurement_unit=MeasurementUnit.SQ_FT,
            billing_frequency=BillingFrequency.MONTHLY,
            fee_start_trigger=FeeStartTrigger.AFTER_DAYS,
            start_offset_days=None,
        )


def test_fee_configuration_rate_rejects_offset_for_other_triggers():
    """start_offset_days is only valid with after_days trigger."""
    with pytest.raises(ValidationError, match="start_offset_days is only allowed"):
        FeeConfigurationRate(
            unit_config_kind=UnitConfigKind.APARTMENT,
            rate_amount=10.0,
            measurement_unit=MeasurementUnit.SQ_FT,
            billing_frequency=BillingFrequency.MONTHLY,
            fee_start_trigger=FeeStartTrigger.POSSESSION_DATE,
            start_offset_days=30,
        )


def test_fee_configuration_rate_allows_null_offset_for_possession_trigger():
    """Non-after_days triggers do not require start_offset_days."""
    rate = FeeConfigurationRate(
        unit_config_kind=UnitConfigKind.APARTMENT,
        rate_amount=10.0,
        measurement_unit=MeasurementUnit.SQ_FT,
        fee_start_trigger=FeeStartTrigger.POSSESSION_DATE,
    )
    assert rate.start_offset_days is None


def test_fee_configuration_rate_accepts_after_days_with_offset():
    """after_days trigger accepts a positive start offset."""
    rate = FeeConfigurationRate(
        unit_config_kind=UnitConfigKind.APARTMENT,
        rate_amount=10.0,
        measurement_unit=MeasurementUnit.SQ_FT,
        billing_frequency=BillingFrequency.MONTHLY,
        fee_start_trigger=FeeStartTrigger.AFTER_DAYS,
        start_offset_days=30,
    )
    assert rate.start_offset_days == 30
