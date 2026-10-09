"""Unit tests for the daily maintenance and club invoice run."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from apps.user_service.app.services.fee_billing_service import FeeBillingService
from apps.user_service.app.services.fee_invoice_mail import recipient_addresses
from apps.user_service.app.services.fee_invoice_pdf import build_fee_invoice_pdf
from apps.user_service.app.services.fee_late_fee import (
    build_outstanding_summary,
    build_unit_balance,
    invoice_status,
)
from libs.shared_utils.http_exceptions import ConflictException, NotFoundException

pytestmark = pytest.mark.asyncio

ORG_ID = "org-1"
PROJECT_ID = "project-1"
RUN_DATE = date(2026, 10, 1)


def _scopes(
    *,
    residential: tuple[str, str] = ("3.25", "500"),
    plots: tuple[str, str] = ("1.75", "400"),
    commercial: tuple[str, str] = ("5.50", "1200"),
) -> list[dict[str, Any]]:
    """Maintenance rate and minimum for each property type."""
    rows = (
        ("residential", residential),
        ("plots", plots),
        ("commercial", commercial),
    )
    return [
        {
            "property_type": property_type,
            "enabled": True,
            "rate_per_sqft": Decimal(rate),
            "minimum_amount": Decimal(minimum),
        }
        for property_type, (rate, minimum) in rows
    ]


def _club_scopes(
    enabled: tuple[bool, bool, bool] = (True, False, False),
) -> list[dict[str, Any]]:
    """Club scopes. Club has no rate; only enabled matters."""
    property_types = ("residential", "plots", "commercial")
    return [
        {
            "property_type": property_type,
            "enabled": is_enabled,
            "rate_per_sqft": None,
            "minimum_amount": None,
        }
        for property_type, is_enabled in zip(property_types, enabled, strict=True)
    ]


def _head(**overrides: Any) -> dict[str, Any]:
    """An active monthly maintenance head, with field overrides."""
    payload: dict[str, Any] = {
        "id": "head-maintenance",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "kind": "maintenance",
        "name": "Maintenance (CAM)",
        "line_description": "Housekeeping, security, lifts, landscaping and common-area power.",
        "status": "active",
        "frequency": "monthly",
        "billing_cycle": None,
        "cycle_anchor_month": None,
        "fee_start_rule": "first_of_next_month",
        "fee_start_date": None,
        "due_within_days": 10,
        "invoice_day": 1,
        "version": 3,
        "charge": {},
        "tax": {"applicable": False},
        "scopes": _scopes(),
    }
    payload.update(overrides)
    return payload


def _unit(**overrides: Any) -> dict[str, Any]:
    """An apartment of 100 sq ft, with field overrides."""
    payload: dict[str, Any] = {
        "id": "unit-1",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "config_kind": "apartment",
        "tower_type": "residential",
        "plot_item_id": None,
        "area_sqft": Decimal("100"),
        "carpet_area_sqft": None,
        "size_sqft": None,
        "code": "A-101",
        "has_owner_or_occupant": True,
    }
    payload.update(overrides)
    return payload


class FakeFeeBillingRepository:
    """In-memory stand-in that records inserts and honours the invoice unique key."""

    def __init__(
        self, heads: list[dict[str, Any]], units: list[dict[str, Any]] | None = None
    ) -> None:
        self.heads = heads
        self.units = units or []
        self.runs: list[dict[str, Any]] = []
        self.skips: list[dict[str, Any]] = []
        self.invoices: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.counts: list[tuple[str, int, int]] = []
        self.prior_invoices: list[dict[str, Any]] = []
        self.stored_invoices: dict[str, dict[str, Any]] = {}
        self.payments: list[dict[str, Any]] = []
        self.credits: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.unit_invoices: list[dict[str, Any]] = []
        self.project_invoices: list[dict[str, Any]] = []
        self.invoice_details: dict[str, dict[str, Any]] = {}

    async def list_active_heads(self) -> list[dict[str, Any]]:
        """Return the heads supplied to the fake."""
        return self.heads

    async def list_units(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """Return units for one project."""
        return [
            unit
            for unit in self.units
            if unit["organization_id"] == organization_id
            and unit["project_id"] == project_id
            and unit.get("has_owner_or_occupant", True)
        ]

    async def ensure_run(self, *, organization_id: str, project_id: str, run_date: date) -> str:
        """Return the existing run id or create one."""
        for run in self.runs:
            if run["project_id"] == project_id and run["run_date"] == run_date:
                return run["id"]
        run_id = f"run-{len(self.runs) + 1}"
        self.runs.append(
            {
                "id": run_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "run_date": run_date,
            }
        )
        return run_id

    async def insert_skip(self, skip: dict[str, Any]) -> bool:
        """Insert a skip once per run, reason, head, and unit."""
        key = (skip["run_id"], skip["reason"], skip.get("fee_head_id"), skip.get("unit_id"))
        if any(self._skip_key(existing) == key for existing in self.skips):
            return False
        self.skips.append(skip)
        return True

    async def find_invoice(
        self,
        *,
        project_id: str,
        unit_id: str,
        invoice_day: int,
        billing_month: date,
    ) -> str | None:
        """Return an invoice that already occupies this unique key."""
        for invoice in self.invoices:
            if (
                invoice["project_id"] == project_id
                and invoice["unit_id"] == unit_id
                and invoice["invoice_day"] == invoice_day
                and invoice["billing_month"] == billing_month
            ):
                return invoice["id"]
        return None

    async def insert_invoice(self, invoice: dict[str, Any]) -> str | None:
        """Insert an invoice unless the unique key is already taken."""
        existing = await self.find_invoice(
            project_id=invoice["project_id"],
            unit_id=invoice["unit_id"],
            invoice_day=invoice["invoice_day"],
            billing_month=invoice["billing_month"],
        )
        if existing is not None:
            return None
        invoice_date = invoice["invoice_date"]
        code = next(
            (unit.get("code") for unit in self.units if unit["id"] == invoice["unit_id"]),
            invoice["unit_id"],
        )
        invoice["unit_code"] = str(code)
        invoice["invoice_number"] = f"INV-{code}-{invoice_date:%Y%m%d}"
        stored = {
            **invoice,
            "id": f"inv-{len(self.invoices) + 1}",
        }
        self.invoices.append(stored)
        return stored["id"]

    async def insert_line(self, line: dict[str, Any]) -> None:
        """Record a line."""
        self.lines.append(line)

    async def add_run_counts(
        self, *, run_id: str, invoices_created: int, lines_skipped: int
    ) -> None:
        """Record the counts this call added."""
        self.counts.append((run_id, invoices_created, lines_skipped))

    async def list_collectible_invoices(self, *, before: date) -> list[dict[str, Any]]:
        """Earlier invoices whose due date is before this run."""
        return [
            invoice
            for invoice in self.prior_invoices
            if invoice["due_date"] < before
            and invoice["invoice_date"] < before
            and invoice.get("status") != "cancelled"
        ]

    async def get_invoice(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        unit_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a stored invoice header, or the detail when a unit is given."""
        if unit_id is not None:
            invoice = self.invoice_details.get(invoice_id)
            if invoice is None:
                return None
            if (
                invoice["organization_id"] != organization_id
                or invoice["project_id"] != project_id
                or invoice["unit_id"] != unit_id
            ):
                return None
            return invoice
        invoice = self.stored_invoices.get(invoice_id)
        if invoice is None:
            return None
        if invoice["organization_id"] != organization_id or invoice["project_id"] != project_id:
            return None
        return invoice

    async def list_payments(self, *, invoice_id: str) -> list[dict[str, Any]]:
        """Payments already recorded for one invoice."""
        return [payment for payment in self.payments if payment["invoice_id"] == invoice_id]

    async def insert_payment(self, payment: dict[str, Any]) -> str:
        """Record a payment."""
        self.payments.append(payment)
        return f"pay-{len(self.payments)}"

    async def update_invoice_status(self, *, invoice_id: str, status: str) -> None:
        """Update a stored invoice status."""
        if invoice_id in self.stored_invoices:
            self.stored_invoices[invoice_id]["status"] = status
        detail = self.invoice_details.get(invoice_id)
        if detail is not None:
            detail["status"] = status
        for invoice in [*self.invoices, *self.prior_invoices]:
            if str(invoice["id"]) == invoice_id:
                invoice["status"] = status

    async def list_open_credits(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Unapplied credit rows for one project."""
        return [
            credit
            for credit in self.credits
            if credit["organization_id"] == organization_id
            and credit["project_id"] == project_id
            and credit["amount"] > 0
        ]

    async def list_open_invoices(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Open invoices from priors, stored headers, and newly issued invoices."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        sources = [*self.stored_invoices.values(), *self.prior_invoices, *self.invoices]
        for invoice in sources:
            invoice_id = str(invoice["id"])
            if invoice_id in seen:
                continue
            if invoice.get("organization_id") != organization_id:
                continue
            if invoice.get("project_id") != project_id:
                continue
            if invoice.get("status") == "cancelled":
                continue
            seen.add(invoice_id)
            paid = sum(
                (Decimal(str(payment["amount"])) for payment in invoice.get("payments") or []),
                Decimal("0"),
            )
            if Decimal(str(invoice["total_amount"])) - paid > 0:
                rows.append(invoice)
        return rows

    async def unit_credit(self, *, organization_id: str, project_id: str, unit_id: str) -> Decimal:
        """Remaining credit for one unit."""
        return sum(
            (
                credit["amount"]
                for credit in self.credits
                if credit["organization_id"] == organization_id
                and credit["project_id"] == project_id
                and credit["unit_id"] == unit_id
                and credit["amount"] > 0
            ),
            Decimal("0"),
        )

    async def insert_credit(self, credit: dict[str, Any]) -> str:
        """Store a surplus receipt."""
        stored = {**credit, "id": f"credit-{len(self.credits) + 1}"}
        self.credits.append(stored)
        return stored["id"]

    async def consume_credit(self, *, credit_id: str, amount: Decimal) -> None:
        """Reduce one credit row."""
        for credit in self.credits:
            if credit["id"] == credit_id:
                credit["amount"] = credit["amount"] - amount
                return

    async def list_unit_invoices(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> list[dict[str, Any]]:
        """Invoices supplied for one unit."""
        return [
            invoice
            for invoice in self.unit_invoices
            if invoice["organization_id"] == organization_id
            and invoice["project_id"] == project_id
            and invoice["unit_id"] == unit_id
        ]

    async def list_project_invoices(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str | None,
        unit_ids: list[str] | None = None,
        status: str | None,
        billing_months: list[date],
        as_of: date,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filter the supplied project invoices the way the query does."""
        matched = [
            invoice
            for invoice in self.project_invoices
            if invoice["organization_id"] == organization_id
            and invoice["project_id"] == project_id
            and (unit_id is None or invoice["unit_id"] == unit_id)
            and (unit_ids is None or invoice["unit_id"] in unit_ids)
            and (
                status is None
                or invoice_status(invoice["status"], invoice["due_date"], as_of) == status
            )
            and (not billing_months or invoice["billing_month"] in billing_months)
        ]
        matched.sort(key=lambda invoice: invoice["billing_month"], reverse=True)
        return matched[offset : offset + limit], len(matched)

    @staticmethod
    def _skip_key(skip: dict[str, Any]) -> tuple[Any, ...]:
        """Identity of a skip row within one run."""
        return (
            skip["run_id"],
            skip["reason"],
            skip.get("fee_head_id"),
            skip.get("unit_id"),
        )


def _service(fake: FakeFeeBillingRepository) -> FeeBillingService:
    """Service wired to the fake repository."""
    return FeeBillingService(db_connection=None, repository=fake)  # type: ignore[arg-type]


async def test_maintenance_bills_the_greater_amount():
    """3.25 × 100 is 325, so the 500 minimum is billed, then tax is added and rounded."""
    head = _head(tax={"applicable": True, "rate_percent": Decimal("18.5")})
    fake = FakeFeeBillingRepository([head], [_unit()])
    result = await _service(fake).issue_due(run_date=RUN_DATE)

    assert result["invoice_ids"] == ["inv-1"]
    line = fake.lines[0]
    invoice = fake.invoices[0]
    assert line["taxable_amount"] == Decimal("500.00")
    assert line["rate"] == Decimal("3.25")
    assert line["minimum_amount"] == Decimal("500.00")
    assert line["area_or_quantity"] == Decimal("100.00")
    assert line["fee_head_version"] == 3
    assert line["tax_amount"] == Decimal("92.50")
    assert invoice["taxable_amount"] == Decimal("500.00")
    assert invoice["tax_amount"] == Decimal("92.50")
    assert invoice["total_amount"] == Decimal("593")
    assert invoice["round_off_amount"] == Decimal("0.50")
    assert invoice["status"] == "issued"
    assert invoice["due_date"] == date(2026, 10, 11)
    assert invoice["billing_month"] == date(2026, 10, 1)


async def test_club_uses_flat_amount():
    """Club bills charge.amount, including on the specific start date itself."""
    head = _head(
        id="head-club",
        kind="club",
        name="Club charges",
        line_description="Clubhouse membership — pool, gym, courts and lounges.",
        charge={"amount": Decimal("1500")},
        fee_start_rule="specific_date",
        fee_start_date=RUN_DATE,
        scopes=_club_scopes(),
    )
    fake = FakeFeeBillingRepository([head], [_unit()])
    await _service(fake).issue_due(run_date=RUN_DATE)

    line = fake.lines[0]
    assert line["taxable_amount"] == Decimal("1500.00")
    assert line["rate"] == Decimal("1500.00")
    assert line["area_or_quantity"] == Decimal("1.00")
    assert line["minimum_amount"] is None
    assert fake.invoices[0]["total_amount"] == Decimal("1500")


async def test_heads_on_the_same_invoice_day_merge():
    """Maintenance and club due on day 1 become one invoice with two lines."""
    maintenance = _head()
    club = _head(
        id="head-club",
        kind="club",
        name="Club charges",
        line_description="Clubhouse membership.",
        charge={"amount": Decimal("1500")},
        scopes=_club_scopes(),
    )
    fake = FakeFeeBillingRepository([maintenance, club], [_unit()])
    result = await _service(fake).issue_due(run_date=RUN_DATE)

    assert result["invoice_ids"] == ["inv-1"]
    assert len(fake.lines) == 2
    assert {line["fee_head_id"] for line in fake.lines} == {"head-maintenance", "head-club"}
    assert fake.invoices[0]["taxable_amount"] == Decimal("2000.00")
    assert fake.invoices[0]["invoice_day"] == 1


async def test_head_on_another_invoice_day_stays_separate():
    """A head whose invoice day is not today is not merged onto today's invoice."""
    today = _head(
        id="head-today",
        kind="club",
        charge={"amount": Decimal("1500")},
        scopes=_club_scopes(),
    )
    later = _head(id="head-later", invoice_day=10)
    fake = FakeFeeBillingRepository([today, later], [_unit()])
    await _service(fake).issue_due(run_date=RUN_DATE)

    assert len(fake.invoices) == 1
    assert [line["fee_head_id"] for line in fake.lines] == ["head-today"]


async def test_specific_date_in_the_future_is_excluded():
    """A head that has not reached its start date is not billed and not skipped."""
    head = _head(fee_start_rule="specific_date", fee_start_date=date(2026, 11, 1))
    fake = FakeFeeBillingRepository([head], [_unit()])
    result = await _service(fake).issue_due(run_date=RUN_DATE)

    assert result["invoice_ids"] == []
    assert result["skip_counts"] == {
        "electricity_not_ready": 0,
        "pro_rata_not_ready": 0,
        "missing_area": 0,
    }
    assert not fake.runs
    assert not fake.invoices


async def test_repeat_call_does_not_insert_again():
    """The second call on the same day finds the invoice and writes nothing new."""
    fake = FakeFeeBillingRepository([_head()], [_unit()])
    service = _service(fake)
    first = await service.issue_due(run_date=RUN_DATE)
    second = await service.issue_due(run_date=RUN_DATE)

    assert first["invoice_ids"] == ["inv-1"]
    assert second["invoice_ids"] == []
    assert len(fake.invoices) == 1
    assert len(fake.lines) == 1
    assert fake.counts == [("run-1", 1, 0), ("run-1", 0, 0)]


async def test_named_skip_reasons_are_recorded():
    """Those three cases are named on the run and do not create invoices."""
    electricity = _head(id="head-electricity", kind="electricity", charge={"amount": None})
    pro_rata = _head(
        id="head-pro-rata",
        frequency="quarterly",
        billing_cycle="pro_rata",
        cycle_anchor_month=None,
    )
    maintenance = _head(id="head-maintenance")
    fake = FakeFeeBillingRepository(
        [electricity, pro_rata, maintenance],
        [_unit(area_sqft=None)],
    )
    result = await _service(fake).issue_due(run_date=RUN_DATE)

    assert result["invoice_ids"] == []
    assert result["skip_counts"] == {
        "electricity_not_ready": 1,
        "pro_rata_not_ready": 1,
        "missing_area": 1,
    }
    assert not fake.invoices
    reasons = {skip["reason"]: skip for skip in fake.skips}
    assert reasons["electricity_not_ready"]["fee_head_id"] == "head-electricity"
    assert reasons["electricity_not_ready"]["unit_id"] is None
    assert reasons["pro_rata_not_ready"]["fee_head_id"] == "head-pro-rata"
    assert reasons["missing_area"]["unit_id"] == "unit-1"
    assert reasons["missing_area"]["fee_head_id"] == "head-maintenance"


async def test_unassigned_unit_is_not_billed():
    """A unit with no owner, tenant, or family member gets no invoice."""
    assigned = _unit(id="assigned")
    empty = _unit(id="empty", has_owner_or_occupant=False)
    fake = FakeFeeBillingRepository([_head()], [assigned, empty])

    await _service(fake).issue_due(run_date=RUN_DATE)

    assert {invoice["unit_id"] for invoice in fake.invoices} == {"assigned"}


async def test_quarterly_head_bills_only_in_anchor_months():
    """Calendar quarterly (anchor January) bills in October and not in February."""
    head = _head(
        frequency="quarterly",
        billing_cycle="calendar_year",
        cycle_anchor_month=1,
    )
    fake = FakeFeeBillingRepository([head], [_unit()])
    service = _service(fake)

    february = await service.issue_due(run_date=date(2026, 2, 1))
    october = await service.issue_due(run_date=RUN_DATE)

    assert february["invoice_ids"] == []
    assert october["invoice_ids"] == ["inv-1"]


async def test_area_follows_the_property_type():
    """Apartment uses area, commercial uses carpet, and a plot uses its size."""
    head = _head(
        scopes=_scopes(
            residential=("2", "0"),
            commercial=("3", "0"),
            plots=("4", "0"),
        )
    )
    units = [
        _unit(id="apt", area_sqft=Decimal("100")),
        _unit(
            id="shop",
            config_kind="commercial",
            tower_type=None,
            area_sqft=None,
            carpet_area_sqft=Decimal("50"),
        ),
        _unit(
            id="plot",
            config_kind="plot",
            tower_type=None,
            area_sqft=None,
            size_sqft=Decimal("25"),
            plot_item_id="plot-item-1",
        ),
    ]
    fake = FakeFeeBillingRepository([head], units)
    await _service(fake).issue_due(run_date=RUN_DATE)

    by_unit = {line["area_or_quantity"]: line["taxable_amount"] for line in fake.lines}
    assert by_unit[Decimal("100.00")] == Decimal("200.00")
    assert by_unit[Decimal("50.00")] == Decimal("150.00")
    assert by_unit[Decimal("25.00")] == Decimal("100.00")


def _prior_invoice(**overrides: Any) -> dict[str, Any]:
    """An October club invoice of ₹1,770, due on the 6th, with 18% interest."""
    payload: dict[str, Any] = {
        "id": "inv-oct",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "due_date": date(2026, 10, 6),
        "invoice_date": date(2026, 10, 1),
        "billing_month": date(2026, 10, 1),
        "total_amount": Decimal("1770.00"),
        "status": "issued",
        "lines": [
            {
                "id": "line-oct",
                "fee_head_id": "head-club",
                "kind": "club",
                "fee_head_version": 3,
                "description": "Clubhouse membership.",
                "line_total": Decimal("1770.00"),
                "line_role": "charge",
                "late_fee": {"mode": "interest", "annual_percent": "18"},
            }
        ],
        "payments": [],
        "late_fees_posted": [],
    }
    payload.update(overrides)
    return payload


def _november_club() -> dict[str, Any]:
    """Club head due on 1 November."""
    return _head(
        id="head-club",
        kind="club",
        name="Club charges",
        line_description="Clubhouse membership.",
        invoice_day=1,
        charge={"amount": Decimal("1500")},
        scopes=_club_scopes(),
    )


async def test_next_invoice_adds_one_month_of_interest():
    """A bill still open on 1 November picks up one month of interest."""
    fake = FakeFeeBillingRepository([_november_club()], [_unit()])
    fake.prior_invoices = [_prior_invoice()]
    await _service(fake).issue_due(run_date=date(2026, 11, 1))

    late = next(line for line in fake.lines if line["line_role"] == "late_fee")
    assert late["line_total"] == Decimal("27.00")
    assert late["source_invoice_id"] == "inv-oct"
    assert fake.invoices[0]["total_amount"] == Decimal("1527")


async def test_payment_on_the_due_date_adds_no_late_fee():
    """A bill paid on the due date is not overdue."""
    prior = _prior_invoice(
        status="paid",
        payments=[{"amount": Decimal("1770.00"), "paid_on": date(2026, 10, 6)}],
    )
    fake = FakeFeeBillingRepository([_november_club()], [_unit()])
    fake.prior_invoices = [prior]
    await _service(fake).issue_due(run_date=date(2026, 11, 1))

    assert [line["line_role"] for line in fake.lines] == ["charge"]


async def test_advance_credit_pays_the_next_invoice():
    """Credit from an earlier receipt pays the next invoice and skips late fee."""
    prior = _prior_invoice(
        status="paid",
        payments=[{"amount": Decimal("1770.00"), "paid_on": date(2026, 10, 1)}],
    )
    fake = FakeFeeBillingRepository([_november_club()], [_unit()])
    fake.prior_invoices = [prior]
    fake.credits = [
        {
            "id": "credit-1",
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "unit_id": "unit-1",
            "source_invoice_id": "inv-oct",
            "amount": Decimal("3000.00"),
            "paid_on": date(2026, 10, 1),
            "mode": "neft_rtgs",
            "reference": None,
        }
    ]
    await _service(fake).issue_due(run_date=date(2026, 11, 1))

    issued = fake.invoices[0]
    assert [line["line_role"] for line in fake.lines] == ["charge"]
    assert issued["status"] == "paid"
    applied = [payment for payment in fake.payments if payment["invoice_id"] == issued["id"]]
    assert applied[0]["amount"] == Decimal("1500.00")
    assert applied[0]["paid_on"] == date(2026, 10, 1)
    assert fake.credits[0]["amount"] == Decimal("1500.00")


async def test_flat_late_fee_uses_the_step_reached():
    """26 days overdue reaches the 15-day step, not the sum of both steps."""
    prior = _prior_invoice(
        lines=[
            {
                "id": "line-oct",
                "fee_head_id": "head-club",
                "kind": "club",
                "fee_head_version": 3,
                "description": "Clubhouse membership.",
                "line_total": Decimal("1770.00"),
                "line_role": "charge",
                "late_fee": {
                    "mode": "flat",
                    "steps": [
                        {"days_overdue": 1, "amount": "100.00"},
                        {"days_overdue": 15, "amount": "250.00"},
                    ],
                },
            }
        ]
    )
    fake = FakeFeeBillingRepository([_november_club()], [_unit()])
    fake.prior_invoices = [prior]
    await _service(fake).issue_due(run_date=date(2026, 11, 1))

    late = next(line for line in fake.lines if line["line_role"] == "late_fee")
    assert late["line_total"] == Decimal("250.00")


async def test_balance_splits_arrears_late_fee_and_current():
    """Amount due is the open October invoice plus November's late fee and charges."""
    october = _prior_invoice()
    november = {
        "id": "inv-nov",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "billing_month": date(2026, 11, 1),
        "invoice_date": date(2026, 11, 1),
        "due_date": date(2026, 11, 11),
        "status": "issued",
        "total_amount": Decimal("1527.00"),
        "payments": [],
        "lines": [
            {
                "line_role": "charge",
                "kind": "club",
                "description": "Clubhouse membership.",
                "line_total": Decimal("1500.00"),
            },
            {
                "line_role": "late_fee",
                "kind": "club",
                "description": "Late fee — Clubhouse membership.",
                "line_total": Decimal("27.00"),
            },
        ],
    }
    balance = build_unit_balance([october, november])

    assert balance["arrears"] == "1770.00"
    assert balance["late_fee"] == "27.00"
    assert balance["current_charges"] == "1500.00"
    assert balance["credit"] == "0.00"
    assert balance["amount_due"] == "3297.00"


async def test_outstanding_summary_for_unpaid_bills():
    """Total outstanding is the unpaid bills, with overdue and late fee called out."""
    march = _prior_invoice(
        id="inv-mar",
        billing_month=date(2026, 3, 1),
        invoice_date=date(2026, 3, 5),
        due_date=date(2026, 3, 15),
        total_amount=Decimal("4000.00"),
        lines=[{"line_role": "charge", "line_total": Decimal("4000.00")}],
    )
    april = _prior_invoice(
        id="inv-apr",
        billing_month=date(2026, 4, 1),
        invoice_date=date(2026, 4, 5),
        due_date=date(2026, 4, 15),
        total_amount=Decimal("4520.00"),
        lines=[
            {"line_role": "charge", "line_total": Decimal("4200.00")},
            {"line_role": "late_fee", "line_total": Decimal("320.00")},
        ],
    )
    summary = build_outstanding_summary(
        [april, march],
        as_of=date(2026, 10, 5),
    )

    assert summary["total_outstanding"] == "8520.00"
    assert summary["overdue"] is True
    assert summary["unpaid_count"] == 2
    assert summary["billing_months"] == ["2026-03-01", "2026-04-01"]
    assert summary["includes_late_fee"] is True

    covered = build_outstanding_summary(
        [march, april],
        credit=Decimal("8520.00"),
        as_of=date(2026, 10, 5),
    )
    assert covered["total_outstanding"] == "0.00"
    assert covered["unpaid_count"] == 2


async def test_invoice_number_uses_unit_code_and_date():
    """One number per unit and invoice date. The same day stays one invoice."""
    unit = _unit(id="unit-1", code="LUX-B2101")
    maintenance = _head()
    club = _head(
        id="head-club",
        kind="club",
        name="Club charges",
        line_description="Clubhouse membership.",
        charge={"amount": Decimal("1500")},
        scopes=_club_scopes(),
    )
    later = _head(id="head-later", invoice_day=5)
    fake = FakeFeeBillingRepository([maintenance, club, later], [unit])
    service = _service(fake)
    await service.issue_due(run_date=date(2026, 10, 1))
    await service.issue_due(run_date=date(2026, 10, 5))

    october_first = [
        invoice["invoice_number"]
        for invoice in fake.invoices
        if invoice["invoice_date"] == date(2026, 10, 1)
    ]
    october_fifth = [
        invoice["invoice_number"]
        for invoice in fake.invoices
        if invoice["invoice_date"] == date(2026, 10, 5)
    ]
    assert october_first == ["INV-LUX-B2101-20261001"]
    assert october_fifth == ["INV-LUX-B2101-20261005"]


async def test_new_invoice_notifies_once():
    """A new invoice is queued once. The same day does not queue it again."""
    sent: list[dict[str, Any]] = []

    async def capture(notice: dict[str, Any]) -> None:
        sent.append(notice)

    fake = FakeFeeBillingRepository([_head()], [_unit(code="LUX-B2101")])
    service = _service(fake)
    service.mailer = capture
    await service.issue_due(run_date=date(2026, 10, 1))
    await service.issue_due(run_date=date(2026, 10, 1))

    assert len(sent) == 1
    assert sent[0]["invoice_number"] == "INV-LUX-B2101-20261001"
    assert sent[0]["unit_code"] == "LUX-B2101"
    pdf = build_fee_invoice_pdf({**sent[0], "project_name": "Luxe"})
    assert b"INV-LUX-B2101-20261001" in pdf
    assert pdf.startswith(b"%PDF-")


async def test_mail_failure_keeps_the_invoice():
    """A mailer error leaves the issued invoice in place."""

    async def boom(_notice: dict[str, Any]) -> None:
        raise RuntimeError("smtp down")

    fake = FakeFeeBillingRepository([_head()], [_unit()])
    service = _service(fake)
    service.mailer = boom
    result = await service.issue_due(run_date=date(2026, 10, 1))

    assert result["invoice_ids"] == ["inv-1"]
    assert fake.invoices[0]["invoice_number"] == "INV-A-101-20261001"


async def test_recipient_addresses_skip_blank_email():
    """Owner, tenant, and family emails are used once. A blank address is left out."""
    rows = [
        {
            "first_name": "Ada",
            "emails": [{"email": "ada@example.com", "is_primary": True}],
        },
        {
            "first_name": "Ada Again",
            "emails": [{"email": "Ada@example.com", "is_primary": True}],
        },
        {"first_name": "Bob", "emails": []},
        {"first_name": "", "emails": '[{"email": "sam@example.com"}]'},
    ]
    assert recipient_addresses(rows) == [
        ("Ada", "ada@example.com"),
        ("there", "sam@example.com"),
    ]


async def test_overpayment_is_kept_as_invoice_credit():
    """A receipt larger than the invoice is saved, and the extra is credit."""
    fake = FakeFeeBillingRepository([], [])
    fake.stored_invoices["inv-oct"] = {
        "id": "inv-oct",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "billing_month": date(2026, 10, 1),
        "invoice_date": date(2026, 10, 1),
        "total_amount": Decimal("1770.00"),
        "status": "issued",
        "payments": [],
    }
    service = _service(fake)
    partial = await service.record_payment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
        amount=Decimal("500"),
        paid_on=date(2026, 10, 15),
        mode="neft_rtgs",
        reference="UTR998877",
    )
    assert partial["status"] == "partial"
    assert partial["outstanding"] == "1270.00"
    assert partial["mode"] == "neft_rtgs"
    assert partial["reference"] == "UTR998877"
    assert fake.payments[0]["mode"] == "neft_rtgs"

    overpaid = await service.record_payment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
        amount=Decimal("2000"),
        paid_on=date(2026, 10, 16),
        mode="cash",
        reference=None,
    )
    assert overpaid["status"] == "paid"
    assert overpaid["amount_paid"] == "1770.00"
    assert overpaid["outstanding"] == "0.00"
    assert overpaid["credit"] == "730.00"
    assert fake.credits[0]["amount"] == Decimal("730.00")


