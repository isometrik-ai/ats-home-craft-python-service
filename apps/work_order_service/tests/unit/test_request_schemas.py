"""Unit tests for work_order request schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from apps.work_order_service.app.schemas.assets import CreateAssetRequest
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.invoices import UpdateInvoiceRequest
from apps.work_order_service.app.schemas.work_orders import (
    CreateWorkOrderRequest,
    UpdateWorkOrderRequest,
    VendorUpdateWorkOrderRequest,
)


def test_create_work_order_requires_title():
    """Verify create work order requires title."""
    with pytest.raises(ValidationError):
        CreateWorkOrderRequest.model_validate({})


def test_create_work_order_rejects_unknown_fields():
    """Verify create work order rejects unknown fields."""
    with pytest.raises(ValidationError):
        CreateWorkOrderRequest.model_validate({"title": "Fix HVAC", "foo": "bar"})


def test_create_work_order_accepts_valid_payload():
    """Verify create work order accepts valid payload."""
    body = CreateWorkOrderRequest.model_validate(
        {
            "title": "Fix HVAC",
            "priority": "high",
            "scheduled_date": "2026-09-15",
        }
    )
    assert body.title == "Fix HVAC"
    assert body.priority.value == "high"


def test_update_work_order_allows_partial_payload():
    """Verify update work order allows partial payload."""
    body = UpdateWorkOrderRequest.model_validate({"state": "in_progress"})
    dumped = body.model_dump(exclude_unset=True, mode="json")
    assert dumped == {"state": "in_progress"}


def test_update_work_order_accepts_edit_form_fields():
    """Staff PATCH supports prototype edit-form and detail-page fields."""
    body = UpdateWorkOrderRequest.model_validate(
        {
            "title": "Lift check",
            "asset_ids": ["00000000-0000-0000-0000-000000000001"],
            "access_notes": "Use service lift",
            "form_template_id": "00000000-0000-0000-0000-000000000002",
            "pre_start_form_template_id": "00000000-0000-0000-0000-000000000003",
            "form_values": {"pressure": 42},
            "line_items": [{"description": "Filter", "total_minor": 1000}],
            "invoice_ids": ["00000000-0000-0000-0000-000000000004"],
            "is_recurring": True,
            "recurring_frequency": "weekly",
            "recurring_days": [1, 3],
        }
    )
    assert body.title == "Lift check"
    assert body.recurring_frequency.value == "weekly"


def test_update_work_order_rejects_timeline_patch():
    """Timeline must use the append endpoint, not PATCH."""
    with pytest.raises(ValidationError):
        UpdateWorkOrderRequest.model_validate({"timeline": [{"type": "note"}]})


def test_vendor_update_accepts_operational_fields():
    """Vendor portal PATCH supports line items and form payloads."""
    body = VendorUpdateWorkOrderRequest.model_validate(
        {
            "state": "in_progress",
            "line_items": [{"description": "Belt", "total_minor": 500}],
            "form_values": {"pressure": 40},
            "pre_start_form_values": {"checked": True},
        }
    )
    dumped = body.model_dump(exclude_unset=True, mode="json")
    assert dumped["state"] == "in_progress"
    assert len(dumped["line_items"]) == 1


def test_update_invoice_rejects_timeline_patch():
    """Invoice timeline must use the append endpoint, not PATCH."""
    with pytest.raises(ValidationError):
        UpdateInvoiceRequest.model_validate({"timeline": [{"type": "approved"}]})


def test_update_invoice_accepts_status_note():
    """FM revision requests can carry a note without persisting it."""
    body = UpdateInvoiceRequest.model_validate(
        {"status": "revision_requested", "note": "Please itemize parts"}
    )
    assert body.note == "Please itemize parts"


def test_dump_request_preserves_date_objects_for_asyncpg():
    """Repository payloads must keep date types for Postgres date columns."""
    body = CreateAssetRequest.model_validate(
        {
            "name": "Pump",
            "code": "P-001",
            "category_id": "00000000-0000-0000-0000-000000000001",
            "purchase_date": "2026-09-17",
            "install_date": "2026-09-18",
        }
    )
    dumped = dump_request(body)
    assert dumped["purchase_date"] == date(2026, 9, 17)
    assert isinstance(dumped["purchase_date"], date)
    assert dumped["install_date"] == date(2026, 9, 18)
