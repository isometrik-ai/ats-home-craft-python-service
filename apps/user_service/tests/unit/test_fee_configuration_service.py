"""Unit tests for FeeConfigurationService."""

from __future__ import annotations

from typing import Any

import pytest

from apps.user_service.app.schemas.enums import (
    MeasurementUnit,
    PropertyType,
    UnitConfigKind,
)
from apps.user_service.app.schemas.fee_configuration import (
    FeeConfigurationRate,
    FeeConfigurationSettings,
    UpsertFeeConfigurationRequest,
)
from apps.user_service.app.services.fee_configuration_service import (
    FeeConfigurationService,
)
from apps.user_service.app.services.fee_property_types import (
    applicable_unit_config_kinds,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException


def _user_context() -> UserContext:
    """Build a minimal admin user context for service tests."""
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

    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row
        self.last_upsert: dict[str, Any] | None = None

    async def get_by_project_id(self, **_kwargs):
        """Return configured settings row."""
        return self.row

    async def upsert(self, **_kwargs):
        """Record upsert call."""
        self.last_upsert = _kwargs
        return self.row or {"is_configured": True}


class _FakeRatesRepo:
    """In-memory fake ProjectFeeRatesRepository."""

    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.last_batch: list[dict[str, Any]] | None = None

    async def list_by_project_id(self, **_kwargs):
        """Return configured rate rows."""
        return self.rows

    async def upsert_batch(self, **_kwargs):
        """Record upsert batch call."""
        self.last_batch = _kwargs.get("rates")
        return self.rows

    async def delete_kinds_not_in(self, **_kwargs):
        """No-op delete helper."""
        return None


class _FakeUnitsRepo:
    """In-memory fake UnitsRepository."""

    async def get_unit_detail_base(self, **_kwargs):
        """Return no unit row by default."""
        return None


def _service(**kwargs) -> FeeConfigurationService:
    """Build a FeeConfigurationService with fake repositories."""
    service = FeeConfigurationService.__new__(FeeConfigurationService)
    service._org_id = "org-1"
    service._user_id = "user-1"
    service.projects_repo = kwargs.get("projects_repo", _FakeProjectsRepo(None))
    service.settings_repo = kwargs.get("settings_repo", _FakeSettingsRepo())
    service.rates_repo = kwargs.get("rates_repo", _FakeRatesRepo())
    service.units_repo = kwargs.get("units_repo", _FakeUnitsRepo())
    return service


def test_applicable_tabs_from_property_types() -> None:
    """Property types should map to the correct fee tabs."""
    tabs = applicable_unit_config_kinds([PropertyType.RESIDENTIAL.value, PropertyType.PLOTS.value])
    assert tabs == [UnitConfigKind.APARTMENT, UnitConfigKind.PLOT]


def test_is_configured_requires_all_applicable_tabs() -> None:
    """Configuration is incomplete until every applicable tab has a rate."""
    applicable = [UnitConfigKind.APARTMENT, UnitConfigKind.COMMERCIAL]
    rates = [
        FeeConfigurationRate(
            unit_config_kind=UnitConfigKind.APARTMENT,
            rate_amount=1.0,
            measurement_unit=MeasurementUnit.SQ_FT,
        )
    ]
    assert FeeConfigurationService._is_configured(applicable, rates) is False


def test_validate_rate_tab_rejects_inapplicable_kind() -> None:
    """Submitting a rate for a non-applicable tab should fail validation."""
    service = _service()
    applicable = [UnitConfigKind.APARTMENT]
    rates = [
        FeeConfigurationRate(
            unit_config_kind=UnitConfigKind.PLOT,
            rate_amount=1.0,
            measurement_unit=MeasurementUnit.SQ_FT,
        )
    ]
    with pytest.raises(ValidationException):
        service._validate_rate_tabs(applicable=applicable, rates=rates)


@pytest.mark.asyncio
async def test_upsert_marks_configured_when_all_tabs_present() -> None:
    """Saving all required tabs should mark the configuration complete."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.RESIDENTIAL.value],
        "possession_date": None,
    }
    settings_repo = _FakeSettingsRepo()
    rates_repo = _FakeRatesRepo(
        [
            {
                "unit_config_kind": "apartment",
                "rate_amount_minor_per_unit": 125,
                "measurement_unit": "sq_ft",
                "billing_frequency": "monthly",
                "fee_start_trigger": "possession_date",
                "minimum_fee_minor": 0,
            }
        ]
    )
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        settings_repo=settings_repo,
        rates_repo=rates_repo,
    )
    body = UpsertFeeConfigurationRequest(
        settings=FeeConfigurationSettings(),
        rates=[
            FeeConfigurationRate(
                unit_config_kind=UnitConfigKind.APARTMENT,
                rate_amount=1.25,
                measurement_unit=MeasurementUnit.SQ_FT,
            )
        ],
    )
    await service.upsert_configuration(project_id="proj-1", body=body)
    assert settings_repo.last_upsert is not None
    assert settings_repo.last_upsert["data"]["is_configured"] is True


def test_is_configured_false_when_no_applicable_tabs() -> None:
    """Projects without property types should never be marked configured."""
    assert FeeConfigurationService._is_configured([], []) is False


@pytest.mark.asyncio
async def test_get_configuration_defaults_and_warnings() -> None:
    """GET should return defaults and possession warnings."""
    project = {
        "id": "proj-1",
        "property_types": ["residential"],
        "possession_date": None,
    }
    rates = [
        {
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "start_offset_days": None,
            "minimum_fee_minor": 0,
        }
    ]
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        rates_repo=_FakeRatesRepo(rates),
    )
    data = await service.get_configuration(project_id="proj-1")
    assert data["is_configured"] is False
    assert data["warnings"]["possession_date_missing"] is True
    assert len(data["rates"]) == 1


@pytest.mark.asyncio
async def test_preview_raises_when_rate_missing() -> None:
    """Preview should fail when the requested tab has no saved rate."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.RESIDENTIAL.value],
        "possession_date": None,
    }
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        rates_repo=_FakeRatesRepo([]),
    )
    with pytest.raises(ValidationException):
        await service.preview(
            project_id="proj-1",
            unit_config_kind=UnitConfigKind.APARTMENT,
            area=1500,
            measurement_unit="sq_ft",
        )