async def test_credit_lowers_the_unit_amount_due():
    """Credit on a paid invoice reduces what the unit still owes."""
    october = _prior_invoice(
        status="paid",
        payments=[{"amount": Decimal("1770.00"), "paid_on": date(2026, 10, 1)}],
    )
    november = _prior_invoice(
        id="inv-nov",
        billing_month=date(2026, 11, 1),
        invoice_date=date(2026, 11, 1),
        due_date=date(2026, 11, 11),
        total_amount=Decimal("1500.00"),
        lines=[
            {
                "line_role": "charge",
                "kind": "club",
                "description": "Clubhouse membership.",
                "line_total": Decimal("1500.00"),
            }
        ],
    )
    balance = build_unit_balance(
        [october, november],
        credit=Decimal("230.00"),
        as_of=date(2026, 11, 1),
    )
    assert balance["credit"] == "230.00"
    assert balance["amount_due"] == "1270.00"


def _listed_invoice(**overrides: Any) -> dict[str, Any]:
    """One invoice header the list query would return."""
    payload: dict[str, Any] = {
        "id": "inv-oct",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "unit_code": "A-0903",
        "invoice_number": "INV-A-0903-20261001",
        "billing_month": date(2026, 10, 1),
        "invoice_date": date(2026, 10, 1),
        "due_date": date(2026, 10, 11),
        "status": "partial",
        "total_amount": Decimal("1770.00"),
        "amount_paid": Decimal("500.00"),
        "pdf_path": "fee-invoices/inv-oct.pdf",
    }
    payload.update(overrides)
    return payload


