"""Unit tests for work_order request schemas."""

import pytest
from pydantic import ValidationError

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
