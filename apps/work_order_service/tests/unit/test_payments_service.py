"""Unit tests for payments service helpers."""

from apps.work_order_service.app.services.payments_service import PaymentsService


def test_payment_timeline_note_includes_amount_and_reference():
    """Payment notes mirror the prototype timeline wording."""
    note = PaymentsService._payment_timeline_note(
        amount_minor=566400,
        currency="INR",
        invoice_number="ACP-082",
        reference="UTR/HDFC0009123456",
    )
    assert "₹5,664.00" in note
    assert "ACP-082" in note
    assert "UTR/HDFC0009123456" in note


def test_payment_timeline_note_without_amount():
    """Fallback note when amount is unknown."""
    note = PaymentsService._payment_timeline_note(
        amount_minor=None,
        currency="INR",
        invoice_number="INV-1",
        reference=None,
    )
    assert note == "Payment recorded against INV-1"