async def test_list_invoices_filters_unit_status_and_month():
    """The list keeps the requested unit, status, and billing month."""
    fake = FakeFeeBillingRepository([], [])
    fake.project_invoices = [
        _listed_invoice(),
        _listed_invoice(
            id="inv-sep",
            billing_month=date(2026, 9, 1),
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 9, 11),
            status="paid",
            amount_paid=Decimal("1770.00"),
        ),
        _listed_invoice(id="inv-other", unit_id="unit-2", unit_code="B-0101"),
    ]
    items, total = await _service(fake).list_invoices(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id="unit-1",
        status="partial",
        billing_months=[date(2026, 10, 15)],
        page=1,
        page_size=20,
        as_of=date(2026, 10, 5),
    )
    assert total == 1
    assert items[0]["id"] == "inv-oct"
    assert items[0]["unit_code"] == "A-0903"
    assert items[0]["billing_month"] == "2026-10-01"
    assert items[0]["status"] == "partial"
    assert items[0]["outstanding"] == "1270.00"
    assert items[0]["pdf_path"] == "fee-invoices/inv-oct.pdf"


async def test_list_invoices_keeps_only_given_units():
    """A unit list returns invoices for those units only."""
    fake = FakeFeeBillingRepository([], [])
    fake.project_invoices = [
        _listed_invoice(id="inv-a", unit_id="unit-1", unit_code="A-0903"),
        _listed_invoice(id="inv-b", unit_id="unit-2", unit_code="B-0101"),
        _listed_invoice(id="inv-c", unit_id="unit-3", unit_code="C-0202"),
    ]
    items, total = await _service(fake).list_invoices(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=None,
        unit_ids=["unit-1", "unit-3"],
        status=None,
        billing_months=None,
        page=1,
        page_size=20,
        as_of=date(2026, 10, 5),
    )
    assert total == 2
    assert {item["id"] for item in items} == {"inv-a", "inv-c"}


