"""Unit tests for fee invoice payment logic."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.enums import (
    FeeStartTrigger,
    MaintenanceFeeInvoiceStatus,
)
from apps.user_service.app.services.fee_invoice_service import FeeInvoiceService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _user_context() -> UserContext:
    """Build a minimal user context for service tests."""
    return UserContext(user_id="user-1", email="admin@example.com", organization_id="org-1")


class _FakeProjectsRepo:
    """In-memory fake ProjectsRepository."""

    def __init__(self, project: dict[str, Any] | None):
        self.project = project

    async def get_project(self, **_kwargs):
        """Return configured project row."""
        return self.project


class _FakeSettingsRepo:
    """In-memory fake ProjectFeeSettingsRepository."""

    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    async def get_by_project_id(self, **_kwargs):
        """Return configured settings row."""
        return self.row


class _FakeRatesRepo:
    """In-memory fake ProjectFeeRatesRepository."""

    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []

    async def list_by_project_id(self, **_kwargs):
        """Return configured rate rows."""
        return self.rows


class _FakeInvoicesRepo:
    """In-memory fake MaintenanceFeeInvoicesRepository."""

    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        due_reminders: list[dict[str, Any]] | None = None,
        due_retries: list[dict[str, Any]] | None = None,
    ):
        self.row = row
        self.rows = rows or ([row] if row else [])
        self.due_reminders = due_reminders or []
        self.due_retries = due_retries or []
        self.last_patch: dict[str, Any] | None = None
        self.inserted: list[dict[str, Any]] = []

    async def get_by_id(self, **_kwargs):
        """Return configured invoice row."""
        return self.row

    async def list_by_project(self, **_kwargs):
        """Return paginated invoice rows."""
        return self.rows, len(self.rows)

    async def list_by_contact_units(self, **_kwargs):
        """Return paginated resident invoice rows."""
        return self.rows, len(self.rows)

    async def list_due_for_reminders(self, **_kwargs):
        """Return invoices due for reminders."""
        return self.due_reminders

    async def list_due_for_retries(self, **_kwargs):
        """Return invoices due for retries."""
        return self.due_retries

    async def update_invoice(self, **_kwargs):
        """Apply patch to the fake invoice row."""
        self.last_patch = _kwargs.get("patch")
        if self.row:
            updated = dict(self.row)
            updated.update(_kwargs.get("patch") or {})
            self.row = updated
            return updated
        return None

    async def insert(self, *, data):
        """Insert a new invoice row."""
        row = {"id": f"inv-{len(self.inserted) + 1}", **data}
        self.inserted.append(row)
        return row


class _FakeEventsRepo:
    """In-memory fake MaintenanceFeeInvoiceEventsRepository."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def insert(self, *, data):
        """Record timeline event."""
        self.events.append(data)
        return data


def _service(**kwargs) -> FeeInvoiceService:
    """Build a FeeInvoiceService with fake repositories."""
    service = FeeInvoiceService.__new__(FeeInvoiceService)
    service._org_id = "org-1"
    service._user_id = "user-1"
    service.projects_repo = kwargs.get("projects_repo", _FakeProjectsRepo(None))
    service.settings_repo = kwargs.get("settings_repo", _FakeSettingsRepo(None))
    service.rates_repo = kwargs.get("rates_repo", _FakeRatesRepo())
    service.invoices_repo = kwargs.get("invoices_repo", _FakeInvoicesRepo())
    service.events_repo = kwargs.get("events_repo", _FakeEventsRepo())
    service.db_connection = kwargs.get("db_connection")
    return service


def test_month_period_returns_full_month() -> None:
    """Month period should span first through last day of the month."""
    start, end = FeeInvoiceService._month_period(date(2026, 2, 15))
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_resolve_anchor_possession_date() -> None:
    """Possession trigger should use the project possession date."""
    anchor = FeeInvoiceService._resolve_anchor_date(
        trigger=FeeStartTrigger.POSSESSION_DATE.value,
        possession_date=date(2026, 1, 1),
        onboarding_activated_at=None,
    )
    assert anchor == date(2026, 1, 1)


