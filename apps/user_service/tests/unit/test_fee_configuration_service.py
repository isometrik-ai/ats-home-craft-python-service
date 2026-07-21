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
from libs.shared_utils.http_exceptions import ValidationException


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