async def test_open_invoice_is_overdue_after_due_date():
    """An unpaid invoice is overdue once the due date has passed."""
    fake = FakeFeeBillingRepository([], [])
    fake.project_invoices = [
        _listed_invoice(),
        _listed_invoice(
            id="inv-paid",
            status="paid",
            amount_paid=Decimal("1770.00"),
        ),
    ]
    service = _service(fake)
    overdue, overdue_total = await service.list_invoices(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=None,
        status="overdue",
        billing_months=None,
        page=1,
        page_size=20,
        as_of=date(2026, 10, 12),
    )
    still_partial, partial_total = await service.list_invoices(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=None,
        status="partial",
        billing_months=None,
        page=1,
        page_size=20,
        as_of=date(2026, 10, 12),
    )

    assert overdue_total == 1
    assert overdue[0]["id"] == "inv-oct"
    assert overdue[0]["status"] == "overdue"
    assert partial_total == 0
    assert still_partial == []


async def test_invoice_detail_returns_lines_and_breakdown():
    """A resident invoice includes each line's tax parts and the receipts."""
    fake = FakeFeeBillingRepository([], [])
    fake.invoice_details["inv-oct"] = {
        "id": "inv-oct",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "unit_code": "A-101",
        "invoice_number": "INV-A-101-20261005",
        "billing_month": date(2026, 10, 1),
        "invoice_date": date(2026, 10, 5),
        "due_date": date(2026, 10, 15),
        "status": "partial",
        "taxable_amount": Decimal("8000.00"),
        "tax_amount": Decimal("960.00"),
        "round_off_amount": Decimal("2.00"),
        "total_amount": Decimal("8962.00"),
        "pdf_path": "fee-invoices/inv-oct.pdf",
        "lines": [
            {
                "id": "line-1",
                "kind": "maintenance",
                "line_role": "charge",
                "description": "Maintenance",
                "area_or_quantity": Decimal("1200.00"),
                "rate": Decimal("3.25"),
                "minimum_amount": Decimal("500.00"),
                "taxable_amount": Decimal("3900.00"),
                "tax_amount": Decimal("702.00"),
                "line_total": Decimal("4602.00"),
                "source_invoice_id": None,
                "started_months": None,
                "days_overdue": None,
            }
        ],
        "payments": [
            {
                "amount": Decimal("2000.00"),
                "paid_on": date(2026, 10, 6),
                "mode": "upi",
                "reference": "UTR123",
            }
        ],
    }
    service = _service(fake)
    detail = await service.invoice_detail(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id="unit-1",
        invoice_id="inv-oct",
        as_of=date(2026, 10, 5),
    )

    assert detail["total_amount"] == "8962.00"
    assert detail["taxable_amount"] == "8000.00"
    assert detail["tax_amount"] == "960.00"
    assert detail["round_off_amount"] == "2.00"
    assert detail["amount_paid"] == "2000.00"
    assert detail["outstanding"] == "6962.00"
    assert detail["status"] == "partial"
    assert detail["lines"][0]["tax_amount"] == "702.00"
    assert detail["lines"][0]["line_total"] == "4602.00"
    assert detail["payments"][0]["mode"] == "upi"
    assert detail["payments"][0]["reference"] == "UTR123"
    assert detail["pdf_path"] == "fee-invoices/inv-oct.pdf"
    assert detail["activities"] == []

    with pytest.raises(NotFoundException):
        await service.invoice_detail(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            unit_id="unit-2",
            invoice_id="inv-oct",
            as_of=date(2026, 10, 5),
        )


