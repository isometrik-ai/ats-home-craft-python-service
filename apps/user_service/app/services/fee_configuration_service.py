"""Fee configuration orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.project_fee_rates_repository import (
    ProjectFeeRatesRepository,
)
from apps.user_service.app.db.repositories.project_fee_settings_repository import (
    ProjectFeeSettingsRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import FeeStartTrigger, UnitConfigKind
from apps.user_service.app.schemas.fee_configuration import (
    FeeConfigurationRate,
    FeeConfigurationRateResponse,
    FeeConfigurationResponse,
    FeeConfigurationSettings,
    FeeConfigurationSettingsResponse,
    FeeConfigurationWarnings,
    FeePreviewResponse,
    FeeRatePreview,
    UpsertFeeConfigurationRequest,
)
from apps.user_service.app.services.fee_calculation_service import (
    compute_period_fee_minor,
    convert_major_to_minor,
    convert_minor_to_major,
    convert_unit_area_to_sqft,
    fee_rate_input_from_row,
    resolve_area_sqft_from_unit_row,
)
from apps.user_service.app.services.fee_property_types import (
    applicable_unit_config_kinds,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

DEFAULT_SAMPLE_AREA_SQFT = 1500.0


class FeeConfigurationService:
    """Business rules for project fee configuration."""

    def __init__(self, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self._org_id = user_context.organization_id
        self._user_id = user_context.user_id
        self.settings_repo = ProjectFeeSettingsRepository(db_connection)
        self.rates_repo = ProjectFeeRatesRepository(db_connection)
        self.projects_repo = ProjectsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)

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
    def _default_settings() -> FeeConfigurationSettingsResponse:
        """Return default fee settings for projects without a saved row."""
        settings = FeeConfigurationSettings()
        return FeeConfigurationSettingsResponse(
            **settings.model_dump(),
            first_reminder_lead_days=settings.reminder_count * settings.reminder_interval_days,
        )

    def _build_warnings(
        self, project: dict[str, Any], rates: list[dict[str, Any]]
    ) -> FeeConfigurationWarnings:
        """Build non-blocking configuration warnings for the UI."""
        possession_missing = project.get("possession_date") is None and any(
            row.get("fee_start_trigger") == FeeStartTrigger.POSSESSION_DATE.value for row in rates
        )
        return FeeConfigurationWarnings(possession_date_missing=possession_missing)

    def _rate_preview(self, row: dict[str, Any]) -> FeeRatePreview:
        """Compute a sample-area preview for one rate row."""
        preview = compute_period_fee_minor(
            area_sqft=DEFAULT_SAMPLE_AREA_SQFT,
            rate=fee_rate_input_from_row(row),
        )
        return FeeRatePreview(
            sample_area=DEFAULT_SAMPLE_AREA_SQFT,
            sample_area_unit=row["measurement_unit"],
            computed_period_fee=convert_minor_to_major(preview.period_amount_minor),
            minimum_applied=preview.minimum_applied,
        )

    def _serialize_rate_row(self, row: dict[str, Any]) -> FeeConfigurationRateResponse:
        """Map a DB rate row to the API response model."""
        return FeeConfigurationRateResponse(
            unit_config_kind=UnitConfigKind(row["unit_config_kind"]),
            rate_amount=convert_minor_to_major(int(row["rate_amount_minor_per_unit"])),
            measurement_unit=row["measurement_unit"],
            billing_frequency=row["billing_frequency"],
            fee_start_trigger=row["fee_start_trigger"],
            start_offset_days=row.get("start_offset_days"),
            minimum_fee=convert_minor_to_major(int(row.get("minimum_fee_minor") or 0)),
            preview=self._rate_preview(row),
        )

    def _serialize_settings_row(
        self, row: dict[str, Any] | None
    ) -> FeeConfigurationSettingsResponse:
        """Map a DB settings row to the API response model."""
        if not row:
            return self._default_settings()
        reminder_count = int(row["reminder_count"])
        reminder_interval = int(row["reminder_interval_days"])
        return FeeConfigurationSettingsResponse(
            currency=row["currency"],
            billing_cycle_type=row["billing_cycle_type"],
            retry_count=int(row["retry_count"]),
            retry_interval_days=int(row["retry_interval_days"]),
            reminder_count=reminder_count,
            reminder_interval_days=reminder_interval,
            exhausted_retry_action=row["exhausted_retry_action"],
            first_reminder_lead_days=reminder_count * reminder_interval,
        )

    async def get_configuration(self, *, project_id: str) -> dict[str, Any]:
        """Return full fee configuration for a project."""
        project = await self._ensure_project(project_id)
        tabs = applicable_unit_config_kinds(project.get("property_types"))
        settings_row = await self.settings_repo.get_by_project_id(
            organization_id=self._org_id,
            project_id=project_id,
        )
        rate_rows = await self.rates_repo.list_by_project_id(
            organization_id=self._org_id,
            project_id=project_id,
        )
        payload = FeeConfigurationResponse(
            project_id=project_id,
            is_configured=bool(settings_row and settings_row.get("is_configured")),
            configured_at=(serialize_row(settings_row)["configured_at"] if settings_row else None),
            settings=self._serialize_settings_row(settings_row),
            applicable_tabs=tabs,
            rates=[self._serialize_rate_row(row) for row in rate_rows],
            warnings=self._build_warnings(project, rate_rows),
        )
        return payload.model_dump()

    def _validate_rate_tabs(
        self,
        *,
        applicable: list[UnitConfigKind],
        rates: list[FeeConfigurationRate],
    ) -> None:
        """Ensure submitted rate tabs match the project's property types."""
        applicable_values = {kind.value for kind in applicable}
        seen: set[str] = set()
        for rate in rates:
            kind = rate.unit_config_kind.value
            if kind in seen:
                raise ValidationException(
                    message_key="fee_configuration.errors.rate_tab_not_applicable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            seen.add(kind)
            if kind not in applicable_values:
                raise ValidationException(
                    message_key="fee_configuration.errors.rate_tab_not_applicable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if rate.fee_start_trigger == FeeStartTrigger.AFTER_DAYS and not rate.start_offset_days:
                raise ValidationException(
                    message_key="fee_configuration.errors.start_offset_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

    @staticmethod
    def _is_configured(applicable: list[UnitConfigKind], rates: list[FeeConfigurationRate]) -> bool:
        """True when every applicable tab has a submitted rate row."""
        if not applicable:
            return False
        provided = {rate.unit_config_kind.value for rate in rates}
        return all(kind.value in provided for kind in applicable)

    async def upsert_configuration(
        self,
        *,
        project_id: str,
        body: UpsertFeeConfigurationRequest,
    ) -> dict[str, Any]:
        """Validate and persist fee configuration."""
        project = await self._ensure_project(project_id)
        applicable = applicable_unit_config_kinds(project.get("property_types"))
        self._validate_rate_tabs(applicable=applicable, rates=body.rates)
        configured = self._is_configured(applicable, body.rates)
        now = datetime.now(UTC)
        settings_data = {
            **body.settings.model_dump(),
            "billing_cycle_type": body.settings.billing_cycle_type.value,
            "exhausted_retry_action": body.settings.exhausted_retry_action.value,
            "is_configured": configured,
            "configured_at": now if configured else None,
            "configured_by": self._user_id if configured else None,
        }
        await self.settings_repo.upsert(
            organization_id=self._org_id,
            project_id=project_id,
            data=settings_data,
        )
        rate_payloads = [
            {
                "unit_config_kind": rate.unit_config_kind.value,
                "rate_amount_minor_per_unit": convert_major_to_minor(rate.rate_amount),
                "measurement_unit": rate.measurement_unit.value,
                "billing_frequency": rate.billing_frequency.value,
                "fee_start_trigger": rate.fee_start_trigger.value,
                "start_offset_days": rate.start_offset_days,
                "minimum_fee_minor": convert_major_to_minor(rate.minimum_fee),
            }
            for rate in body.rates
        ]
        await self.rates_repo.upsert_batch(
            organization_id=self._org_id,
            project_id=project_id,
            rates=rate_payloads,
        )
        await self.rates_repo.delete_kinds_not_in(
            organization_id=self._org_id,
            project_id=project_id,
            kinds=[rate.unit_config_kind.value for rate in body.rates],
        )
        return await self.get_configuration(project_id=project_id)

    async def preview(
        self,
        *,
        project_id: str,
        unit_config_kind: UnitConfigKind,
        unit_id: str | None = None,
        area: float | None = None,
        measurement_unit: str | None = None,
    ) -> dict[str, Any]:
        """Compute a fee preview for a unit or sample area."""
        project = await self._ensure_project(project_id)
        applicable = applicable_unit_config_kinds(project.get("property_types"))
        if unit_config_kind not in applicable:
            raise ValidationException(
                message_key="fee_configuration.errors.rate_tab_not_applicable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        rate_rows = await self.rates_repo.list_by_project_id(
            organization_id=self._org_id,
            project_id=project_id,
        )
        rate_row = next(
            (row for row in rate_rows if row["unit_config_kind"] == unit_config_kind.value),
            None,
        )
        if not rate_row:
            raise ValidationException(
                message_key="fee_configuration.errors.configuration_incomplete",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        area_sqft: float | None = None
        if unit_id:
            unit_row = await self.units_repo.get_unit_detail_base(
                organization_id=self._org_id,
                project_id=project_id,
                unit_id=unit_id,
            )
            if not unit_row:
                raise NotFoundException(
                    message_key="project_setup.errors.unit_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            area_sqft = resolve_area_sqft_from_unit_row(unit_row)
        elif area is not None and measurement_unit:
            area_sqft = convert_unit_area_to_sqft(area, measurement_unit)
        if area_sqft is None:
            area_sqft = DEFAULT_SAMPLE_AREA_SQFT
        preview = compute_period_fee_minor(
            area_sqft=area_sqft,
            rate=fee_rate_input_from_row(rate_row),
        )
        response = FeePreviewResponse(
            unit_config_kind=unit_config_kind,
            area=round(area_sqft, 2),
            measurement_unit="sq_ft",
            billing_frequency=rate_row["billing_frequency"],
            computed_period_fee=convert_minor_to_major(preview.period_amount_minor),
            minimum_applied=preview.minimum_applied,
            currency=rate_row.get("currency", "INR"),
        )
        return response.model_dump()