def test_apply_start_trigger_first_of_next_month() -> None:
    """First-of-next-month trigger should roll to the next calendar month."""
    result = FeeInvoiceService._apply_start_trigger(
        trigger=FeeStartTrigger.FIRST_OF_NEXT_MONTH.value,
        anchor=date(2026, 11, 15),
        offset_days=None,
    )
    assert result == date(2026, 12, 1)


def test_apply_start_trigger_after_days() -> None:
    """After-days trigger should add the configured offset."""
    result = FeeInvoiceService._apply_start_trigger(
        trigger=FeeStartTrigger.AFTER_DAYS.value,
        anchor=date(2026, 1, 1),
        offset_days=30,
    )
    assert result == date(2026, 1, 31)


def test_resolve_anchor_onboarding_date() -> None:
    """Onboarding trigger should use the contact unit activation timestamp."""
    activated = datetime(2026, 3, 15, tzinfo=UTC)
    anchor = FeeInvoiceService._resolve_anchor_date(
        trigger=FeeStartTrigger.ONBOARDING_DATE.value,
        possession_date=None,
        onboarding_activated_at=activated,
    )
    assert anchor == date(2026, 3, 15)


def test_apply_start_trigger_after_one_year() -> None:
    """After-one-year trigger should advance the anchor by one calendar year."""
    result = FeeInvoiceService._apply_start_trigger(
        trigger=FeeStartTrigger.AFTER_ONE_YEAR.value,
        anchor=date(2025, 6, 1),
        offset_days=None,
    )
    assert result == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_get_invoice_returns_serialized_row() -> None:
    """Successful fetch should return serialized invoice payload."""
    invoice = {
        "id": "inv-1",
        "project_id": "proj-1",
        "amount_minor": 5000,
        "amount_paid_minor": 1000,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    result = await service.get_invoice(invoice_id="inv-1")
    assert result["id"] == "inv-1"
    assert result["outstanding_amount"] == 40.0


def test_rate_for_unit_matches_config_kind() -> None:
    """Rate lookup should match unit config kind."""
    rows = [{"unit_config_kind": "apartment", "id": "r1"}]
    service = _service()
    matched = service._rate_for_unit(rate_rows=rows, config_kind="apartment")
    assert matched is not None
    assert matched["id"] == "r1"
    assert service._rate_for_unit(rate_rows=rows, config_kind="plot") is None


def test_serialize_invoice_converts_amounts() -> None:
    """Serialization should expose major currency amounts."""
    service = _service()
    payload = service._serialize_invoice(
        {
            "id": "inv-1",
            "amount_minor": 10000,
            "amount_paid_minor": 2500,
            "currency": "INR",
            "status": "issued",
        }
    )
    assert payload["amount"] == 100.0
    assert payload["amount_paid"] == 25.0
    assert payload["outstanding_amount"] == 75.0


@pytest.mark.asyncio
async def test_get_invoice_raises_when_missing() -> None:
    """Missing invoices should raise not-found."""
    service = _service(invoices_repo=_FakeInvoicesRepo(row=None))
    with pytest.raises(NotFoundException):
        await service.get_invoice(invoice_id="missing")


@pytest.mark.asyncio
async def test_get_invoice_scoped_to_project() -> None:
    """Project-scoped fetch should reject mismatched project ids."""
    invoice = {
        "id": "inv-1",
        "project_id": "proj-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    with pytest.raises(NotFoundException):
        await service.get_invoice(invoice_id="inv-1", project_id="other-proj")


@pytest.mark.asyncio
async def test_list_project_invoices() -> None:
    """Project invoice list should serialize rows and totals."""
    rows = [
        {
            "id": "inv-1",
            "amount_minor": 5000,
            "amount_paid_minor": 0,
            "currency": "INR",
            "status": "issued",
        }
    ]
    service = _service(
        projects_repo=_FakeProjectsRepo({"id": "proj-1"}),
        invoices_repo=_FakeInvoicesRepo(rows=rows),
    )
    result = await service.list_project_invoices(project_id="proj-1")
    assert result["total"] == 1
    assert result["items"][0]["amount"] == 50.0


@pytest.mark.asyncio
async def test_list_resident_invoices() -> None:
    """Resident invoice list should paginate contact-unit invoices."""
    rows = [
        {
            "id": "inv-1",
            "amount_minor": 3000,
            "amount_paid_minor": 0,
            "currency": "INR",
            "status": "issued",
        }
    ]
    service = _service(invoices_repo=_FakeInvoicesRepo(rows=rows))
    result = await service.list_resident_invoices(contact_unit_ids=["cu-1"])
    assert result["total"] == 1
    assert result["items"][0]["outstanding_amount"] == 30.0


@pytest.mark.asyncio
async def test_record_payment_marks_invoice_paid() -> None:
    """Full payment should mark the invoice as paid."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    result = await service.record_payment(invoice_id="inv-1")
    assert result["status"] == "paid"
    assert service.invoices_repo.last_patch["amount_paid_minor"] == 10000


@pytest.mark.asyncio
async def test_record_payment_partial() -> None:
    """Partial payment should leave the invoice partially paid."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    result = await service.record_payment(invoice_id="inv-1", amount_minor=4000)
    assert result["status"] == MaintenanceFeeInvoiceStatus.PARTIALLY_PAID.value
    assert service.invoices_repo.last_patch["amount_paid_minor"] == 4000


@pytest.mark.asyncio
async def test_record_payment_rejects_overpayment() -> None:
    """Payments above the outstanding balance should be rejected."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    with pytest.raises(ValidationException):
        await service.record_payment(invoice_id="inv-1", amount_minor=20000)


@pytest.mark.asyncio
async def test_record_payment_rejects_already_paid() -> None:
    """Already paid invoices should reject further payments."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 10000,
        "currency": "INR",
        "status": "paid",
    }
    service = _service(invoices_repo=_FakeInvoicesRepo(row=invoice))
    with pytest.raises(ValidationException):
        await service.record_payment(invoice_id="inv-1")


@pytest.mark.asyncio
async def test_generate_invoices_requires_configuration() -> None:
    """Generation should fail when fee configuration is incomplete."""
    service = _service(
        projects_repo=_FakeProjectsRepo({"id": "proj-1", "possession_date": None}),
        settings_repo=_FakeSettingsRepo(None),
    )
    with pytest.raises(ValidationException):
        await service.generate_invoices_for_project(project_id="proj-1")


@pytest.mark.asyncio
async def test_generate_invoices_creates_rows() -> None:
    """Generation should insert invoices for billable units."""
    project = {"id": "proj-1", "possession_date": date(2025, 1, 1)}
    settings = {
        "is_configured": True,
        "currency": "INR",
        "reminder_count": 2,
        "reminder_interval_days": 3,
    }
    rates = [
        {
            "id": "rate-1",
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": FeeStartTrigger.POSSESSION_DATE.value,
            "start_offset_days": None,
            "minimum_fee_minor": 0,
        }
    ]
    db = type("Conn", (), {})()
    db.fetch = AsyncMock(
        return_value=[
            {
                "unit_id": "unit-1",
                "project_id": "proj-1",
                "config_kind": "apartment",
                "area_sqft": 1000,
                "carpet_area_sqft": None,
                "plot_size_sqft": None,
                "contact_unit_id": "cu-1",
                "activated_at": datetime(2025, 6, 1, tzinfo=UTC),
            }
        ]
    )
    events_repo = _FakeEventsRepo()
    invoices_repo = _FakeInvoicesRepo()
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        settings_repo=_FakeSettingsRepo(settings),
        rates_repo=_FakeRatesRepo(rates),
        invoices_repo=invoices_repo,
        events_repo=events_repo,
        db_connection=db,
    )
    result = await service.generate_invoices_for_project(
        project_id="proj-1",
        reference_date=date(2026, 7, 1),
    )
    assert result["created_count"] == 1
    assert len(invoices_repo.inserted) == 1
    assert len(events_repo.events) == 1