def _issued_invoice(**overrides: Any) -> dict[str, Any]:
    """One unpaid invoice an admin can cancel."""
    payload: dict[str, Any] = {
        "id": "inv-oct",
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "unit_id": "unit-1",
        "unit_code": "A-101",
        "invoice_number": "INV-A-101-20261001",
        "billing_month": date(2026, 10, 1),
        "invoice_date": date(2026, 10, 1),
        "due_date": date(2026, 10, 11),
        "status": "issued",
        "taxable_amount": Decimal("1500.00"),
        "tax_amount": Decimal("270.00"),
        "round_off_amount": Decimal("0.00"),
        "total_amount": Decimal("1770.00"),
        "pdf_path": "fee-invoices/inv-oct.pdf",
        "lines": [],
        "payments": [],
    }
    payload.update(overrides)
    return payload


def _store_invoice(fake: FakeFeeBillingRepository, invoice: dict[str, Any]) -> None:
    """Put one invoice where both cancel and detail can read it."""
    fake.stored_invoices[invoice["id"]] = invoice
    fake.invoice_details[invoice["id"]] = invoice


async def test_cancel_unpaid_invoice():
    """An issued invoice, even after its due date, becomes cancelled."""
    fake = FakeFeeBillingRepository([], [])
    _store_invoice(fake, _issued_invoice())
    detail = await _service(fake).cancel_invoice(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
        as_of=date(2026, 10, 20),
    )
    assert detail["status"] == "cancelled"
    assert detail["outstanding"] == "0.00"
    assert fake.stored_invoices["inv-oct"]["status"] == "cancelled"


