"""Lazy, idempotent work-order generation from maintenance contracts."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg
from dateutil.relativedelta import relativedelta

from apps.work_order_service.app.db.repositories.contracts_repository import (
    ContractsRepository,
)
from apps.work_order_service.app.db.repositories.work_orders_repository import (
    WorkOrderRepository,
)
from apps.work_order_service.app.utils.tokens import generate_vendor_token

DEFAULT_LEAD_DAYS = 5
RECURRING_LEAD_DAYS = 5
GENERATING_STATUS = "active"
TERMINAL_STATUSES = ("terminated", "expired")

DAY_STEPS = {"daily": 1, "weekly": 7, "fortnightly": 14}
MONTH_STEPS = {
    "monthly": 1,
    "quarterly": 3,
    "half_yearly": 6,
    "yearly": 12,
}
WEEKDAY_FREQUENCIES = {"weekly", "fortnightly"}
MONTH_FREQUENCIES = set(MONTH_STEPS)

_MAX_VISITS = 1000
_HORIZON_DAYS = 366 * 5

_WEEKDAY_NAMES = {
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
    "sun": 7,
    "sunday": 7,
}


def _today() -> date:
    """Today."""
    return datetime.now(timezone.utc).date()


def _now_iso() -> str:
    """Now iso."""
    return datetime.now(timezone.utc).isoformat()


def _parse_d(value: object | None) -> date | None:
    """Parse d."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_days(raw: object, frequency: str) -> list[int]:
    """Normalize days."""
    if not raw:
        return []
    out: list[int] = []
    for day_value in raw:
        try:
            out.append(int(day_value))
        except (TypeError, ValueError):
            name = _WEEKDAY_NAMES.get(str(day_value).strip().lower())
            if name:
                out.append(name)
    low, high = (1, 7) if frequency in WEEKDAY_FREQUENCIES else (1, 28)
    return sorted({min(max(value, low), high) for value in out if low <= value <= high})


def next_occurrence(after: date, frequency: str, days: list[int]) -> date:
    """Next occurrence."""
    days_n = _normalize_days(days, frequency)
    if frequency in WEEKDAY_FREQUENCIES:
        span = DAY_STEPS.get(frequency, 7)
        if not days_n:
            return after + timedelta(days=span)
        for offset in range(1, span + 1):
            candidate = after + timedelta(days=offset)
            if candidate.isoweekday() in days_n:
                return candidate
        return after + timedelta(days=span)
    months = MONTH_STEPS.get(frequency, 1)
    nxt = after + relativedelta(months=months)
    if not days_n:
        return nxt
    candidate = nxt.replace(day=min(days_n))
    if candidate <= after:
        candidate = (nxt + relativedelta(months=1)).replace(day=min(days_n))
    return candidate


def _step(day: date, frequency: str) -> date:
    """Step."""
    if frequency in DAY_STEPS:
        return day + timedelta(days=DAY_STEPS[frequency])
    months = MONTH_STEPS.get(frequency, 3)
    return day + relativedelta(months=months)


def _lead_days(contract: dict[str, Any]) -> int:
    """Lead days."""
    raw = contract.get("auto_generate_lead_days")
    if raw is None:
        return DEFAULT_LEAD_DAYS
    return max(0, int(raw))


def _iter_visit_dates(contract: dict[str, Any], today: date):
    """Iter visit dates."""
    start = _parse_d(contract.get("start_date"))
    if not start:
        return
    end = _parse_d(contract.get("end_date"))
    last = _parse_d(contract.get("last_serviced_date"))
    frequency = contract.get("visit_frequency") or "quarterly"

    if last:
        anchor = max(start, last)
        visit = _step(anchor, frequency)
    else:
        visit = start

    horizon = today + timedelta(days=_HORIZON_DAYS)
    for _ in range(_MAX_VISITS):
        if end is not None and visit > end:
            return
        if visit > horizon:
            return
        yield visit
        visit = _step(visit, frequency)


