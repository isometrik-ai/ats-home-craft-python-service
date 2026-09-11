"""Unit tests for work_order request schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from apps.work_order_service.app.schemas.assets import CreateAssetRequest
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.work_orders import (
    CreateWorkOrderRequest,
    UpdateWorkOrderRequest,
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