async def test_cancel_rejects_paid_or_partial():
    """A payment, or a closed invoice, blocks cancel."""
    fake = FakeFeeBillingRepository([], [])
    _store_invoice(fake, _issued_invoice(status="partial"))
    service = _service(fake)
    with pytest.raises(ConflictException):
        await service.cancel_invoice(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
        )
    fake.payments.append({"invoice_id": "inv-oct", "amount": Decimal("100.00")})
    fake.stored_invoices["inv-oct"]["status"] = "issued"
    with pytest.raises(ConflictException):
        await service.cancel_invoice(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
        )
    assert fake.stored_invoices["inv-oct"]["status"] == "issued"


async def test_cancelled_invoice_is_left_out_of_balance():
    """A cancelled bill does not add to the unit balance or the pending card."""
    invoice = _issued_invoice(status="cancelled", lines=[], payments=[])
    balance = build_unit_balance([invoice], as_of=date(2026, 10, 20))
    summary = build_outstanding_summary([invoice], as_of=date(2026, 10, 20))
    assert balance["amount_due"] == "0.00"
    assert balance["invoices"] == []
    assert summary["total_outstanding"] == "0.00"
    assert summary["unpaid_count"] == 0


async def test_payment_rejects_a_cancelled_invoice():
    """A cancelled invoice cannot take a new receipt."""
    fake = FakeFeeBillingRepository([], [])
    fake.stored_invoices["inv-oct"] = _issued_invoice(status="cancelled")
    with pytest.raises(ConflictException):
        await _service(fake).record_payment(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
            amount=Decimal("100"),
            paid_on=date(2026, 10, 12),
            mode="cash",
            reference=None,
        )


