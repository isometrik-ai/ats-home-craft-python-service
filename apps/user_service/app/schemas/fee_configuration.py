"""Request and response models for fee configuration."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums.fee_configuration import (
    FeeBillingCycle,
    FeeFrequency,
    FeeHeadKind,
    FeeHeadStatus,
    FeeStartRule,
)
from apps.user_service.app.schemas.enums.property import PropertyType


class TaxInput(BaseModel):
    """Tax object stored on a fee head."""

    model_config = ConfigDict(extra="forbid")

    applicable: bool
    rate_percent: Decimal | None = None


class LateFeeStepInput(BaseModel):
    """One step of a flat late fee. The bill carries the step it has reached."""

    model_config = ConfigDict(extra="forbid")

    days_overdue: int
    amount: Decimal


class LateFeeInput(BaseModel):
    """Late fee rule. Mode selects which other fields apply."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    steps: list[LateFeeStepInput] | None = None
    annual_percent: Decimal | None = None


class FeeHeadScopeInput(BaseModel):
    """One property-type row. Maintenance also sends rate and minimum."""

    model_config = ConfigDict(extra="forbid")

    property_type: PropertyType
    enabled: bool
    rate_per_sqft: Decimal | None = None
    minimum_amount: Decimal | None = None


class FeeHeadChargeInput(BaseModel):
    """Charge amounts. Electricity sends the four rates.
    Club sends amount. Maintenance omits this."""

    model_config = ConfigDict(extra="forbid")

    grid_fixed_amount: Decimal | None = None
    grid_unit_rate: Decimal | None = None
    dg_fixed_amount: Decimal | None = None
    dg_unit_rate: Decimal | None = None
    amount: Decimal | None = None


class FeeHeadWriteRequest(BaseModel):
    """Editable fee-head document. Category is derived from kind and is rejected."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: FeeHeadStatus
    frequency: FeeFrequency
    line_description: str | None = None
    fee_start_rule: FeeStartRule
    fee_start_date: date | None = Field(
        default=None,
        description=(
            "Required when fee_start_rule is specific_date, as YYYY-MM-DD. "
            "Omit for first_of_next_month."
        ),
    )
    due_within_days: int
    invoice_day: int
    billing_cycle: FeeBillingCycle | None = None
    cycle_anchor_month: int | None = None
    tax: TaxInput
    late_fee: LateFeeInput
    scopes: list[FeeHeadScopeInput]
    meter_read_day: int | None = None
    charge: FeeHeadChargeInput | None = Field(
        default=None,
        description=(
            "Electricity requires grid_fixed_amount, grid_unit_rate, "
            "dg_fixed_amount, and dg_unit_rate. Club requires amount. Omit for maintenance."
        ),
    )


class CreateFeeHeadRequest(FeeHeadWriteRequest):
    """Create one fee head of a known kind. One row per kind per project."""

    kind: FeeHeadKind


class UpdateFeeHeadRequest(FeeHeadWriteRequest):
    """Full editable fee-head document. Kind and category are rejected."""

    version: int = Field(..., ge=1)


class UpdateFeeHeadStatusRequest(BaseModel):
    """List toggle. Does not replace the charge document."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1)
    status: FeeHeadStatus


class FinanceSettingsWriteRequest(BaseModel):
    """The four project-wide dunning numbers."""

    model_config = ConfigDict(extra="forbid")

    payment_retry_count: int
    payment_retry_interval_days: int
    pre_due_reminder_count: int
    pre_due_reminder_interval_days: int


class CreateFinanceSettingsRequest(FinanceSettingsWriteRequest):
    """Create the project's finance settings row."""


class UpdateFinanceSettingsRequest(FinanceSettingsWriteRequest):
    """Replace the four dunning numbers when version still matches."""

    version: int = Field(..., ge=1)


class FeeHeadListItem(BaseModel):
    """One row on the fee heads tab."""

    id: str
    kind: FeeHeadKind
    name: str
    category: str
    category_label: str
    charge_summary: str
    frequency: FeeFrequency
    frequency_label: str
    tax_label: str
    status: FeeHeadStatus
    version: int


class TaxResponse(BaseModel):
    """Tax object returned on a fee head. Money is a decimal string."""

    applicable: bool
    rate_percent: str | None = None


class LateFeeStepResponse(BaseModel):
    """One flat late-fee step. Amount is a decimal string."""

    days_overdue: int
    amount: str


class LateFeeResponse(BaseModel):
    """Late fee rule. Unused fields are omitted for the active mode."""

    mode: str
    steps: list[LateFeeStepResponse] | None = None
    annual_percent: str | None = None


class FeeHeadScopeResponse(BaseModel):
    """One property-type row. Rates are present for maintenance only."""

    property_type: PropertyType
    label: str
    enabled: bool
    rate_per_sqft: str | None = None
    minimum_amount: str | None = None


class FeeHeadChargeResponse(BaseModel):
    """Kind-specific charge. Electricity uses the four rate keys. Club uses amount."""

    grid_fixed_amount: str | None = None
    grid_unit_rate: str | None = None
    dg_fixed_amount: str | None = None
    dg_unit_rate: str | None = None
    amount: str | None = None


class FeeHeadDetail(FeeHeadListItem):
    """Editor payload. Money fields are decimal strings."""

    line_description: str | None = None
    fee_start_rule: FeeStartRule
    fee_start_date: date | None = None
    due_within_days: int
    invoice_day: int
    billing_cycle: FeeBillingCycle | None = None
    cycle_anchor_month: int | None = None
    billing_months: list[int] | None = None
    billing_months_sentence: str | None = None
    tax: TaxResponse
    late_fee: LateFeeResponse
    late_fee_example: str
    scopes: list[FeeHeadScopeResponse]
    meter_read_day: int | None = None
    charge: FeeHeadChargeResponse | None = None


class FinanceSettingsResponse(BaseModel):
    """Settings tab payload, including the sentence built from the four numbers."""

    payment_retry_count: int
    payment_retry_interval_days: int
    pre_due_reminder_count: int
    pre_due_reminder_interval_days: int
    summary: str
    version: int


class FeeHeadListApiResponse(BaseModel):
    """API envelope for GET /fee-heads."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[FeeHeadListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class FeeHeadDetailApiResponse(BaseModel):
    """API envelope for create, get, and save of one fee head."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeeHeadDetail


class FeeHeadListItemApiResponse(BaseModel):
    """API envelope for the list status toggle."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeeHeadListItem


class FinanceSettingsApiResponse(BaseModel):
    """API envelope for get, create, and save of finance settings."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FinanceSettingsResponse
