"""Background scheduler entrypoints for maintenance fee billing."""

from __future__ import annotations

from datetime import date

import asyncpg

from apps.user_service.app.db.repositories.project_fee_settings_repository import (
    ProjectFeeSettingsRepository,
)
from apps.user_service.app.services.fee_invoice_service import FeeInvoiceService
from apps.user_service.app.utils.common_utils import UserContext


class FeeSchedulerService:
    """Run invoice generation, reminders, and retries for an organization."""

    def __init__(self, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self._org_id = user_context.organization_id
        self.settings_repo = ProjectFeeSettingsRepository(db_connection)
        self.invoice_service = FeeInvoiceService(db_connection, user_context)

    async def run_billing_cycle(
        self,
        *,
        reference_date: date | None = None,
    ) -> dict[str, object]:
        """Generate invoices, process reminders, then retries for configured projects."""
        projects = await self.settings_repo.list_configured_projects(organization_id=self._org_id)
        generation_results: list[dict[str, object]] = []
        for project in projects:
            result = await self.invoice_service.generate_invoices_for_project(
                project_id=project["project_id"],
                reference_date=reference_date,
            )
            generation_results.append({"project_id": project["project_id"], **result})
        reminders = await self.invoice_service.process_reminders()
        retries = await self.invoice_service.process_retries()
        return {
            "projects_processed": len(projects),
            "generation": generation_results,
            "reminders": reminders,
            "retries": retries,
        }
