"""Unit tests for fee configuration labels, validation, and version conflicts."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.enums.fee_configuration import (
    FeeBillingCycle,
    FeeFrequency,
    FeeHeadKind,
    FeeHeadStatus,
    FeeStartRule,
)
from apps.user_service.app.schemas.enums.property import PropertyType
from apps.user_service.app.schemas.fee_configuration import (
    CreateFeeHeadRequest,
    CreateFinanceSettingsRequest,
    FeeHeadScopeInput,
    LateFeeInput,
    TaxInput,
    UpdateFeeHeadRequest,
    UpdateFeeHeadStatusRequest,
    UpdateFinanceSettingsRequest,
)
from apps.user_service.app.services.fee_configuration_labels import (
    billing_months_sentence,
    charge_summary,
    frequency_label,
    late_fee_example,
    settings_summary,
)
from apps.user_service.app.services.fee_configuration_service import (
    FeeConfigurationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

ORG_ID = "org-1"
PROJECT_ID = "project-1"
FEE_HEAD_ID = "fee-1"


def _scopes(
    *,
    apartments: tuple[str, str] = ("3.25", "500"),
    plots: tuple[str, str] = ("1.75", "400"),
    commercial: tuple[str, str] = ("5.50", "1200"),
    enabled: tuple[bool, bool, bool] = (True, True, True),
) -> list[dict]:
    rows = (
        (PropertyType.RESIDENTIAL.value, apartments, enabled[0]),
        (PropertyType.PLOTS.value, plots, enabled[1]),
        (PropertyType.COMMERCIAL.value, commercial, enabled[2]),
    )
    return [
        {
            "property_type": property_type,
            "enabled": is_enabled,
            "rate_per_sqft": rate,
            "minimum_amount": minimum,
        }
        for property_type, (rate, minimum), is_enabled in rows
    ]


def _maintenance_body(**overrides) -> UpdateFeeHeadRequest:
    payload = {
        "version": 1,
        "name": "Maintenance (CAM)",
        "status": FeeHeadStatus.ACTIVE,
        "frequency": FeeFrequency.MONTHLY,
        "line_description": "Housekeeping, security, lifts, landscaping and common-area power.",
        "fee_start_rule": FeeStartRule.FIRST_OF_NEXT_MONTH,
        "due_within_days": 10,
        "invoice_day": 1,
        "tax": TaxInput(applicable=True, rate_percent=Decimal("18")),
        "late_fee": LateFeeInput(mode="interest", annual_percent=Decimal("18")),
        "scopes": [
            FeeHeadScopeInput(
                property_type=PropertyType.RESIDENTIAL,
                enabled=True,
                rate_per_sqft=Decimal("3.25"),
                minimum_amount=Decimal("500"),
            ),
            FeeHeadScopeInput(
                property_type=PropertyType.PLOTS,
                enabled=True,
                rate_per_sqft=Decimal("1.75"),
                minimum_amount=Decimal("400"),
            ),
            FeeHeadScopeInput(
                property_type=PropertyType.COMMERCIAL,
                enabled=False,
                rate_per_sqft=Decimal("5.50"),
                minimum_amount=Decimal("1200"),
            ),
        ],
    }
    payload.update(overrides)
    return UpdateFeeHeadRequest(**payload)


def _head(*, kind: str = "maintenance", version: int = 1) -> dict:
    return {
        "id": FEE_HEAD_ID,
        "kind": kind,
        "category": "maintenance",
        "name": "Maintenance (CAM)",
        "line_description": None,
        "status": "inactive",
        "frequency": "monthly",
        "billing_cycle": None,
        "cycle_anchor_month": None,
        "fee_start_rule": "first_of_next_month",
        "due_within_days": 10,
        "invoice_day": 1,
        "meter_read_day": None,
        "charge": {},
        "tax": {"applicable": False},
        "late_fee": {"mode": "none"},
        "version": version,
    }


def _service() -> FeeConfigurationService:
    service = FeeConfigurationService(
        db_connection=AsyncMock(),
        user_context=UserContext(
            user_id="user-1",
            email="admin@example.com",
            organization_id=ORG_ID,
        ),
    )
    service.repo = AsyncMock()
    return service


def test_prototype_charge_summaries():
    """List copy matches the agreed prototype strings."""
    maintenance = charge_summary(kind="maintenance", scopes=_scopes(), charge=None)
    electricity = charge_summary(
        kind="electricity",
        scopes=[],
        charge={
            "grid_fixed_amount": "120",
            "grid_unit_rate": "8.5",
            "dg_fixed_amount": "150",
            "dg_unit_rate": "22",
        },
    )
    club = charge_summary(kind="club", scopes=[], charge={"amount": "1500"})
    assert maintenance == "₹3.25 / sq ft · min ₹500 · varies by type"
    assert electricity == "₹120 + ₹8.5/kWh · DG ₹150 + ₹22/kWh"
    assert club == "₹1,500 / month"


def test_club_late_fee_sentence_matches_worked_check():
    """₹1,500 at 18% tax and 18% interest is a ₹1,770 bill picking up ₹27 a month."""
    sentence = late_fee_example(
        kind="club",
        scopes=[],
        charge={"amount": "1500"},
        tax={"applicable": True, "rate_percent": "18"},
        late_fee={"mode": "interest", "annual_percent": "18"},
    )
    assert sentence == ("A ₹1,770 bill left unpaid picks up ₹27 for each month it stays overdue.")


def test_billing_month_labels():
    """Calendar, financial, custom July, and pro-rata labels stay in step with the cycle."""
    calendar_months, calendar_sentence = billing_months_sentence(
        frequency="quarterly",
        billing_cycle="calendar_year",
        anchor_month=1,
    )
    financial_months, _financial_sentence = billing_months_sentence(
        frequency="quarterly",
        billing_cycle="financial_year",
        anchor_month=4,
    )
    custom_months, custom_sentence = billing_months_sentence(
        frequency="quarterly",
        billing_cycle="custom",
        anchor_month=7,
    )
    half_months, _half_sentence = billing_months_sentence(
        frequency="half_yearly",
        billing_cycle="financial_year",
        anchor_month=4,
    )
    annual_months, _annual_sentence = billing_months_sentence(
        frequency="annual",
        billing_cycle="calendar_year",
        anchor_month=1,
    )
    _pro_rata_months, pro_rata_sentence = billing_months_sentence(
        frequency="quarterly",
        billing_cycle="pro_rata",
        anchor_month=None,
    )
    assert calendar_months == [1, 4, 7, 10]
    assert calendar_sentence == "Raised in Jan, Apr, Jul, Oct each year."
    assert financial_months == [4, 7, 10, 1]
    assert custom_months == [7, 10, 1, 4]
    assert custom_sentence == "Raised in Jul, Oct, Jan, Apr each year."
    assert half_months == [4, 10]
    assert annual_months == [1]
    assert pro_rata_sentence == "Raised in each unit's possession month, then every quarter."
    assert (
        frequency_label(
            kind="maintenance",
            frequency="quarterly",
            billing_cycle="custom",
            anchor_month=7,
        )
        == "Quarterly · Jul, Oct, Jan, Apr"
    )
    assert (
        frequency_label(
            kind="maintenance",
            frequency="quarterly",
            billing_cycle="pro_rata",
            anchor_month=None,
        )
        == "Quarterly · by possession"
    )
    assert (
        frequency_label(
            kind="electricity",
            frequency="monthly",
            billing_cycle=None,
            anchor_month=None,
        )
        == "Monthly · on reading"
    )


def test_settings_sentence_pluralises_and_drops_zero_counts():
    """The dunning sentence follows the four numbers, including a zero count."""
    assert settings_summary(
        payment_retry_count=3,
        payment_retry_interval_days=2,
        pre_due_reminder_count=2,
        pre_due_reminder_interval_days=3,
    ) == (
        "A failed payment is retried 3 times, 2 days apart. "
        "2 reminders go out before the due date, 3 days apart. "
        "Exhausted retries escalate to the billing team."
    )
    assert settings_summary(
        payment_retry_count=1,
        payment_retry_interval_days=1,
        pre_due_reminder_count=0,
        pre_due_reminder_interval_days=3,
    ) == (
        "A failed payment is retried 1 time, 1 day apart. "
        "No reminders go out before the due date. "
        "Exhausted retries escalate to the billing team."
    )
    zero = settings_summary(
        payment_retry_count=0,
        payment_retry_interval_days=2,
        pre_due_reminder_count=2,
        pre_due_reminder_interval_days=3,
    )
    assert zero.startswith("Failed payments are not retried.")


@pytest.mark.asyncio
async def test_update_rejects_empty_name_and_does_not_write():
    """An empty name is a validation error and leaves the row unchanged."""
    service = _service()
    service.repo.get_head = AsyncMock(return_value=_head())
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(
            project_id=PROJECT_ID,
            fee_head_id=FEE_HEAD_ID,
            body=_maintenance_body(name="   "),
        )
    assert exc.value.message_key == "fee_configuration.errors.name_required"
    service.repo.update_head.assert_not_called()


@pytest.mark.asyncio
async def test_update_rejects_no_enabled_property_type():
    """Save is blocked when every property type is unticked."""
    service = _service()
    service.repo.get_head = AsyncMock(return_value=_head())
    body = _maintenance_body()
    for scope in body.scopes:
        scope.enabled = False
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(
            project_id=PROJECT_ID,
            fee_head_id=FEE_HEAD_ID,
            body=body,
        )
    assert exc.value.message_key == "fee_configuration.errors.no_property_type"


@pytest.mark.asyncio
async def test_electricity_frequency_is_locked_to_monthly():
    """A non-monthly electricity frequency is rejected."""
    service = _service()
    head = _head(kind=FeeHeadKind.ELECTRICITY.value)
    head["meter_read_day"] = 1
    head["charge"] = {
        "grid_fixed_amount": "0.00",
        "grid_unit_rate": "0.00",
        "dg_fixed_amount": "0.00",
        "dg_unit_rate": "0.00",
    }
    service.repo.get_head = AsyncMock(return_value=head)
    body = _maintenance_body(
        frequency=FeeFrequency.QUARTERLY,
        billing_cycle=FeeBillingCycle.CALENDAR_YEAR,
        meter_read_day=1,
        charge={
            "grid_fixed_amount": "120",
            "grid_unit_rate": "8.5",
            "dg_fixed_amount": "150",
            "dg_unit_rate": "22",
        },
    )
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(project_id=PROJECT_ID, fee_head_id=FEE_HEAD_ID, body=body)
    assert exc.value.message_key == "fee_configuration.errors.frequency_locked"


@pytest.mark.asyncio
async def test_flat_late_fee_rejects_duplicate_days_and_a_fifth_step():
    """Steps must be unique and there can be at most four."""
    service = _service()
    service.repo.get_head = AsyncMock(return_value=_head())
    duplicate = _maintenance_body(
        late_fee=LateFeeInput(
            mode="flat",
            steps=[
                {"days_overdue": 1, "amount": Decimal("100")},
                {"days_overdue": 1, "amount": Decimal("250")},
            ],
        )
    )
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(
            project_id=PROJECT_ID,
            fee_head_id=FEE_HEAD_ID,
            body=duplicate,
        )
    assert exc.value.message_key == "fee_configuration.errors.late_fee_steps"

    five = _maintenance_body(
        late_fee=LateFeeInput(
            mode="flat",
            steps=[{"days_overdue": day, "amount": Decimal("10")} for day in range(1, 6)],
        )
    )
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(project_id=PROJECT_ID, fee_head_id=FEE_HEAD_ID, body=five)
    assert exc.value.message_key == "fee_configuration.errors.late_fee_steps"


@pytest.mark.asyncio
async def test_interest_rate_of_zero_is_rejected():
    """Zero interest is not a stand-in for no late fee."""
    service = _service()
    service.repo.get_head = AsyncMock(return_value=_head())
    body = _maintenance_body(late_fee=LateFeeInput(mode="interest", annual_percent=Decimal("0")))
    with pytest.raises(ValidationException) as exc:
        await service.update_fee_head(project_id=PROJECT_ID, fee_head_id=FEE_HEAD_ID, body=body)
    assert exc.value.message_key == "fee_configuration.errors.invalid_interest_rate"


@pytest.mark.asyncio
async def test_stale_version_returns_conflict_and_does_not_replace_scopes():
    """A second save with an old version is a 409 and does not write scopes."""
    service = _service()
    service.repo.get_head = AsyncMock(return_value=_head(version=4))
    service.repo.update_head = AsyncMock(return_value=None)
    with pytest.raises(ConflictException) as exc:
        await service.update_fee_head(
            project_id=PROJECT_ID,
            fee_head_id=FEE_HEAD_ID,
            body=_maintenance_body(version=4),
        )
    assert exc.value.message_key == "fee_configuration.errors.version_conflict"
    service.repo.replace_scopes.assert_not_called()


@pytest.mark.asyncio
async def test_status_toggle_does_not_send_the_charge_body():
    """The list toggle updates status only and returns the list item."""
    service = _service()
    current = _head(version=2)
    updated = {**current, "status": "active", "version": 3}
    service.repo.get_head = AsyncMock(side_effect=[current, updated])
    service.repo.update_head = AsyncMock(return_value={"id": FEE_HEAD_ID})
    service.repo.list_scopes = AsyncMock(return_value=_scopes())

    result = await service.update_status(
        project_id=PROJECT_ID,
        fee_head_id=FEE_HEAD_ID,
        version=2,
        status=FeeHeadStatus.ACTIVE,
    )

    update_data = service.repo.update_head.await_args.kwargs["update_data"]
    assert set(update_data) == {"status", "updated_by"}
    assert update_data["status"] == "active"
    assert result["status"] == "active"
    assert result["version"] == 3
    assert "charge" not in result


def _create_body() -> CreateFeeHeadRequest:
    document = _maintenance_body().model_dump()
    document.pop("version")
    document["kind"] = FeeHeadKind.MAINTENANCE
    return CreateFeeHeadRequest(**document)


@pytest.mark.asyncio
async def test_create_fee_head_inserts_one_head_and_three_scopes():
    """Create writes one fee head of the requested kind and its three scopes."""
    service = _service()
    service.repo.get_head_by_kind = AsyncMock(return_value=None)
    service.repo.insert_head = AsyncMock(return_value=FEE_HEAD_ID)
    service.repo.insert_scopes = AsyncMock()
    service.repo.get_head = AsyncMock(return_value=_head())
    service.repo.list_scopes = AsyncMock(return_value=_scopes())

    result = await service.create_fee_head(project_id=PROJECT_ID, body=_create_body())

    insert = service.repo.insert_head.await_args.kwargs
    assert insert["kind"] == "maintenance"
    assert insert["category"] == "maintenance"
    assert len(service.repo.insert_scopes.await_args.kwargs["scopes"]) == 3
    assert result["id"] == FEE_HEAD_ID


@pytest.mark.asyncio
async def test_create_fee_head_rejects_a_duplicate_kind():
    """A project can hold only one fee head of each kind."""
    service = _service()
    service.repo.get_head_by_kind = AsyncMock(return_value=_head())
    service.repo.insert_head = AsyncMock()

    with pytest.raises(ConflictException):
        await service.create_fee_head(project_id=PROJECT_ID, body=_create_body())

    service.repo.insert_head.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_settings_inserts_the_four_numbers():
    """Finance settings are created by the API, with the numbers the client sends."""
    service = _service()
    service.repo.get_settings = AsyncMock(
        side_effect=[
            None,
            {
                "payment_retry_count": 3,
                "payment_retry_interval_days": 2,
                "pre_due_reminder_count": 2,
                "pre_due_reminder_interval_days": 3,
                "version": 1,
            },
        ]
    )
    service.repo.insert_settings = AsyncMock()
    body = CreateFinanceSettingsRequest(
        payment_retry_count=3,
        payment_retry_interval_days=2,
        pre_due_reminder_count=2,
        pre_due_reminder_interval_days=3,
    )

    result = await service.create_settings(project_id=PROJECT_ID, body=body)

    saved = service.repo.insert_settings.await_args.kwargs["settings"]
    assert saved["payment_retry_count"] == 3
    assert result["version"] == 1


def test_disabled_maintenance_scope_keeps_its_rate_in_the_prepared_write():
    """Unticking a property type stores the rate and marks the row disabled."""
    service = _service()
    prepared = service._prepare_update(
        kind="maintenance",
        body=_maintenance_body(),
        current=_head(),
    )
    commercial = next(
        scope for scope in prepared["scopes"] if scope["property_type"] == "commercial"
    )
    assert commercial["enabled"] is False
    assert commercial["rate_per_sqft"] == Decimal("5.50")
    assert commercial["minimum_amount"] == Decimal("1200.00")


def test_status_request_does_not_require_a_charge_body():
    """The toggle payload is version plus status."""
    body = UpdateFeeHeadStatusRequest(version=2, status=FeeHeadStatus.INACTIVE)
    assert body.model_dump() == {"version": 2, "status": FeeHeadStatus.INACTIVE}


def test_settings_request_is_four_numbers():
    """Settings save does not accept fee-specific fields."""
    body = UpdateFinanceSettingsRequest(
        version=1,
        payment_retry_count=3,
        payment_retry_interval_days=2,
        pre_due_reminder_count=2,
        pre_due_reminder_interval_days=3,
    )
    assert set(body.model_dump()) == {
        "version",
        "payment_retry_count",
        "payment_retry_interval_days",
        "pre_due_reminder_count",
        "pre_due_reminder_interval_days",
    }
