"""Maintenance fee invoice generation, reminders, retries, and payments."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.maintenance_fee_invoice_events_repository import (
    MaintenanceFeeInvoiceEventsRepository,
)
from apps.user_service.app.db.repositories.maintenance_fee_invoices_repository import (
    MaintenanceFeeInvoicesRepository,
)
from apps.user_service.app.db.repositories.project_fee_rates_repository import (
    ProjectFeeRatesRepository,
)
from apps.user_service.app.db.repositories.project_fee_settings_repository import (
    ProjectFeeSettingsRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.schemas.enums import (
    FeeStartTrigger,
    MaintenanceFeeInvoiceEventType,
    MaintenanceFeeInvoiceStatus,
)
from apps.user_service.app.services.fee_calculation_service import (
    compute_period_fee_minor,
    convert_minor_to_major,
    fee_rate_input_from_row,
    resolve_area_sqft_from_unit_row,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class FeeInvoiceService:
    """Operational billing for maintenance fee invoices."""

    def __init__(self, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self._org_id = user_context.organization_id
        self._user_id = user_context.user_id
        self.settings_repo = ProjectFeeSettingsRepository(db_connection)
        self.rates_repo = ProjectFeeRatesRepository(db_connection)
        self.projects_repo = ProjectsRepository(db_connection)
        self.invoices_repo = MaintenanceFeeInvoicesRepository(db_connection)
        self.events_repo = MaintenanceFeeInvoiceEventsRepository(db_connection)
        self.db_connection = db_connection

    async def _ensure_project(self, project_id: str) -> dict[str, Any]:
        """Load a project or raise not-found."""
        project = await self.projects_repo.get_project(
            organization_id=self._org_id,
            project_id=project_id,
        )
        if not project:
            raise NotFoundException(
                message_key="fee_configuration.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return project

    @staticmethod
    def _serialize_invoice(row: dict[str, Any]) -> dict[str, Any]:
        """Convert an invoice row to major currency API fields."""
        amount_minor = int(row["amount_minor"])
        paid_minor = int(row["amount_paid_minor"])
        serialized = serialize_row(row)
        return {
            **serialized,
            "amount": convert_minor_to_major(amount_minor),
            "amount_paid": convert_minor_to_major(paid_minor),
            "outstanding_amount": convert_minor_to_major(max(amount_minor - paid_minor, 0)),
        }

    async def list_project_invoices(
        self,
        *,
        project_id: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Admin list of invoices for a project."""
        await self._ensure_project(project_id)
        offset = max(page - 1, 0) * page_size
        rows, total = await self.invoices_repo.list_by_project(
            organization_id=self._org_id,
            project_id=project_id,
            status=status,
            limit=page_size,
            offset=offset,
        )
        return {
            "items": [self._serialize_invoice(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_invoice(
        self, *, invoice_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Fetch a single invoice."""
        row = await self.invoices_repo.get_by_id(
            organization_id=self._org_id,
            invoice_id=invoice_id,
        )
        if not row:
            raise NotFoundException(
                message_key="fee_invoices.errors.invoice_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if project_id and str(row.get("project_id")) != project_id:
            raise NotFoundException(
                message_key="fee_invoices.errors.invoice_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_invoice(row)

    @staticmethod
    def _month_period(reference: date) -> tuple[date, date]:
        """Return inclusive month start/end for a reference date."""
        start = reference.replace(day=1)
        end = reference.replace(day=monthrange(reference.year, reference.month)[1])
        return start, end

    @staticmethod
    def _resolve_anchor_date(
        *,
        trigger: str,
        possession_date: date | None,
        onboarding_activated_at: datetime | None,
    ) -> date | None:
        """Resolve the base anchor date for a fee start trigger."""
        if trigger == FeeStartTrigger.POSSESSION_DATE.value:
            return possession_date
        if trigger == FeeStartTrigger.ONBOARDING_DATE.value:
            return onboarding_activated_at.date() if onboarding_activated_at else None
        base = possession_date or (
            onboarding_activated_at.date() if onboarding_activated_at else None
        )
        return base

    @staticmethod
    def _apply_start_trigger(
        *,
        trigger: str,
        anchor: date,
        offset_days: int | None,
    ) -> date:
        """Apply the configured start trigger to an anchor date."""
        if trigger == FeeStartTrigger.FIRST_OF_NEXT_MONTH.value:
            if anchor.month == 12:
                return date(anchor.year + 1, 1, 1)
            return date(anchor.year, anchor.month + 1, 1)
        if trigger == FeeStartTrigger.AFTER_ONE_YEAR.value:
            return anchor.replace(year=anchor.year + 1)
        if trigger == FeeStartTrigger.AFTER_DAYS.value:
            return anchor + timedelta(days=int(offset_days or 0))
        return anchor

    async def _list_billable_units(self, *, project_id: str) -> list[dict[str, Any]]:
        """List non-parking units with area and primary owner activation data."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              u.id::text AS unit_id,
              u.project_id::text AS project_id,
              uc.config_kind::text AS config_kind,
              uc.area_sqft,
              uc.carpet_area_sqft,
              pci.size_sqft AS plot_size_sqft,
              cu.id::text AS contact_unit_id,
              cu.activated_at
            FROM units u
            LEFT JOIN unit_configs uc
              ON uc.id = u.config_id
             AND uc.organization_id = u.organization_id
            LEFT JOIN plot_config_items pci
              ON pci.id = u.plot_item_id
             AND pci.organization_id = u.organization_id
            LEFT JOIN contact_units cu
              ON cu.unit_id = u.id
             AND cu.organization_id = u.organization_id
             AND cu.is_primary = true
             AND cu.status = 'active'::contact_unit_status
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.is_parking = false
            """,
            self._org_id,
            project_id,
        )
        return [dict(row) for row in rows]

    def _rate_for_unit(
        self, *, rate_rows: list[dict[str, Any]], config_kind: str | None
    ) -> dict[str, Any] | None:
        """Pick the fee rate row matching a unit's config kind."""
        if not config_kind:
            return None
        return next(
            (row for row in rate_rows if row["unit_config_kind"] == config_kind),
            None,
        )

    async def generate_invoices_for_project(
        self,
        *,
        project_id: str,
        reference_date: date | None = None,
    ) -> dict[str, Any]:
        """Generate maintenance fee invoices for billable units."""
        project = await self._ensure_project(project_id)
        settings = await self.settings_repo.get_by_project_id(
            organization_id=self._org_id,
            project_id=project_id,
        )
        if not settings or not settings.get("is_configured"):
            raise ValidationException(
                message_key="fee_configuration.errors.configuration_incomplete",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        rate_rows = await self.rates_repo.list_by_project_id(
            organization_id=self._org_id,
            project_id=project_id,
        )
        today = reference_date or datetime.now(UTC).date()
        period_start, period_end = self._month_period(today)
        due_date = period_end
        created: list[str] = []
        skipped = 0
        for unit in await self._list_billable_units(project_id=project_id):
            config_kind = unit.get("config_kind") or "plot"
            rate_row = self._rate_for_unit(rate_rows=rate_rows, config_kind=config_kind)
            if not rate_row:
                skipped += 1
                continue
            area_sqft = resolve_area_sqft_from_unit_row(unit)
            if area_sqft is None:
                skipped += 1
                continue
            anchor = self._resolve_anchor_date(
                trigger=rate_row["fee_start_trigger"],
                possession_date=project.get("possession_date"),
                onboarding_activated_at=unit.get("activated_at"),
            )
            if anchor is None:
                skipped += 1
                continue
            fee_start = self._apply_start_trigger(
                trigger=rate_row["fee_start_trigger"],
                anchor=anchor,
                offset_days=rate_row.get("start_offset_days"),
            )
            if fee_start > period_end:
                skipped += 1
                continue
            preview = compute_period_fee_minor(
                area_sqft=float(area_sqft),
                rate=fee_rate_input_from_row(rate_row),
            )
            reminder_lead_days = int(settings["reminder_count"]) * int(
                settings["reminder_interval_days"]
            )
            next_reminder_at = None
            if int(settings["reminder_count"]) > 0 and reminder_lead_days > 0:
                next_reminder_at = datetime.combine(
                    due_date - timedelta(days=reminder_lead_days),
                    datetime.min.time(),
                    tzinfo=UTC,
                )
            row = await self.invoices_repo.insert(
                data={
                    "organization_id": self._org_id,
                    "project_id": project_id,
                    "unit_id": unit["unit_id"],
                    "contact_unit_id": unit.get("contact_unit_id"),
                    "unit_config_kind": config_kind,
                    "period_start": period_start,
                    "period_end": period_end,
                    "due_date": due_date,
                    "amount_minor": preview.period_amount_minor,
                    "currency": settings["currency"],
                    "status": MaintenanceFeeInvoiceStatus.ISSUED.value,
                    "next_reminder_at": next_reminder_at,
                    "issued_at": datetime.now(UTC),
                    "metadata": {
                        "area_sqft": area_sqft,
                        "fee_start_date": fee_start.isoformat(),
                        "rate_id": rate_row["id"],
                    },
                }
            )
            await self.events_repo.insert(
                data={
                    "organization_id": self._org_id,
                    "invoice_id": row["id"],
                    "event_type": MaintenanceFeeInvoiceEventType.ISSUED.value,
                    "actor_user_id": self._user_id,
                }
            )
            created.append(row["id"])
        return {"created_count": len(created), "skipped_count": skipped, "invoice_ids": created}

    async def process_reminders(self, *, limit: int = 100) -> dict[str, Any]:
        """Send due reminders (records events; notification delivery is follow-up)."""
        rows = await self.invoices_repo.list_due_for_reminders(
            organization_id=self._org_id,
            limit=limit,
        )
        processed = 0
        for row in rows:
            reminders_sent = int(row["reminders_sent"]) + 1
            reminder_interval = int(row["reminder_interval_days"])
            due_date = row["due_date"]
            next_reminder_at = (
                datetime.combine(
                    due_date
                    - timedelta(
                        days=reminder_interval * (int(row["reminder_count"]) - reminders_sent)
                    ),
                    datetime.min.time(),
                    tzinfo=UTC,
                )
                if reminders_sent < int(row["reminder_count"])
                else None
            )
            await self.invoices_repo.update_invoice(
                organization_id=self._org_id,
                invoice_id=row["id"],
                patch={
                    "reminders_sent": reminders_sent,
                    "next_reminder_at": next_reminder_at,
                },
            )
            await self.events_repo.insert(
                data={
                    "organization_id": self._org_id,
                    "invoice_id": row["id"],
                    "event_type": MaintenanceFeeInvoiceEventType.REMINDER_SENT.value,
                    "actor_user_id": self._user_id,
                    "metadata": {"reminder_number": reminders_sent},
                }
            )
            processed += 1
        return {"processed_count": processed}

    async def process_retries(self, *, limit: int = 100) -> dict[str, Any]:
        """Process failed payment retries or escalate when exhausted."""
        rows = await self.invoices_repo.list_due_for_retries(
            organization_id=self._org_id,
            limit=limit,
        )
        processed = 0
        escalated = 0
        for row in rows:
            retry_attempts = int(row["retry_attempts"]) + 1
            if retry_attempts >= int(row["retry_count"]):
                await self.invoices_repo.update_invoice(
                    organization_id=self._org_id,
                    invoice_id=row["id"],
                    patch={
                        "status": MaintenanceFeeInvoiceStatus.ESCALATED.value,
                        "retry_attempts": retry_attempts,
                        "next_retry_at": None,
                        "escalated_at": datetime.now(UTC),
                    },
                )
                await self.events_repo.insert(
                    data={
                        "organization_id": self._org_id,
                        "invoice_id": row["id"],
                        "event_type": MaintenanceFeeInvoiceEventType.ESCALATED.value,
                        "actor_user_id": self._user_id,
                    }
                )
                escalated += 1
            else:
                next_retry_at = datetime.now(UTC) + timedelta(days=int(row["retry_interval_days"]))
                await self.invoices_repo.update_invoice(
                    organization_id=self._org_id,
                    invoice_id=row["id"],
                    patch={
                        "retry_attempts": retry_attempts,
                        "next_retry_at": next_retry_at,
                        "status": MaintenanceFeeInvoiceStatus.FAILED.value,
                    },
                )
                await self.events_repo.insert(
                    data={
                        "organization_id": self._org_id,
                        "invoice_id": row["id"],
                        "event_type": MaintenanceFeeInvoiceEventType.RETRY_SCHEDULED.value,
                        "actor_user_id": self._user_id,
                        "metadata": {"retry_attempt": retry_attempts},
                    }
                )
            processed += 1
        return {"processed_count": processed, "escalated_count": escalated}

    async def record_payment(
        self,
        *,
        invoice_id: str,
        amount_minor: int | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a payment against an invoice (PSP webhook or resident pay)."""
        row = await self.invoices_repo.get_by_id(
            organization_id=self._org_id,
            invoice_id=invoice_id,
        )
        if not row:
            raise NotFoundException(
                message_key="fee_invoices.errors.invoice_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        outstanding = int(row["amount_minor"]) - int(row["amount_paid_minor"])
        if outstanding <= 0:
            raise ValidationException(
                message_key="fee_invoices.errors.invoice_already_paid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        pay_minor = amount_minor if amount_minor is not None else outstanding
        if pay_minor <= 0 or pay_minor > outstanding:
            raise ValidationException(
                message_key="fee_invoices.errors.invalid_payment_amount",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        new_paid = int(row["amount_paid_minor"]) + pay_minor
        total = int(row["amount_minor"])
        if new_paid >= total:
            status = MaintenanceFeeInvoiceStatus.PAID.value
            paid_at = datetime.now(UTC)
        else:
            status = MaintenanceFeeInvoiceStatus.PARTIALLY_PAID.value
            paid_at = None
        updated = await self.invoices_repo.update_invoice(
            organization_id=self._org_id,
            invoice_id=invoice_id,
            patch={
                "amount_paid_minor": new_paid,
                "status": status,
                "paid_at": paid_at,
                "next_retry_at": None,
            },
        )
        await self.events_repo.insert(
            data={
                "organization_id": self._org_id,
                "invoice_id": invoice_id,
                "event_type": MaintenanceFeeInvoiceEventType.PAYMENT_SUCCEEDED.value,
                "actor_user_id": actor_user_id or self._user_id,
                "metadata": {"amount_minor": pay_minor},
            }
        )
        return self._serialize_invoice(updated or row)

    async def list_resident_invoices(
        self,
        *,
        contact_unit_ids: list[str],
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List invoices for resident-owned units."""
        offset = max(page - 1, 0) * page_size
        rows, total = await self.invoices_repo.list_by_contact_units(
            organization_id=self._org_id,
            contact_unit_ids=contact_unit_ids,
            limit=page_size,
            offset=offset,
        )
        return {
            "items": [self._serialize_invoice(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
