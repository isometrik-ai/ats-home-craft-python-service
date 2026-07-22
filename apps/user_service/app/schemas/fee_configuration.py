"""Fee configuration request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    BillingCycleType,
    BillingFrequency,
    ExhaustedRetryAction,
    FeeStartTrigger,
    MeasurementUnit,
    UnitConfigKind,
)


class FeeConfigurationSettings(BaseModel):
    """Global fee policy fields (API uses major currency units)."""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="INR", min_length=3, max_length=3)
    billing_cycle_type: BillingCycleType = BillingCycleType.FINANCIAL_YEAR
    retry_count: int = Field(default=2, ge=2, le=5)
    retry_interval_days: int = Field(default=3)
    reminder_count: int = Field(default=2, ge=0, le=3)
    reminder_interval_days: int = Field(default=2)
    exhausted_retry_action: ExhaustedRetryAction = ExhaustedRetryAction.ESCALATE_TO_BILLING_TEAM

    @model_validator(mode="after")
    def validate_intervals(self) -> FeeConfigurationSettings:
        """Ensure interval values match UI dropdown options."""
        allowed = {1, 2, 3, 7}
        if self.retry_interval_days not in allowed:
            raise ValueError("retry_interval_days must be one of 1, 2, 3, 7")
        if self.reminder_interval_days not in allowed:
            raise ValueError("reminder_interval_days must be one of 1, 2, 3, 7")
        return self


class FeeConfigurationRate(BaseModel):
    """Per property-category rate (API uses major currency units)."""

    model_config = ConfigDict(extra="forbid")

    unit_config_kind: UnitConfigKind
    rate_amount: float = Field(..., ge=0)
    measurement_unit: MeasurementUnit
    billing_frequency: BillingFrequency = BillingFrequency.MONTHLY
    fee_start_trigger: FeeStartTrigger = FeeStartTrigger.POSSESSION_DATE
    start_offset_days: int | None = Field(default=None, ge=1)
    minimum_fee: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_start_offset(self) -> FeeConfigurationRate:
        """Require offset when trigger is after_days."""
        if self.fee_start_trigger == FeeStartTrigger.AFTER_DAYS:
            if self.start_offset_days is None:
                raise ValueError(
                    "start_offset_days is required when fee_start_trigger is after_days"
                )
        elif self.start_offset_days is not None:
            raise ValueError(
                "start_offset_days is only allowed when fee_start_trigger is after_days"
            )
        return self


class FeeRatePreview(BaseModel):
    """Computed preview for a rate row."""

    model_config = ConfigDict(extra="forbid")

    sample_area: float
    sample_area_unit: str
    computed_period_fee: float
    minimum_applied: bool = False


class FeeConfigurationRateResponse(FeeConfigurationRate):
    """Rate row returned from GET with optional preview."""

    preview: FeeRatePreview | None = None


class FeeConfigurationSettingsResponse(FeeConfigurationSettings):
    """Settings returned from GET with derived reminder lead."""

    first_reminder_lead_days: int = Field(default=0, ge=0)


class UpsertFeeConfigurationRequest(BaseModel):
    """PUT body for fee configuration."""

    model_config = ConfigDict(extra="forbid")

    settings: FeeConfigurationSettings
    rates: list[FeeConfigurationRate] = Field(default_factory=list)


class FeeConfigurationWarnings(BaseModel):
    """Non-blocking warnings on GET/PUT."""

    model_config = ConfigDict(extra="forbid")

    possession_date_missing: bool = False


class FeeConfigurationResponse(BaseModel):
    """Full fee configuration payload."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    is_configured: bool = False
    configured_at: str | None = None
    settings: FeeConfigurationSettingsResponse
    applicable_tabs: list[UnitConfigKind] = Field(default_factory=list)
    rates: list[FeeConfigurationRateResponse] = Field(default_factory=list)
    warnings: FeeConfigurationWarnings = Field(default_factory=FeeConfigurationWarnings)


class FeePreviewQuery(BaseModel):
    """Query params for preview endpoint."""

    model_config = ConfigDict(extra="forbid")

    unit_config_kind: UnitConfigKind
    unit_id: str | None = None
    area: float | None = Field(default=None, gt=0)
    measurement_unit: MeasurementUnit = MeasurementUnit.SQ_FT


class FeePreviewResponse(BaseModel):
    """Preview calculation result."""

    model_config = ConfigDict(extra="forbid")

    unit_config_kind: UnitConfigKind
    area: float
    measurement_unit: MeasurementUnit
    billing_frequency: BillingFrequency
    computed_period_fee: float
    minimum_applied: bool = False
    currency: str = "INR"


class PayMaintenanceFeeInvoiceRequest(BaseModel):
    """Resident payment request (Phase 3 — amount in major units)."""

    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(
        default=None,
        gt=0,
        description="Optional partial payment; defaults to full outstanding balance.",
    )


class MaintenanceFeeInvoiceSummary(BaseModel):
    """Invoice list row."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    unit_id: str
    unit_code: str | None = None
    unit_label: str | None = None
    period_start: str
    period_end: str
    due_date: str
    amount: float
    amount_paid: float
    outstanding_amount: float
    currency: str
    status: str
    retry_attempts: int = 0
    reminders_sent: int = 0
    escalated_at: str | None = None


class MaintenanceFeeInvoiceDetail(MaintenanceFeeInvoiceSummary):
    """Invoice detail with metadata."""

    metadata: dict = Field(default_factory=dict)
    issued_at: str | None = None
    paid_at: str | None = None
    created_at: str
    updated_at: str
