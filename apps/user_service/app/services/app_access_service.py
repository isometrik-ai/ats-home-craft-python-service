"""App access control: rule, evaluation, overrides, resident hold payload."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from apps.user_service.app.db.repositories.app_access_repository import (
    AppAccessRepository,
    as_decimal,
)
from apps.user_service.app.schemas.app_access import (
    AppAccessOverrideRequest,
    UpsertAppAccessRuleRequest,
)
from apps.user_service.app.schemas.enums.app_access import (
    AppAccessMatchMode,
    AppAccessOverrideKind,
    AppAccessStatus,
)
from apps.user_service.app.services.app_access_evaluate import (
    empty_rule,
    evaluate_unit_status,
    reason_key_for,
)
from apps.user_service.app.services.fee_configuration_labels import money, money_str
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_KOLKATA = ZoneInfo("Asia/Kolkata")
_ZERO = Decimal("0.00")


def app_access_today() -> date:
    """Asia/Kolkata calendar day used for days past due."""
    return datetime.now(_KOLKATA).date()


class AppAccessService:
    """Staff rule/overrides and unit evaluation from fee invoices."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = AppAccessRepository(db_connection)

    @property
    def _org_id(self) -> str:
        """Session organization."""
        assert self.user_context is not None
        assert self.user_context.organization_id
        return self.user_context.organization_id

    async def get_rule(self, *, project_id: str, refresh: bool = False) -> dict[str, Any]:
        """Return the saved rule and impact counts."""
        if refresh:
            await self.evaluate_project(organization_id=self._org_id, project_id=project_id)
        row = await self.repo.get_rule(organization_id=self._org_id, project_id=project_id)
        document = self._rule_document(row)
        document["impact"] = await self._impact(project_id=project_id)
        return document

    async def upsert_rule(
        self, *, project_id: str, body: UpsertAppAccessRuleRequest
    ) -> dict[str, Any]:
        """Create or replace the rule, then re-evaluate the project."""
        if body.enabled and not body.by_amount and not body.by_days:
            raise ValidationException(
                message_key="app_access.errors.condition_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        payload = {
            "enabled": body.enabled,
            "by_amount": body.by_amount,
            "amount_threshold": money(body.amount_threshold),
            "by_days": body.by_days,
            "days_threshold": body.days_threshold,
            "match_mode": body.match_mode.value,
            "updated_by": self.user_context.user_id if self.user_context else None,
        }
        existing = await self.repo.get_rule(organization_id=self._org_id, project_id=project_id)
        if existing is None:
            if body.version != 0:
                raise ConflictException(
                    message_key="app_access.errors.version_conflict",
                    custom_code=CustomStatusCode.CONFLICT,
                )
            await self.repo.insert_rule(
                organization_id=self._org_id,
                project_id=project_id,
                rule=payload,
            )
        else:
            updated = await self.repo.update_rule(
                organization_id=self._org_id,
                project_id=project_id,
                expected_version=body.version,
                rule=payload,
            )
            if updated is None:
                raise ConflictException(
                    message_key="app_access.errors.version_conflict",
                    custom_code=CustomStatusCode.CONFLICT,
                )
        await self.evaluate_project(organization_id=self._org_id, project_id=project_id)
        return await self.get_rule(project_id=project_id, refresh=False)

    async def list_units(
        self,
        *,
        project_id: str,
        q: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        """Customers tab from last evaluation."""
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        if status and status not in {item.value for item in AppAccessStatus}:
            raise ValidationException(
                message_key="app_access.errors.invalid_status",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        rows, total = await self.repo.list_units_page(
            organization_id=self._org_id,
            project_id=project_id,
            q=q,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return [self._unit_item(row) for row in rows], total

    async def set_override(
        self, *, project_id: str, unit_id: str, body: AppAccessOverrideRequest
    ) -> dict[str, Any]:
        """Block now or release one unit."""
        unit = await self.repo.unit_project(organization_id=self._org_id, unit_id=unit_id)
        if unit is None or unit["project_id"] != project_id:
            raise NotFoundException(
                message_key="app_access.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        dues = await self._dues_for_unit(
            organization_id=self._org_id, project_id=project_id, unit_id=unit_id
        )
        if dues["outstanding"] <= _ZERO:
            raise ValidationException(
                message_key="app_access.errors.nothing_to_hold",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.upsert_override(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
            kind=body.kind.value,
            set_by=self.user_context.user_id if self.user_context else None,
        )
        await self.evaluate_unit(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return await self._unit_item_for(project_id=project_id, unit_id=unit_id)

    async def clear_override(self, *, project_id: str, unit_id: str) -> dict[str, Any]:
        """Back to rule."""
        unit = await self.repo.unit_project(organization_id=self._org_id, unit_id=unit_id)
        if unit is None or unit["project_id"] != project_id:
            raise NotFoundException(
                message_key="app_access.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.repo.delete_override(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        await self.evaluate_unit(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return await self._unit_item_for(project_id=project_id, unit_id=unit_id)

    async def evaluate_unit(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Persist evaluation for one unit. Clears a hand hold at ₹0."""
        as_of = as_of or app_access_today()
        dues = await self._dues_for_unit(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            as_of=as_of,
        )
        override = await self.repo.get_override(
            organization_id=organization_id, project_id=project_id, unit_id=unit_id
        )
        if override and dues["outstanding"] <= _ZERO:
            await self.repo.delete_override(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
            )
            override = None
        rule = await self.repo.get_rule(organization_id=organization_id, project_id=project_id)
        outcome = evaluate_unit_status(
            outstanding=dues["outstanding"],
            days_past_due=dues["days_past_due"],
            has_open_invoice=dues["has_open_invoice"],
            override_kind=override["kind"] if override else None,
            enabled=bool(rule["enabled"]) if rule else False,
            by_amount=bool(rule["by_amount"]) if rule else False,
            amount_threshold=as_decimal(rule["amount_threshold"]) if rule else _ZERO,
            by_days=bool(rule["by_days"]) if rule else False,
            days_threshold=int(rule["days_threshold"]) if rule else 15,
            match_mode=(rule["match_mode"] if rule else AppAccessMatchMode.ALL.value),
        )
        previous = await self.repo.get_state(
            organization_id=organization_id, project_id=project_id, unit_id=unit_id
        )
        now = datetime.now(timezone.utc)
        blocked_on, blocked_by = self._stamp(
            previous=previous, outcome=outcome, now=now, override=override
        )
        days_value: int | None
        if dues["outstanding"] <= _ZERO:
            days_value = None
        else:
            days_value = dues["days_past_due"]
        state = {
            "organization_id": organization_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "status": outcome["status"],
            "outstanding_amount": money(dues["outstanding"]),
            "days_past_due": days_value,
            "oldest_invoice_date": dues["oldest_invoice_date"],
            "blocked_on": blocked_on,
            "blocked_by": blocked_by,
            "reason": outcome["reason"],
            "evaluated_at": now,
        }
        await self.repo.upsert_state(state)
        return state

    async def evaluate_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        as_of: date | None = None,
    ) -> None:
        """Re-evaluate every candidate unit in the project."""
        unit_ids = await self.repo.list_eval_unit_ids(
            organization_id=organization_id, project_id=project_id
        )
        for unit_id in unit_ids:
            await self.evaluate_unit(
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
                as_of=as_of,
            )

    async def resident_unit_access(self, *, organization_id: str, unit_id: str) -> dict[str, Any]:
        """Hold payload for one unit. Missing state is not held."""
        unit = await self.repo.unit_project(organization_id=organization_id, unit_id=unit_id)
        if unit is None:
            raise NotFoundException(
                message_key="app_access.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        state = await self.repo.get_state(
            organization_id=organization_id,
            project_id=unit["project_id"],
            unit_id=unit_id,
        )
        return self._resident_payload(unit_id=unit_id, unit_code=unit["unit_code"], state=state)

    async def onboarding_app_access(
        self, *, organization_id: str, contact_id: str
    ) -> dict[str, Any]:
        """Hold summary for every active unit of the contact."""
        units = await self.repo.list_contact_active_units(
            organization_id=organization_id, contact_id=contact_id
        )
        items: list[dict[str, Any]] = []
        held_ids: list[str] = []
        for unit in units:
            state = await self.repo.get_state(
                organization_id=organization_id,
                project_id=unit["project_id"],
                unit_id=unit["unit_id"],
            )
            payload = self._resident_payload(
                unit_id=unit["unit_id"],
                unit_code=unit["unit_code"],
                state=state,
            )
            items.append(
                {
                    "unit_id": payload["unit_id"],
                    "unit_code": payload["unit_code"] or "",
                    "status": payload["status"],
                    "held": payload["held"],
                    "outstanding_amount": payload["outstanding_amount"],
                    "days_past_due": payload["days_past_due"],
                    "reason_key": payload["reason_key"],
                }
            )
            if payload["held"]:
                held_ids.append(unit["unit_id"])
        return {
            "any_held": bool(held_ids),
            "held_unit_ids": held_ids,
            "units": items,
        }

    async def is_unit_held(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any] | None:
        """State row when the unit is blocked; otherwise None."""
        state = await self.repo.get_state(
            organization_id=organization_id, project_id=project_id, unit_id=unit_id
        )
        if state and state["status"] == AppAccessStatus.BLOCKED.value:
            return state
        return None

    async def _dues_for_unit(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Outstanding and days past due for one unit."""
        rows = await self.repo.list_unit_dues(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            as_of=as_of or app_access_today(),
        )
        if not rows:
            return {
                "outstanding": _ZERO,
                "days_past_due": 0,
                "oldest_invoice_date": None,
                "has_open_invoice": False,
            }
        row = rows[0]
        outstanding = money(as_decimal(row["outstanding_amount"]))
        days = int(row["days_past_due"] or 0)
        return {
            "outstanding": outstanding,
            "days_past_due": days,
            "oldest_invoice_date": row["oldest_invoice_date"],
            "has_open_invoice": outstanding > _ZERO,
        }

    async def _impact(self, *, project_id: str) -> dict[str, Any]:
        """Aggregate counts from persisted states."""
        rows = await self.repo.list_states(organization_id=self._org_id, project_id=project_id)
        counts = {
            "blocked": 0,
            "watch": 0,
            "notdue": 0,
            "released": 0,
            "clear": 0,
        }
        blocked_amount = _ZERO
        overdue_amount = _ZERO
        for row in rows:
            status = row["status"]
            if status in counts:
                counts[status] += 1
            amount = as_decimal(row["outstanding_amount"])
            if status == AppAccessStatus.BLOCKED.value:
                blocked_amount = money(blocked_amount + amount)
            days = row.get("days_past_due") or 0
            if amount > _ZERO and days > 0:
                overdue_amount = money(overdue_amount + amount)
        return {
            **counts,
            "blocked_amount": money_str(blocked_amount),
            "overdue_amount": money_str(overdue_amount),
        }

    async def _unit_item_for(self, *, project_id: str, unit_id: str) -> dict[str, Any]:
        """One customers-tab row after override."""
        rows, _ = await self.repo.list_units_page(
            organization_id=self._org_id,
            project_id=project_id,
            q=None,
            status=None,
            limit=1,
            offset=0,
            unit_id=unit_id,
        )
        if rows:
            return self._unit_item(rows[0])
        state = await self.repo.get_state(
            organization_id=self._org_id, project_id=project_id, unit_id=unit_id
        )
        unit = await self.repo.unit_project(organization_id=self._org_id, unit_id=unit_id)
        override = await self.repo.get_override(
            organization_id=self._org_id, project_id=project_id, unit_id=unit_id
        )
        merged = {
            "unit_id": unit_id,
            "unit_code": (unit or {}).get("unit_code") or "",
            "display_name": "—",
            "outstanding_amount": (state or {}).get("outstanding_amount") or _ZERO,
            "oldest_invoice_date": (state or {}).get("oldest_invoice_date"),
            "days_past_due": (state or {}).get("days_past_due"),
            "status": (state or {}).get("status") or AppAccessStatus.CLEAR.value,
            "blocked_on": (state or {}).get("blocked_on"),
            "blocked_by": (state or {}).get("blocked_by"),
            "reason": (state or {}).get("reason") or "",
            "override_kind": override["kind"] if override else None,
            "evaluated_at": (state or {}).get("evaluated_at"),
        }
        return self._unit_item(merged)

    @staticmethod
    def _rule_document(row: dict[str, Any] | None) -> dict[str, Any]:
        """API rule document."""
        if row is None:
            return empty_rule()
        return {
            "exists": True,
            "enabled": bool(row["enabled"]),
            "by_amount": bool(row["by_amount"]),
            "amount_threshold": money_str(as_decimal(row["amount_threshold"])),
            "by_days": bool(row["by_days"]),
            "days_threshold": int(row["days_threshold"]),
            "match_mode": row["match_mode"],
            "version": int(row["version"]),
        }

    @staticmethod
    def _unit_item(row: dict[str, Any]) -> dict[str, Any]:
        """Serialize a customers-tab row."""
        outstanding = as_decimal(row.get("outstanding_amount"))
        days = row.get("days_past_due")
        return {
            "unit_id": str(row["unit_id"]),
            "unit_code": row.get("unit_code") or "",
            "display_name": row.get("display_name") or "—",
            "outstanding_amount": money_str(outstanding),
            "oldest_invoice_date": row.get("oldest_invoice_date"),
            "days_past_due": None if outstanding <= _ZERO else int(days or 0),
            "status": row.get("status") or AppAccessStatus.CLEAR.value,
            "blocked_on": row.get("blocked_on"),
            "blocked_by": row.get("blocked_by"),
            "reason": row.get("reason") or "",
            "override_kind": row.get("override_kind"),
            "evaluated_at": row.get("evaluated_at"),
        }

    @staticmethod
    def _resident_payload(
        *, unit_id: str, unit_code: str | None, state: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Resident hold JSON."""
        status = (state or {}).get("status") or AppAccessStatus.CLEAR.value
        blocked_by = (state or {}).get("blocked_by")
        outstanding = as_decimal((state or {}).get("outstanding_amount"))
        held = status == AppAccessStatus.BLOCKED.value
        return {
            "unit_id": unit_id,
            "unit_code": unit_code,
            "status": status,
            "held": held,
            "outstanding_amount": money_str(outstanding) if state else None,
            "days_past_due": (state or {}).get("days_past_due"),
            "blocked_on": (state or {}).get("blocked_on"),
            "reason_key": reason_key_for(status=status, blocked_by=blocked_by),
        }

    @staticmethod
    def _stamp(
        *,
        previous: dict[str, Any] | None,
        outcome: dict[str, Any],
        now: datetime,
        override: dict[str, Any] | None,
    ) -> tuple[datetime | None, str | None]:
        """Keep blocked_on until the unit leaves blocked."""
        if outcome["status"] != AppAccessStatus.BLOCKED.value:
            return None, None
        if (
            previous
            and previous.get("status") == AppAccessStatus.BLOCKED.value
            and previous.get("blocked_on") is not None
        ):
            return previous["blocked_on"], previous.get("blocked_by") or outcome.get("blocked_by")
        blocked_by = outcome.get("blocked_by")
        if override and override.get("kind") == AppAccessOverrideKind.HOLD.value:
            blocked_by = "hand"
        return now, blocked_by