@pytest.mark.asyncio
async def test_preview_loads_area_from_unit_row() -> None:
    """Preview with unit id should resolve billable area from inventory."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.RESIDENTIAL.value],
        "possession_date": None,
    }
    rates = [
        {
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "start_offset_days": None,
            "minimum_fee_minor": 0,
        }
    ]

    class _UnitsWithArea(_FakeUnitsRepo):
        """Fake units repo that returns a unit row with carpet area."""

        async def get_unit_detail_base(self, **_kwargs):
            return {"carpet_area_sqft": 1200, "area_sqft": None, "plot_size_sqft": None}

    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        rates_repo=_FakeRatesRepo(rates),
        units_repo=_UnitsWithArea(),
    )
    data = await service.preview(
        project_id="proj-1",
        unit_config_kind=UnitConfigKind.APARTMENT,
        unit_id="unit-1",
    )
    assert data["area"] == 1200.0
    assert data["computed_period_fee"] == 1200.0


def test_validate_rate_tab_rejects_duplicate_kinds() -> None:
    """Duplicate rate tabs in one request should fail validation."""
    service = _service()
    applicable = [UnitConfigKind.APARTMENT]
    rate = FeeConfigurationRate(
        unit_config_kind=UnitConfigKind.APARTMENT,
        rate_amount=1.0,
        measurement_unit=MeasurementUnit.SQ_FT,
    )
    with pytest.raises(ValidationException):
        service._validate_rate_tabs(applicable=applicable, rates=[rate, rate])


@pytest.mark.asyncio
async def test_ensure_project_not_found() -> None:
    """Missing project raises NotFoundException."""
    service = _service(projects_repo=_FakeProjectsRepo(None))
    with pytest.raises(NotFoundException):
        await service.get_configuration(project_id="missing")


def test_serialize_settings_row_from_db() -> None:
    """Saved settings rows map reminder lead days."""
    service = _service()
    row = {
        "currency": "INR",
        "billing_cycle_type": "calendar_year",
        "retry_count": 2,
        "retry_interval_days": 3,
        "reminder_count": 2,
        "reminder_interval_days": 7,
        "exhausted_retry_action": "escalate_to_billing_team",
    }
    settings = service._serialize_settings_row(row)
    assert settings.first_reminder_lead_days == 14


def test_validate_rate_tab_requires_start_offset() -> None:
    """AFTER_DAYS trigger requires start_offset_days."""
    from apps.user_service.app.schemas.enums import FeeStartTrigger

    service = _service()
    applicable = [UnitConfigKind.APARTMENT]
    rate = FeeConfigurationRate.model_construct(
        unit_config_kind=UnitConfigKind.APARTMENT,
        rate_amount=1.0,
        measurement_unit=MeasurementUnit.SQ_FT,
        fee_start_trigger=FeeStartTrigger.AFTER_DAYS,
        start_offset_days=None,
    )
    with pytest.raises(ValidationException):
        service._validate_rate_tabs(applicable=applicable, rates=[rate])


@pytest.mark.asyncio
async def test_preview_rejects_inapplicable_kind() -> None:
    """Preview rejects tabs not applicable to project property types."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.COMMERCIAL.value],
        "possession_date": None,
    }
    service = _service(projects_repo=_FakeProjectsRepo(project))
    with pytest.raises(ValidationException):
        await service.preview(
            project_id="proj-1",
            unit_config_kind=UnitConfigKind.APARTMENT,
        )


@pytest.mark.asyncio
async def test_preview_unit_not_found() -> None:
    """Preview with missing unit raises NotFoundException."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.RESIDENTIAL.value],
        "possession_date": None,
    }
    rates = [
        {
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "minimum_fee_minor": 0,
        }
    ]
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        rates_repo=_FakeRatesRepo(rates),
        units_repo=_FakeUnitsRepo(),
    )
    with pytest.raises(NotFoundException):
        await service.preview(
            project_id="proj-1",
            unit_config_kind=UnitConfigKind.APARTMENT,
            unit_id="missing-unit",
        )


@pytest.mark.asyncio
async def test_preview_uses_area_conversion() -> None:
    """Preview converts explicit area + measurement unit to sqft."""
    project = {
        "id": "proj-1",
        "property_types": [PropertyType.RESIDENTIAL.value],
        "possession_date": None,
    }
    rates = [
        {
            "unit_config_kind": "apartment",
            "rate_amount_minor_per_unit": 100,
            "measurement_unit": "sq_ft",
            "billing_frequency": "monthly",
            "fee_start_trigger": "possession_date",
            "minimum_fee_minor": 0,
        }
    ]
    service = _service(
        projects_repo=_FakeProjectsRepo(project),
        rates_repo=_FakeRatesRepo(rates),
    )
    data = await service.preview(
        project_id="proj-1",
        unit_config_kind=UnitConfigKind.APARTMENT,
        area=1000,
        measurement_unit="sq_ft",
    )
    assert data["area"] == 1000.0