@pytest.mark.asyncio
async def test_generate_invoices_skips_ineligible_units() -> None:
    """Generation should skip units without rates, area, or active fee windows."""
    project = {"id": "proj-1", "possession_date": date(2025, 1, 1)}
    settings = {
        "is_configured": True,
        "currency": "INR",
        "reminder_count": 0,
        "reminder_interval_days": 1,
    }
    rates = [
        {
            "id": "rate-1",
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": FeeStartTrigger.AFTER_DAYS.value,
            "start_offset_days": 3650,
            "minimum_fee_minor": 0,
        }
    ]
    db = type("Conn", (), {})()
    db.fetch = AsyncMock(
        return_value=[
            {
                "unit_id": "unit-no-rate",
                "project_id": "proj-1",
                "config_kind": "plot",
                "area_sqft": 1000,
                "carpet_area_sqft": None,
                "plot_size_sqft": None,
                "contact_unit_id": None,
                "activated_at": None,
            },
            {
                "unit_id": "unit-no-area",
                "project_id": "proj-1",
                "config_kind": "apartment",
                "area_sqft": None,
                "carpet_area_sqft": None,
                "plot_size_sqft": None,
                "contact_unit_id": None,
                "activated_at": None,
            },
            {
                "unit_id": "unit-future-fee",
                "project_id": "proj-1",
                "config_kind": "apartment",
                "area_sqft": 1000,
                "carpet_area_sqft": None,
                "plot_size_sqft": None,
                "contact_unit_id": None,
                "activated_at": None,
            },
        ]
    )
    invoices_repo = _FakeInvoicesRepo()
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        settings_repo=_FakeSettingsRepo(settings),
        rates_repo=_FakeRatesRepo(rates),
        invoices_repo=invoices_repo,
        db_connection=db,
    )
    result = await service.generate_invoices_for_project(
        project_id="proj-1",
        reference_date=date(2026, 7, 1),
    )
    assert result["created_count"] == 0
    assert result["skipped_count"] == 3


