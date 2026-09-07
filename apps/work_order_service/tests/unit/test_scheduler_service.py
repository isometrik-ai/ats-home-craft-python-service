"""Unit tests for SchedulerService date logic."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.work_order_service.app.services.scheduler_service import (
    SchedulerService,
    _iter_visit_dates,
    _lead_days,
    next_occurrence,
)


def test_lead_days_default() -> None:
    """Verify lead days default."""
    assert _lead_days({}) == 5
    assert _lead_days({"auto_generate_lead_days": 10}) == 10


def test_next_occurrence_weekly() -> None:
    """Verify next occurrence weekly."""
    anchor = date(2026, 3, 2)  # Monday
    nxt = next_occurrence(anchor, "weekly", [3])  # Wednesday
    assert nxt == date(2026, 3, 4)


def test_iter_visit_dates_quarterly() -> None:
    """Verify iter visit dates quarterly."""
    contract = {
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "visit_frequency": "quarterly",
        "last_serviced_date": None,
    }
    visits = list(_iter_visit_dates(contract, date(2026, 6, 1)))
    assert visits[0] == date(2026, 1, 1)
    assert visits[1] == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_generate_due_work_orders_skips_existing(monkeypatch) -> None:
    """Idempotent generation skips dates that already have work orders."""
    monkeypatch.setattr(
        "apps.work_order_service.app.services.scheduler_service._today",
        lambda: date(2026, 1, 10),
    )
    conn = MagicMock()
    service = SchedulerService(conn)
    service.contracts = MagicMock()
    service.work_orders = MagicMock()

    contract = {
        "id": "c1",
        "organization_id": "org-1",
        "project_id": "proj-1",
        "title": "AMC",
        "status": "active",
        "start_date": "2026-01-01",
        "visit_frequency": "monthly",
        "auto_generate_lead_days": 5,
        "asset_ids": [],
        "company_id": "co-1",
    }
    service.contracts.list_active_for_scheduler = AsyncMock(return_value=[contract])
    service.work_orders.existing_contract_schedule_dates = AsyncMock(return_value={"2026-01-01"})
    service.work_orders.create = AsyncMock()
    service.work_orders.cancel_contract_work_orders = AsyncMock(return_value=0)
    service.contracts.update_next_visit_date = AsyncMock()
    service.generate_recurring_work_orders = AsyncMock(return_value={"created": 0, "skipped": 0})

    result = await service.generate_due_work_orders("org-1")

    assert result["skipped"] == 1
    assert result["created"] == 0
    service.work_orders.create.assert_not_called()
