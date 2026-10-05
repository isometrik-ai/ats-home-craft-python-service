"""Staff payment and unit balance for fee invoices."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeeInvoiceStatus(str, Enum):
    """Payment position of an issued fee invoice."""

    ISSUED = "issued"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"


class FeePaymentMode(str, Enum):
    """How a fee payment was received outside the app."""

    CASH = "cash"
    CHEQUE = "cheque"
    NEFT_RTGS = "neft_rtgs"
    CARD = "card"
    UPI = "upi"
    OTHER = "other"


class RecordFeePaymentRequest(BaseModel):
    """A payment against one issued invoice."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(description="Amount received, in rupees.", ge=0)
    paid_on: date = Field(description="Date the payment was received.")
    mode: FeePaymentMode = Field(
        description="cash, cheque, neft_rtgs, card, upi, or other.",
    )
    reference: str | None = Field(
        default=None,
        description="Cheque number, UTR, or a note. Omit when there is none.",
    )

    @field_validator("reference")
    @classmethod
    def _clean_reference(cls, value: str | None) -> str | None:
        """Store a trimmed reference, or nothing when the field is blank."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 120:
            raise ValueError("Reference must be 120 characters or fewer.")
        return cleaned


class FeePaymentResult(BaseModel):
    """Invoice position after the payment is recorded."""

    invoice_id: str
    status: str
    amount: str
    paid_on: date
    mode: FeePaymentMode
    reference: str | None = None
    amount_paid: str
    outstanding: str
    credit: str


class FeeBalanceLine(BaseModel):
    """One line on an open invoice."""

    line_role: str
    kind: str | None = None
    description: str
    line_total: str


class FeeBalanceInvoice(BaseModel):
    """An invoice that still has an outstanding balance."""

    id: str
    invoice_number: str | None = None
    billing_month: str
    invoice_date: str
    due_date: str
    status: str
    total_amount: str
    amount_paid: str
    outstanding: str
    lines: list[FeeBalanceLine]


class FeeUnitBalance(BaseModel):
    """Amount due, split into arrears, late fee, and this period's charges."""

    amount_due: str
    credit: str
    arrears: str
    late_fee: str
    current_charges: str
    invoices: list[FeeBalanceInvoice]


class FeeInvoiceLineDetail(BaseModel):
    """One charge or late-fee line on an invoice."""

    id: str
    kind: str
    line_role: str
    description: str
    area_or_quantity: str | None = None
    rate: str | None = None
    minimum_amount: str | None = None
    taxable_amount: str
    tax_amount: str
    line_total: str
    source_invoice_id: str | None = None
    started_months: int | None = None
    days_overdue: int | None = None


class FeeInvoicePaymentDetail(BaseModel):
    """A receipt already applied to the invoice."""

    amount: str
    paid_on: date
    mode: FeePaymentMode
    reference: str | None = None


class FeeInvoiceDetail(BaseModel):
    """Invoice header, lines, and payments."""

    id: str
    invoice_number: str
    unit_id: str
    unit_code: str
    billing_month: str
    invoice_date: str
    due_date: str
    status: FeeInvoiceStatus
    taxable_amount: str
    tax_amount: str
    round_off_amount: str
    total_amount: str
    amount_paid: str
    outstanding: str
    pdf_path: str | None = None
    lines: list[FeeInvoiceLineDetail]
    payments: list[FeeInvoicePaymentDetail]


class FeeInvoiceDetailApiResponse(BaseModel):
    """API envelope for one fee invoice."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeeInvoiceDetail


class FeeInvoiceListItem(BaseModel):
    """One invoice on the project list."""

    id: str
    invoice_number: str
    unit_id: str
    unit_code: str
    billing_month: str
    invoice_date: str
    due_date: str
    status: FeeInvoiceStatus
    total_amount: str
    amount_paid: str
    outstanding: str
    pdf_path: str | None = None


class FeeInvoiceListApiResponse(BaseModel):
    """API envelope for the project invoice list."""

    status: str
    message: str
    statusCode: int
    code: str
    data: list[FeeInvoiceListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class FeePaymentApiResponse(BaseModel):
    """API envelope for a recorded payment."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeePaymentResult


class FeeOutstandingSummary(BaseModel):
    """Pending payments card for one unit."""

    total_outstanding: str
    overdue: bool
    unpaid_count: int
    billing_months: list[str]
    includes_late_fee: bool


class FeeOutstandingSummaryApiResponse(BaseModel):
    """API envelope for a resident's pending payments."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeeOutstandingSummary


class FeeUnitBalanceApiResponse(BaseModel):
    """API envelope for a unit's open fee balance."""

    status: str
    message: str
    statusCode: int
    code: str
    data: FeeUnitBalance
