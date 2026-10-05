"""Reminder dates and the invoice PDF object key."""

from datetime import date

from apps.user_service.app.services.fee_invoice_mail import (
    payment_mode_label,
    payment_reference,
)
from apps.user_service.app.services.fee_invoice_pdf import build_fee_invoice_pdf
from apps.user_service.app.services.fee_invoice_reminders import (
    is_reminder_day,
    reminder_dates,
)
from apps.user_service.app.services.fee_invoice_storage import fee_invoice_pdf_path


def test_two_reminders_fall_three_days_apart():
    """Two reminders, three days apart, land six days and three days before the due date."""
    dates = reminder_dates(due_on=date(2026, 10, 15), count=2, interval_days=3)
    assert dates == [date(2026, 10, 9), date(2026, 10, 12)]


def test_zero_reminders_schedule_nothing():
    """A count of zero sends no reminder."""
    dates = reminder_dates(due_on=date(2026, 10, 15), count=0, interval_days=3)
    assert dates == []


def test_reminder_day_matches_only_those_dates():
    """The due date itself is not a reminder day."""
    kwargs = {
        "due_on": date(2026, 10, 15),
        "count": 2,
        "interval_days": 3,
    }
    assert is_reminder_day(**kwargs, today=date(2026, 10, 9))
    assert is_reminder_day(**kwargs, today=date(2026, 10, 12))
    assert not is_reminder_day(**kwargs, today=date(2026, 10, 11))
    assert not is_reminder_day(**kwargs, today=date(2026, 10, 15))


def test_invoice_pdf_path_uses_the_invoice_id():
    """The stored file sits under fee-invoices in the media bucket."""
    assert fee_invoice_pdf_path("inv-1") == "fee-invoices/inv-1.pdf"


def test_invoice_pdf_shows_the_sample_bill():
    """The page carries the sq ft working, the tax percent, and the amount due."""
    pdf = build_fee_invoice_pdf(
        {
            "invoice_number": "INV-LUX-B3102-20261005",
            "unit_code": "LUX-B3102",
            "project_name": "ATS Luxury",
            "billing_month": "2026-10-01",
            "invoice_date": "2026-10-05",
            "due_date": "2026-10-15",
            "taxable_amount": "5403.30",
            "tax_amount": "972.59",
            "round_off_amount": "0.11",
            "total_amount": "6376.00",
            "lines": [
                {
                    "description": "Maintenance (CAM)",
                    "kind": "maintenance",
                    "area_or_quantity": "1245.00",
                    "rate": "4.34",
                    "taxable_amount": "5403.30",
                    "tax_amount": "972.59",
                    "line_total": "6375.89",
                }
            ],
        }
    )
    assert b"INV-LUX-B3102-20261005" in pdf
    assert b"1,245 sq ft x Rs 4.34" in pdf
    assert b"18%" in pdf
    assert b"Tax 18%" in pdf
    assert b"Rs 5,403.30" in pdf
    assert b"Rs 972.59" in pdf
    assert b"Rs 6,376.00" in pdf


def test_payment_ack_labels_mode_and_blank_reference():
    """UPI is shown as UPI, and a blank reference is a dash."""
    assert payment_mode_label("upi") == "UPI"
    assert payment_mode_label("neft_rtgs") == "NEFT/RTGS"
    assert payment_reference(None) == "—"
    assert payment_reference("  ") == "—"
    assert payment_reference("UTR123") == "UTR123"