class SchedulerService:
    """Generate due work orders from contracts and recurring templates."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        """init  ."""
        self.conn = conn
        self.contracts = ContractsRepository(conn)
        self.work_orders = WorkOrderRepository(conn)

    def _build_work_order(self, contract: dict[str, Any], visit: date, lead: int) -> dict[str, Any]:
        """Build work order."""
        raw_token, token_hash = generate_vendor_token()
        freq = contract.get("visit_frequency") or "quarterly"
        title = f"{freq} visit — {contract['title']} ({visit.isoformat()})"
        return {
            "organization_id": contract["organization_id"],
            "project_id": contract["project_id"],
            "title": title[:256],
            "description": contract.get("scope_included"),
            "asset_ids": contract.get("asset_ids") or [],
            "contract_id": contract["id"],
            "company_id": contract.get("company_id"),
            "form_template_id": contract.get("form_template_id"),
            "state": "upcoming",
            "priority": "medium",
            "source": "contract",
            "scheduled_date": visit.isoformat(),
            "vendor_token_hash": token_hash,
            "timeline": [
                {
                    "id": str(uuid.uuid4()),
                    "at": _now_iso(),
                    "type": "created",
                    "by": "Scheduler",
                    "note": f"Auto-generated from contract (lead {lead}d); token={raw_token}",
                }
            ],
        }

    async def _generate_for_contract(
        self, contract: dict[str, Any], today: date
    ) -> tuple[int, int, date | None]:
        """Generate for contract."""
        lead = _lead_days(contract)
        existing = await self.work_orders.existing_contract_schedule_dates(contract["id"])
        created = 0
        skipped = 0
        next_future: date | None = None

        for visit in _iter_visit_dates(contract, today):
            if visit > today and next_future is None:
                next_future = visit
            if visit > today + timedelta(days=lead):
                break
            if visit.isoformat() in existing:
                skipped += 1
                continue
            await self.work_orders.create(self._build_work_order(contract, visit, lead))
            created += 1

        await self.contracts.update_next_visit_date(
            contract["id"],
            next_future.isoformat() if next_future else None,
        )
        return created, skipped, next_future

    async def _cancel_pending(self, contract: dict[str, Any], _note: str) -> int:
        """Cancel pending."""
        return await self.work_orders.cancel_contract_work_orders(contract["id"])

    async def recompute_contract(
        self,
        contract: dict[str, Any],
        *,
        terms_changed: bool = False,
        status_changed: bool = False,
        terminal_note: str = "Cancelled — contract terminated",
    ) -> dict[str, int]:
        """Recompute contract."""
        status = (contract.get("status") or "").lower()
        if contract.get("record_status") == "deleted" or status in TERMINAL_STATUSES:
            cancelled = await self._cancel_pending(contract, terminal_note)
            return {"cancelled": cancelled}

        if status != GENERATING_STATUS:
            return {"cancelled": 0}

        if terms_changed or status_changed:
            await self._cancel_pending(contract, "Cancelled — contract terms changed")
        created, skipped, _ = await self._generate_for_contract(contract, _today())
        return {"created": created, "cancelled": 0, "skipped": skipped}

    async def generate_recurring_work_orders(
        self, organization_id: str | None = None
    ) -> dict[str, int]:
        """Generate recurring work orders."""
        counts = {"created": 0, "skipped": 0}
        today = _today()
        templates = await self.work_orders.list_recurring_templates(organization_id)

        for template in templates:
            try:
                existing = await self.work_orders.recurring_child_dates(template["id"])
                last_child = max((_parse_d(d) for d in existing), default=None)
                anchor = (
                    _parse_d(template.get("scheduled_date"))
                    or _parse_d(template.get("started_at"))
                    or today
                )
                after = max(anchor, last_child or today)
                next_date = next_occurrence(
                    after,
                    template.get("recurring_frequency") or "weekly",
                    template.get("recurring_days") or [],
                )
                end = _parse_d(template.get("recurring_end_date"))
                if end and next_date > end:
                    counts["skipped"] += 1
                elif next_date.isoformat() in existing:
                    counts["skipped"] += 1
                elif next_date - timedelta(days=RECURRING_LEAD_DAYS) <= today:
                    raw_token, token_hash = generate_vendor_token()
                    await self.work_orders.create(
                        {
                            "organization_id": template["organization_id"],
                            "project_id": template["project_id"],
                            "title": f"{template['title']} — {next_date.isoformat()}"[:256],
                            "description": template.get("description"),
                            "asset_ids": template.get("asset_ids") or [],
                            "contract_id": template.get("contract_id"),
                            "company_id": template.get("company_id"),
                            "form_template_id": template.get("form_template_id"),
                            "state": "upcoming",
                            "priority": template.get("priority") or "medium",
                            "source": template.get("source") or "ad_hoc",
                            "scheduled_date": next_date.isoformat(),
                            "assignee_name": template.get("assignee_name"),
                            "access_notes": template.get("access_notes"),
                            "is_recurring": False,
                            "recurring_parent_id": template["id"],
                            "vendor_token_hash": token_hash,
                            "timeline": [
                                {
                                    "id": str(uuid.uuid4()),
                                    "at": _now_iso(),
                                    "type": "created",
                                    "by": "Scheduler",
                                    "note": f"Recurring instance; token={raw_token}",
                                }
                            ],
                        }
                    )
                    counts["created"] += 1
                await self.work_orders.update_recurring_next_date(
                    template["id"],
                    next_date.isoformat() if not (end and next_date > end) else None,
                )
            except Exception:
                continue

        return counts

    async def generate_due_work_orders(self, organization_id: str | None = None) -> dict[str, int]:
        """Sweep all contracts and recurring templates."""
        counts = {"created": 0, "cancelled": 0, "skipped": 0}
        today = _today()
        contracts = await self.contracts.list_active_for_scheduler(organization_id)

        for contract in contracts:
            status = (contract.get("status") or "").lower()
            if status in TERMINAL_STATUSES:
                counts["cancelled"] += await self._cancel_pending(
                    contract, "Cancelled — contract terminated"
                )
                continue
            if status != GENERATING_STATUS:
                continue
            created, skipped, _ = await self._generate_for_contract(contract, today)
            counts["created"] += created
            counts["skipped"] += skipped

        recurring = await self.generate_recurring_work_orders(organization_id)
        counts["created"] += recurring["created"]
        counts["skipped"] += recurring["skipped"]
        return counts