@pytest.mark.asyncio
async def test_process_reminders_updates_counters() -> None:
    """Reminder processing should increment reminders_sent."""
    due = [
        {
            "id": "inv-1",
            "reminders_sent": 0,
            "reminder_interval_days": 3,
            "reminder_count": 2,
            "due_date": date(2026, 7, 31),
        }
    ]
    invoices_repo = _FakeInvoicesRepo(due_reminders=due, row=due[0])
    events_repo = _FakeEventsRepo()
    service = _service(invoices_repo=invoices_repo, events_repo=events_repo)
    result = await service.process_reminders()
    assert result["processed_count"] == 1
    assert invoices_repo.last_patch["reminders_sent"] == 1
    assert events_repo.events[0]["event_type"] == "reminder_sent"


@pytest.mark.asyncio
async def test_process_retries_schedules_next_attempt() -> None:
    """Non-exhausted retries should schedule another attempt."""
    due = [
        {
            "id": "inv-1",
            "retry_attempts": 0,
            "retry_count": 3,
            "retry_interval_days": 2,
        }
    ]
    invoices_repo = _FakeInvoicesRepo(due_retries=due, row=due[0])
    events_repo = _FakeEventsRepo()
    service = _service(invoices_repo=invoices_repo, events_repo=events_repo)
    result = await service.process_retries()
    assert result["processed_count"] == 1
    assert result["escalated_count"] == 0
    assert invoices_repo.last_patch["status"] == MaintenanceFeeInvoiceStatus.FAILED.value


@pytest.mark.asyncio
async def test_process_retries_escalates_when_exhausted() -> None:
    """Exhausted retries should escalate the invoice."""
    due = [
        {
            "id": "inv-1",
            "retry_attempts": 2,
            "retry_count": 3,
            "retry_interval_days": 2,
        }
    ]
    invoices_repo = _FakeInvoicesRepo(due_retries=due, row=due[0])
    events_repo = _FakeEventsRepo()
    service = _service(invoices_repo=invoices_repo, events_repo=events_repo)
    result = await service.process_retries()
    assert result["escalated_count"] == 1
    assert invoices_repo.last_patch["status"] == MaintenanceFeeInvoiceStatus.ESCALATED.value