async def test_collection_summary_for_one_month():
    """September cards skip a cancelled bill and an invoice from another month."""
    fake = FakeFeeBillingRepository([], [])
    fake.project_invoices = [
        _listed_invoice(
            id="paid",
            billing_month=date(2026, 9, 1),
            due_date=date(2026, 9, 11),
            status="paid",
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),
        ),
        _listed_invoice(
            id="overdue",
            billing_month=date(2026, 9, 1),
            due_date=date(2026, 9, 1),
            status="partial",
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("100.00"),
        ),
        _listed_invoice(
            id="open",
            billing_month=date(2026, 9, 1),
            due_date=date(2026, 10, 20),
            status="issued",
            total_amount=Decimal("200.00"),
            amount_paid=Decimal("0.00"),
        ),
        _listed_invoice(
            id="cancelled",
            billing_month=date(2026, 9, 1),
            status="cancelled",
            total_amount=Decimal("900.00"),
            amount_paid=Decimal("0.00"),
        ),
        _listed_invoice(id="october", billing_month=date(2026, 10, 1)),
    ]
    summary = await _service(fake).collection_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        billing_months=[date(2026, 9, 15)],
        as_of=date(2026, 10, 9),
    )
    assert summary["billing_months"] == ["2026-09-01"]
    assert summary["invoiced_amount"] == "1700.00"
    assert summary["invoice_count"] == 3
    assert summary["collected_amount"] == "1100.00"
    assert summary["collected_percent"] == 65
    assert summary["outstanding_amount"] == "600.00"
    assert summary["open_count"] == 2
    assert summary["overdue_count"] == 1
    assert summary["overdue_amount"] == "400.00"


