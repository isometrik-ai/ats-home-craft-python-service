"""Unit tests for FeeSchedulerService."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.fee_scheduler_service import FeeSchedulerService
from apps.user_service.app.utils.common_utils import UserContext


def _user_context() -> UserContext:
    """Build a minimal admin user context."""
    return UserContext(user_id="user-1", email="admin@example.com", organization_id="org-1")


@pytest.mark.asyncio
async def test_run_billing_cycle_orchestrates_projects() -> None:
    """Billing cycle should generate, remind, and retry per configured project."""
    service = FeeSchedulerService.__new__(FeeSchedulerService)
    service._org_id = "org-1"
    service.settings_repo = MagicMock()
    service.settings_repo.list_configured_projects = AsyncMock(
        return_value=[{"project_id": "proj-1"}, {"project_id": "proj-2"}]
    )
    service.invoice_service = MagicMock()
    service.invoice_service.generate_invoices_for_project = AsyncMock(
        return_value={"created_count": 1, "skipped_count": 0, "invoice_ids": ["inv-1"]}
    )
    service.invoice_service.process_reminders = AsyncMock(return_value={"processed_count": 2})
    service.invoice_service.process_retries = AsyncMock(
        return_value={"processed_count": 1, "escalated_count": 0}
    )

    result = await service.run_billing_cycle(reference_date=date(2026, 7, 1))

    assert result["projects_processed"] == 2
    assert len(result["generation"]) == 2
    assert result["reminders"]["processed_count"] == 2
    assert result["retries"]["escalated_count"] == 0
    assert service.invoice_service.generate_invoices_for_project.await_count == 2


@pytest.mark.asyncio
async def test_run_billing_cycle_empty_projects() -> None:
    """No configured projects should still run reminders and retries."""
    service = FeeSchedulerService.__new__(FeeSchedulerService)
    service._org_id = "org-1"
    service.settings_repo = MagicMock()
    service.settings_repo.list_configured_projects = AsyncMock(return_value=[])
    service.invoice_service = MagicMock()
    service.invoice_service.process_reminders = AsyncMock(return_value={"processed_count": 0})
    service.invoice_service.process_retries = AsyncMock(
        return_value={"processed_count": 0, "escalated_count": 0}
    )

    result = await service.run_billing_cycle()

    assert result["projects_processed"] == 0
    assert result["generation"] == []
    service.invoice_service.generate_invoices_for_project.assert_not_called()
