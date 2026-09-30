"""Fee configuration rules: validation, labels, and optimistic saves."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.fee_configuration_repository import (
    FeeConfigurationRepository,
)
from apps.user_service.app.schemas.enums.fee_configuration import (
    FeeBillingCycle,
    FeeFrequency,
    FeeHeadKind,
    FeeHeadStatus,
    FeeStartRule,
)
from apps.user_service.app.schemas.fee_configuration import (
    CreateFeeHeadRequest,
    CreateFinanceSettingsRequest,
    FeeHeadWriteRequest,
    FinanceSettingsWriteRequest,
    UpdateFeeHeadRequest,
    UpdateFinanceSettingsRequest,
)
from apps.user_service.app.services.fee_configuration_labels import (
    CATEGORY_BY_KIND,
    CATEGORY_LABELS,
    PROPERTY_TYPE_LABELS,
    PROPERTY_TYPE_ORDER,
    anchor_month_for_cycle,
    billing_months_sentence,
    charge_summary,
    frequency_label,
    late_fee_example,
    money_str,
    settings_summary,
    tax_label,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_LATE_FEE_MODES = frozenset({"none", "flat", "interest"})
_ELECTRICITY_CHARGE_KEYS = (
    "grid_fixed_amount",
    "grid_unit_rate",
    "dg_fixed_amount",
    "dg_unit_rate",
)


def _validation(message_key: str) -> ValidationException:
    """Build a validation error for a fee-configuration message key."""
    return ValidationException(
        message_key=message_key,
        custom_code=CustomStatusCode.VALIDATION_ERROR,
    )


def _at_most_two_places(value: Decimal) -> bool:
    """Return whether the amount has at most two decimal places."""
    return value == value.quantize(Decimal("0.01"))


class FeeConfigurationService:
    """Staff fee-head and dunning configuration for one project."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = FeeConfigurationRepository(db_connection)

    @property
    def _org_id(self) -> str:
        """Return the caller's organization id."""
        org_id = self.user_context.organization_id
        if not org_id:
            raise _validation("fee_configuration.errors.not_found")
        return org_id

    async def list_fee_heads(self, *, project_id: str) -> tuple[list[dict[str, Any]], int]:
        """Return the three fee heads with server-built list labels."""
        heads = await self.repo.list_heads(organization_id=self._org_id, project_id=project_id)
        scopes_by_head = await self._scopes_by_head([str(row["id"]) for row in heads])
        items = [self._list_item(row, scopes_by_head.get(str(row["id"]), [])) for row in heads]
        return items, len(items)

    async def get_fee_head(self, *, project_id: str, fee_head_id: str) -> dict[str, Any]:
        """Return the editor document for one fee head."""
        head = await self._require_head(project_id=project_id, fee_head_id=fee_head_id)
        scopes = await self.repo.list_scopes(fee_head_ids=[str(head["id"])])
        return self._detail(head, scopes)

    async def update_fee_head(
        self,
        *,
        project_id: str,
        fee_head_id: str,
        body: UpdateFeeHeadRequest,
    ) -> dict[str, Any]:
        """Replace the editable document when version still matches."""
        current = await self._require_head(project_id=project_id, fee_head_id=fee_head_id)
        prepared = self._prepare_update(kind=str(current["kind"]), body=body, current=current)
        updated = await self.repo.update_head(
            organization_id=self._org_id,
            project_id=project_id,
            fee_head_id=fee_head_id,
            expected_version=body.version,
            update_data={**prepared["head"], "updated_by": self.user_context.user_id},
        )
        if updated is None:
            raise self._version_conflict()
        await self.repo.replace_scopes(fee_head_id=fee_head_id, scopes=prepared["scopes"])
        return await self.get_fee_head(project_id=project_id, fee_head_id=fee_head_id)

    async def create_fee_head(
        self, *, project_id: str, body: CreateFeeHeadRequest
    ) -> dict[str, Any]:
        """Insert one fee head and its three property scopes."""
        kind = body.kind.value
        existing = await self.repo.get_head_by_kind(
            organization_id=self._org_id,
            project_id=project_id,
            kind=kind,
        )
        if existing is not None:
            raise ConflictException(
                message_key="fee_configuration.errors.already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )
        prepared = self._prepare_update(kind=kind, body=body, current={"tax": {}})
        fee_head_id = await self.repo.insert_head(
            organization_id=self._org_id,
            project_id=project_id,
            kind=kind,
            category=CATEGORY_BY_KIND[kind],
            head={**prepared["head"], "updated_by": self.user_context.user_id},
        )
        await self.repo.insert_scopes(
            fee_head_id=fee_head_id,
            organization_id=self._org_id,
            scopes=prepared["scopes"],
        )
        return await self.get_fee_head(project_id=project_id, fee_head_id=fee_head_id)

    async def update_status(
        self,
        *,
        project_id: str,
        fee_head_id: str,
        version: int,
        status: FeeHeadStatus,
    ) -> dict[str, Any]:
        """Switch Active / Inactive without replacing rates."""
        await self._require_head(project_id=project_id, fee_head_id=fee_head_id)
        updated = await self.repo.update_head(
            organization_id=self._org_id,
            project_id=project_id,
            fee_head_id=fee_head_id,
            expected_version=version,
            update_data={"status": status.value, "updated_by": self.user_context.user_id},
        )
        if updated is None:
            raise self._version_conflict()
        head = await self._require_head(project_id=project_id, fee_head_id=fee_head_id)
        scopes = await self.repo.list_scopes(fee_head_ids=[fee_head_id])
        return self._list_item(head, scopes)

    async def get_settings(self, *, project_id: str) -> dict[str, Any]:
        """Return the four dunning numbers and the computed sentence."""
        row = await self._require_settings(project_id=project_id)
        return self._settings_payload(row)

    async def update_settings(
        self, *, project_id: str, body: UpdateFinanceSettingsRequest
    ) -> dict[str, Any]:
        """Replace the four dunning numbers when version still matches."""
        self._validate_settings(body)
        await self._require_settings(project_id=project_id)
        updated = await self.repo.update_settings(
            organization_id=self._org_id,
            project_id=project_id,
            expected_version=body.version,
            update_data={
                "payment_retry_count": body.payment_retry_count,
                "payment_retry_interval_days": body.payment_retry_interval_days,
                "pre_due_reminder_count": body.pre_due_reminder_count,
                "pre_due_reminder_interval_days": body.pre_due_reminder_interval_days,
                "updated_by": self.user_context.user_id,
            },
        )
        if updated is None:
            raise ConflictException(
                message_key="fee_configuration.errors.settings_version_conflict",
                custom_code=CustomStatusCode.CONFLICT,
            )
        return await self.get_settings(project_id=project_id)

    async def create_settings(
        self, *, project_id: str, body: CreateFinanceSettingsRequest
    ) -> dict[str, Any]:
        """Insert the project's finance settings row."""
        self._validate_settings(body)
        existing = await self.repo.get_settings(
            organization_id=self._org_id,
            project_id=project_id,
        )
        if existing is not None:
            raise ConflictException(
                message_key="fee_configuration.errors.settings_already_exist",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.insert_settings(
            organization_id=self._org_id,
            project_id=project_id,
            settings={
                "payment_retry_count": body.payment_retry_count,
                "payment_retry_interval_days": body.payment_retry_interval_days,
                "pre_due_reminder_count": body.pre_due_reminder_count,
                "pre_due_reminder_interval_days": body.pre_due_reminder_interval_days,
                "updated_by": self.user_context.user_id,
            },
        )
        return await self.get_settings(project_id=project_id)

    async def _require_head(self, *, project_id: str, fee_head_id: str) -> dict[str, Any]:
        """Load one fee head or raise not-found."""
        head = await self.repo.get_head(
            organization_id=self._org_id,
            project_id=project_id,
            fee_head_id=fee_head_id,
        )
        if head is None:
            raise NotFoundException(
                message_key="fee_configuration.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return head

    async def _require_settings(self, *, project_id: str) -> dict[str, Any]:
        """Load finance settings or raise not-found."""
        row = await self.repo.get_settings(organization_id=self._org_id, project_id=project_id)
        if row is None:
            raise NotFoundException(
                message_key="fee_configuration.errors.settings_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def _scopes_by_head(self, fee_head_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Group property scopes by fee head id."""
        grouped: dict[str, list[dict[str, Any]]] = {head_id: [] for head_id in fee_head_ids}
        for scope in await self.repo.list_scopes(fee_head_ids=fee_head_ids):
            grouped.setdefault(str(scope["fee_head_id"]), []).append(scope)
        return grouped

    def _prepare_update(
        self,
        *,
        kind: str,
        body: FeeHeadWriteRequest,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a write and return the head columns plus scope rows."""
        name = body.name.strip()
        if not name or len(name) > 80:
            raise _validation("fee_configuration.errors.name_required")
        line_description = (body.line_description or "").strip()
        if len(line_description) > 240:
            raise _validation("fee_configuration.errors.invalid_line_description")
        if body.due_within_days < 0 or body.due_within_days > 365:
            raise _validation("fee_configuration.errors.invalid_due_days")
        if body.invoice_day < 1 or body.invoice_day > 28:
            raise _validation("fee_configuration.errors.invalid_day")
        fee_start_date = self._fee_start_date(body)

        frequency = body.frequency.value
        if kind == FeeHeadKind.ELECTRICITY.value and frequency != FeeFrequency.MONTHLY.value:
            raise _validation("fee_configuration.errors.frequency_locked")

        billing_cycle, anchor = self._schedule(frequency=frequency, body=body)
        meter_read_day = self._meter_read_day(kind=kind, body=body)
        charge = self._charge(kind=kind, body=body)
        scopes = self._scopes(kind=kind, body=body)
        tax = self._tax(body=body, current=current)
        late_fee = self._late_fee(body)

        return {
            "head": {
                "name": name,
                "line_description": line_description or None,
                "status": body.status.value,
                "frequency": frequency,
                "billing_cycle": billing_cycle,
                "cycle_anchor_month": anchor,
                "fee_start_rule": body.fee_start_rule.value,
                "fee_start_date": fee_start_date,
                "due_within_days": body.due_within_days,
                "invoice_day": body.invoice_day,
                "meter_read_day": meter_read_day,
                "charge": charge,
                "tax": tax,
                "late_fee": late_fee,
            },
            "scopes": scopes,
        }

    def _fee_start_date(self, body: FeeHeadWriteRequest) -> date | None:
        """Return the specific start date, and only when that rule is selected."""
        if body.fee_start_rule == FeeStartRule.SPECIFIC_DATE:
            if body.fee_start_date is None:
                raise _validation("fee_configuration.errors.fee_start_date_required")
            return body.fee_start_date
        if body.fee_start_date is not None:
            raise _validation("fee_configuration.errors.fee_start_date_not_allowed")
        return None

    def _schedule(
        self, *, frequency: str, body: FeeHeadWriteRequest
    ) -> tuple[str | None, int | None]:
        """Return the billing cycle and anchor month for a non-monthly fee."""
        if frequency == FeeFrequency.MONTHLY.value:
            return None, None
        if body.billing_cycle is None:
            raise _validation("fee_configuration.errors.billing_cycle_required")
        cycle = body.billing_cycle.value
        if cycle == FeeBillingCycle.CUSTOM.value and body.cycle_anchor_month is None:
            raise _validation("fee_configuration.errors.anchor_month_required")
        if body.cycle_anchor_month is not None and not 1 <= body.cycle_anchor_month <= 12:
            raise _validation("fee_configuration.errors.anchor_month_required")
        return cycle, anchor_month_for_cycle(cycle, body.cycle_anchor_month)

    def _meter_read_day(self, *, kind: str, body: FeeHeadWriteRequest) -> int | None:
        """Return the meter-read day for electricity, otherwise None."""
        if kind != FeeHeadKind.ELECTRICITY.value:
            return None
        day = body.meter_read_day
        if day is None or day < 1 or day > 28:
            raise _validation("fee_configuration.errors.invalid_day")
        return day

    def _charge(self, *, kind: str, body: FeeHeadWriteRequest) -> dict[str, Any]:
        """Return the kind-specific charge object stored on the fee head."""
        if kind == FeeHeadKind.MAINTENANCE.value:
            return {}
        raw = body.charge.model_dump(exclude_none=True) if body.charge is not None else {}
        if kind == FeeHeadKind.CLUB.value:
            if "amount" not in raw:
                raise _validation("fee_configuration.errors.invalid_rate")
            return {"amount": self._non_negative_money(raw["amount"])}
        missing = [key for key in _ELECTRICITY_CHARGE_KEYS if key not in raw]
        if missing:
            raise _validation("fee_configuration.errors.invalid_rate")
        return {key: self._non_negative_money(raw[key]) for key in _ELECTRICITY_CHARGE_KEYS}

    def _scopes(self, *, kind: str, body: FeeHeadWriteRequest) -> list[dict[str, Any]]:
        """Return the three property-scope rows, keeping maintenance rates when disabled."""
        seen = [scope.property_type.value for scope in body.scopes]
        if sorted(seen) != sorted(PROPERTY_TYPE_ORDER) or len(seen) != len(PROPERTY_TYPE_ORDER):
            raise _validation("fee_configuration.errors.no_property_type")
        if not any(scope.enabled for scope in body.scopes):
            raise _validation("fee_configuration.errors.no_property_type")
        prepared: list[dict[str, Any]] = []
        for scope in body.scopes:
            row: dict[str, Any] = {
                "property_type": scope.property_type.value,
                "enabled": scope.enabled,
            }
            if kind == FeeHeadKind.MAINTENANCE.value:
                if scope.rate_per_sqft is None or scope.minimum_amount is None:
                    raise _validation("fee_configuration.errors.invalid_rate")
                row["rate_per_sqft"] = Decimal(self._non_negative_money(scope.rate_per_sqft))
                row["minimum_amount"] = Decimal(self._non_negative_money(scope.minimum_amount))
            else:
                row["rate_per_sqft"] = None
                row["minimum_amount"] = None
            prepared.append(row)
        return prepared

    def _tax(self, *, body: FeeHeadWriteRequest, current: dict[str, Any]) -> dict[str, Any]:
        """Return the tax object, keeping the last rate when tax is turned off."""
        current_tax = current.get("tax") or {}
        if not body.tax.applicable:
            kept = body.tax.rate_percent
            if kept is None:
                kept = current_tax.get("rate_percent")
            payload: dict[str, Any] = {"applicable": False}
            if kept is not None:
                payload["rate_percent"] = self._percent(kept, allow_zero=True)
            return payload
        if body.tax.rate_percent is None:
            raise _validation("fee_configuration.errors.invalid_tax_rate")
        return {
            "applicable": True,
            "rate_percent": self._percent(body.tax.rate_percent, allow_zero=True),
        }

    def _late_fee(self, body: FeeHeadWriteRequest) -> dict[str, Any]:
        """Return the late-fee object for none, flat steps, or interest."""
        mode = body.late_fee.mode
        if mode not in _LATE_FEE_MODES:
            raise _validation("fee_configuration.errors.late_fee_steps")
        if mode == "none":
            return {"mode": "none"}
        if mode == "interest":
            if body.late_fee.annual_percent is None:
                raise _validation("fee_configuration.errors.invalid_interest_rate")
            percent = Decimal(str(body.late_fee.annual_percent))
            if percent <= 0 or percent > 100 or not _at_most_two_places(percent):
                raise _validation("fee_configuration.errors.invalid_interest_rate")
            return {"mode": "interest", "annual_percent": money_str(percent)}
        steps = body.late_fee.steps or []
        if not 1 <= len(steps) <= 4:
            raise _validation("fee_configuration.errors.late_fee_steps")
        days = [step.days_overdue for step in steps]
        if len(days) != len(set(days)) or any(day < 1 for day in days):
            raise _validation("fee_configuration.errors.late_fee_steps")
        ordered = sorted(steps, key=lambda step: step.days_overdue)
        return {
            "mode": "flat",
            "steps": [
                {
                    "days_overdue": step.days_overdue,
                    "amount": self._non_negative_money(step.amount),
                }
                for step in ordered
            ],
        }

    def _validate_settings(self, body: FinanceSettingsWriteRequest) -> None:
        """Reject retry and reminder counts or intervals outside the allowed range."""
        counts = (body.payment_retry_count, body.pre_due_reminder_count)
        if any(count < 0 or count > 12 for count in counts):
            raise _validation("fee_configuration.errors.invalid_dunning")
        intervals = (
            (body.payment_retry_count, body.payment_retry_interval_days),
            (body.pre_due_reminder_count, body.pre_due_reminder_interval_days),
        )
        for count, interval in intervals:
            if count > 0 and not 1 <= interval <= 30:
                raise _validation("fee_configuration.errors.invalid_dunning")
            if count == 0 and not 1 <= interval <= 30:
                raise _validation("fee_configuration.errors.invalid_dunning")

    def _non_negative_money(self, value: Any) -> str:
        """Return a non-negative amount as a two-place decimal string."""
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise _validation("fee_configuration.errors.invalid_rate") from exc
        if amount < 0 or not _at_most_two_places(amount):
            raise _validation("fee_configuration.errors.invalid_rate")
        return money_str(amount)

    def _percent(self, value: Any, *, allow_zero: bool) -> str:
        """Return a percent from 0 or 0.01 through 100 as a decimal string."""
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise _validation("fee_configuration.errors.invalid_tax_rate") from exc
        lower = 0 if allow_zero else Decimal("0.01")
        if amount < lower or amount > 100 or not _at_most_two_places(amount):
            raise _validation("fee_configuration.errors.invalid_tax_rate")
        return money_str(amount)

    def _version_conflict(self) -> ConflictException:
        """Build the stale-version conflict for a fee head save."""
        return ConflictException(
            message_key="fee_configuration.errors.version_conflict",
            custom_code=CustomStatusCode.CONFLICT,
        )

    def _list_item(self, head: dict[str, Any], scopes: list[dict[str, Any]]) -> dict[str, Any]:
        """Build one fee-head list row, including the server-built labels."""
        tax = head.get("tax") or {}
        return {
            "id": str(head["id"]),
            "kind": head["kind"],
            "name": head["name"],
            "category": head["category"],
            "category_label": CATEGORY_LABELS.get(str(head["category"]), str(head["category"])),
            "charge_summary": charge_summary(
                kind=str(head["kind"]),
                scopes=scopes,
                charge=head.get("charge"),
            ),
            "frequency": head["frequency"],
            "frequency_label": frequency_label(
                kind=str(head["kind"]),
                frequency=str(head["frequency"]),
                billing_cycle=head.get("billing_cycle"),
                anchor_month=head.get("cycle_anchor_month"),
            ),
            "tax_label": tax_label(
                applicable=bool(tax.get("applicable")),
                rate_percent=tax.get("rate_percent"),
            ),
            "status": head["status"],
            "version": int(head["version"]),
        }

    def _detail(self, head: dict[str, Any], scopes: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the editor document for one fee head."""
        months, sentence = billing_months_sentence(
            frequency=str(head["frequency"]),
            billing_cycle=head.get("billing_cycle"),
            anchor_month=head.get("cycle_anchor_month"),
        )
        tax = head.get("tax") or {"applicable": False}
        late_fee = head.get("late_fee") or {"mode": "none"}
        charge = head.get("charge") or {}
        kind = str(head["kind"])
        payload = self._list_item(head, scopes)
        payload.update(
            {
                "line_description": head.get("line_description"),
                "fee_start_rule": head["fee_start_rule"],
                "fee_start_date": _public_date(head.get("fee_start_date")),
                "due_within_days": int(head["due_within_days"]),
                "invoice_day": int(head["invoice_day"]),
                "billing_cycle": head.get("billing_cycle"),
                "cycle_anchor_month": head.get("cycle_anchor_month"),
                "billing_months": months,
                "billing_months_sentence": sentence,
                "tax": _public_tax(tax),
                "late_fee": _public_late_fee(late_fee),
                "late_fee_example": late_fee_example(
                    kind=kind,
                    scopes=scopes,
                    charge=charge,
                    tax=tax,
                    late_fee=late_fee,
                ),
                "scopes": [_public_scope(kind, scope) for scope in _ordered_scopes(scopes)],
                "meter_read_day": head.get("meter_read_day"),
                "charge": None if kind == FeeHeadKind.MAINTENANCE.value else _public_charge(charge),
            }
        )
        return payload

    def _settings_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        """Build the finance-settings response, including the summary sentence."""
        return {
            "payment_retry_count": int(row["payment_retry_count"]),
            "payment_retry_interval_days": int(row["payment_retry_interval_days"]),
            "pre_due_reminder_count": int(row["pre_due_reminder_count"]),
            "pre_due_reminder_interval_days": int(row["pre_due_reminder_interval_days"]),
            "summary": settings_summary(
                payment_retry_count=int(row["payment_retry_count"]),
                payment_retry_interval_days=int(row["payment_retry_interval_days"]),
                pre_due_reminder_count=int(row["pre_due_reminder_count"]),
                pre_due_reminder_interval_days=int(row["pre_due_reminder_interval_days"]),
            ),
            "version": int(row["version"]),
        }


def _ordered_scopes(scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return scopes in Apartments, Plots, Commercial order."""
    order = {property_type: index for index, property_type in enumerate(PROPERTY_TYPE_ORDER)}
    return sorted(scopes, key=lambda scope: order.get(str(scope["property_type"]), 99))


def _public_scope(kind: str, scope: dict[str, Any]) -> dict[str, Any]:
    """Return one scope row for the editor, with rates only on maintenance."""
    property_type = str(scope["property_type"])
    payload: dict[str, Any] = {
        "property_type": property_type,
        "label": PROPERTY_TYPE_LABELS.get(property_type, property_type),
        "enabled": bool(scope["enabled"]),
        "rate_per_sqft": None,
        "minimum_amount": None,
    }
    if kind == FeeHeadKind.MAINTENANCE.value:
        payload["rate_per_sqft"] = money_str(scope["rate_per_sqft"])
        payload["minimum_amount"] = money_str(scope["minimum_amount"])
    return payload


def _public_date(value: Any) -> str | None:
    """Return a date as YYYY-MM-DD."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _public_tax(tax: dict[str, Any]) -> dict[str, Any]:
    """Return the tax object with the rate as a decimal string."""
    payload: dict[str, Any] = {"applicable": bool(tax.get("applicable"))}
    if tax.get("rate_percent") is not None:
        payload["rate_percent"] = money_str(tax["rate_percent"])
    else:
        payload["rate_percent"] = None
    return payload


def _public_late_fee(late_fee: dict[str, Any]) -> dict[str, Any]:
    """Return the late-fee object with amounts as decimal strings."""
    mode = str(late_fee.get("mode") or "none")
    if mode == "flat":
        return {
            "mode": "flat",
            "steps": [
                {
                    "days_overdue": int(step["days_overdue"]),
                    "amount": money_str(step["amount"]),
                }
                for step in late_fee.get("steps") or []
            ],
        }
    if mode == "interest":
        return {
            "mode": "interest",
            "annual_percent": money_str(late_fee.get("annual_percent") or 0),
        }
    return {"mode": "none"}


def _public_charge(charge: dict[str, Any]) -> dict[str, Any]:
    """Return charge amounts as decimal strings."""
    return {
        key: money_str(value) if _looks_like_money(value) else value
        for key, value in charge.items()
    }


def _looks_like_money(value: Any) -> bool:
    """Return whether a charge value should be formatted as money."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        try:
            Decimal(value)
        except Exception:
            return False
        return True
    return False