async def test_collection_summary_includes_every_month():
    """With no month filter, every non-cancelled invoice is included."""
    fake = FakeFeeBillingRepository([], [])
    fake.project_invoices = [
        _listed_invoice(
            id="sep",
            billing_month=date(2026, 9, 1),
            total_amount=Decimal("261961.00"),
            amount_paid=Decimal("213508.20"),
            status="partial",
            due_date=date(2026, 9, 10),
        ),
        _listed_invoice(
            id="oct",
            billing_month=date(2026, 10, 1),
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            status="issued",
            due_date=date(2026, 10, 20),
        ),
    ]
    summary = await _service(fake).collection_summary(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        billing_months=None,
        as_of=date(2026, 10, 9),
    )
    assert summary["billing_months"] == []
    assert summary["invoiced_amount"] == "262061.00"
    assert summary["collected_percent"] == 81
    assert summary["invoice_count"] == 2


async def test_reminder_notice_for_an_unpaid_invoice():
    """An issued or partly paid invoice can be reminded."""
    fake = FakeFeeBillingRepository([], [])
    _store_invoice(fake, _issued_invoice(project_name="Luxe"))
    notice = await _service(fake).reminder_notice(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
    )
    assert notice["invoice_number"] == "INV-A-101-20261001"
    assert notice["organization_id"] == ORG_ID
    assert notice["project_id"] == PROJECT_ID
    assert notice["project_name"] == "Luxe"
    assert notice["total_amount"] == "1770.00"
    assert notice["remind_on"]

    fake.stored_invoices["inv-oct"]["status"] = "partial"
    fake.payments.append({"invoice_id": "inv-oct", "amount": Decimal("100.00")})
    partial = await _service(fake).reminder_notice(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
    )
    assert partial["invoice_id"] == "inv-oct"


async def test_reminder_rejects_paid_or_cancelled():
    """A paid or cancelled invoice is not reminded."""
    fake = FakeFeeBillingRepository([], [])
    _store_invoice(fake, _issued_invoice(status="paid"))
    service = _service(fake)
    with pytest.raises(ConflictException):
        await service.reminder_notice(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
        )
    fake.stored_invoices["inv-oct"]["status"] = "issued"
    fake.payments.append({"invoice_id": "inv-oct", "amount": Decimal("1770.00")})
    with pytest.raises(ConflictException):
        await service.reminder_notice(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
        )
    fake.payments.clear()
    fake.stored_invoices["inv-oct"]["status"] = "cancelled"
    with pytest.raises(ConflictException):
        await service.reminder_notice(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            invoice_id="inv-oct",
        )


async def test_new_invoice_records_an_issued_activity():
    """The daily run keeps an issued row with no staff actor."""
    head = _head(tax={"applicable": True, "rate_percent": Decimal("18.5")})
    fake = FakeFeeBillingRepository([head], [_unit()])
    await _service(fake).issue_due(run_date=RUN_DATE)

    activity = fake.activities[0]
    assert activity["event"] == "issued"
    assert activity["invoice_id"] == "inv-1"
    assert activity["actor_user_id"] is None
    assert activity["detail"]["invoice_number"] == "INV-A-101-20261001"
    assert activity["detail"]["total_amount"] == "593.00"


async def test_payment_and_cancel_record_the_staff_actor():
    """A receipt and a cancel name the staff user who did them."""
    fake = FakeFeeBillingRepository([], [])
    _store_invoice(fake, _issued_invoice())
    service = _service(fake)
    await service.record_payment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
        amount=Decimal("500.00"),
        paid_on=date(2026, 10, 6),
        mode="upi",
        reference="UTR123",
        actor_user_id="staff-1",
    )
    paid = fake.activities[0]
    assert paid["event"] == "payment_recorded"
    assert paid["actor_user_id"] == "staff-1"
    assert paid["detail"]["amount"] == "500.00"
    assert paid["detail"]["mode"] == "upi"
    assert paid["detail"]["reference"] == "UTR123"

    _store_invoice(fake, _issued_invoice(id="inv-late"))
    await service.cancel_invoice(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-late",
        actor_user_id="staff-1",
    )
    cancelled = fake.activities[1]
    assert cancelled["event"] == "cancelled"
    assert cancelled["actor_user_id"] == "staff-1"
    assert cancelled["detail"]["invoice_number"] == "INV-A-101-20261001"


async def test_invoice_detail_lists_activities_oldest_first():
    """The detail keeps the stored order: generated, then reminded."""
    fake = FakeFeeBillingRepository([], [])
    invoice = _issued_invoice()
    invoice["activities"] = [
        {
            "event": "issued",
            "actor_user_id": None,
            "detail": {"invoice_number": "INV-A-101-20261001"},
            "created_at": "2026-10-01T00:05:00+00:00",
        },
        {
            "event": "reminder_sent",
            "actor_user_id": "staff-1",
            "detail": {"source": "manual", "email_count": 2},
            "created_at": "2026-10-08T04:00:00+00:00",
        },
    ]
    _store_invoice(fake, invoice)
    detail = await _service(fake).invoice_detail(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id="unit-1",
        invoice_id="inv-oct",
        as_of=date(2026, 10, 5),
    )
    assert [row["event"] for row in detail["activities"]] == ["issued", "reminder_sent"]
    assert detail["activities"][1]["actor_user_id"] == "staff-1"
    assert detail["activities"][1]["detail"]["email_count"] == 2


async def test_manual_reminder_records_the_queued_emails():
    """The admin reminder stores who sent it and how many emails were queued."""
    fake = FakeFeeBillingRepository([], [])
    await _service(fake).record_reminder(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        invoice_id="inv-oct",
        actor_user_id="staff-1",
        email_count=2,
    )
    activity = fake.activities[0]
    assert activity["event"] == "reminder_sent"
    assert activity["actor_user_id"] == "staff-1"
    assert activity["detail"] == {"source": "manual", "email_count": 2}
