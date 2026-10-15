"""Project workbook import: parse Project_data_bulk.xlsx and call create services."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

from apps.user_service.app.db.repositories.import_job_rows_repository import (
    ImportJobRowsRepository,
)
from apps.user_service.app.db.repositories.import_jobs_repository import (
    ImportJobsRepository,
)
from apps.user_service.app.schemas.enums import (
    ContactsImportFileType,
    ContactsImportJobStatus,
    ContactsImportType,
    FacilityType,
    ParkingFacilitySubtype,
    ProjectSetupStep,
    UnitConfigKind,
    UnitNumberingPattern,
    UnitStatus,
)
from apps.user_service.app.schemas.project_inventory import (
    BulkCreateUnitsRequest,
    CreateFacilityRequest,
    CreateParkingZoneRequest,
    CreateUnitConfigRequest,
    CreateUnitRequest,
)
from apps.user_service.app.schemas.project_setup import (
    CreateFloorBulkItem,
    CreateFloorRequest,
    CreateProjectRequest,
    CreateTowerGateBulkItem,
    CreateTowerGateRequest,
    CreateTowerLiftRequest,
    CreateTowerRequest,
    CreateTowerWingRequest,
)
from apps.user_service.app.services.facilities_service import FacilitiesService
from apps.user_service.app.services.inventory_service import InventoryService
from apps.user_service.app.services.project_setup_service import (
    ProjectSetupService,
    compute_visible_steps,
)
from apps.user_service.app.services.project_workbook_parser import (
    ParsedProjectWorkbook,
    WorkbookRowError,
    build_tower_floor_specs,
    parse_project_workbook,
    unit_codes_for_floor,
)
from apps.user_service.app.services.projects_service import ProjectsService
from apps.user_service.app.services.site_map_service import SiteMapService
from apps.user_service.app.services.towers_service import TowersService
from apps.user_service.app.services.unit_configs_service import UnitConfigsService
from apps.user_service.app.services.units_service import UnitsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

logger = logging.getLogger(__name__)

_BACKGROUND_IMPORTS: set[asyncio.Task[None]] = set()
_TERMINAL_JOB_STATUSES = {
    ContactsImportJobStatus.COMPLETED.value,
    ContactsImportJobStatus.FAILED.value,
}

MAX_WORKBOOK_BYTES = 20 * 1024 * 1024
UNIT_BULK_CHUNK = 200
SHEET_ROW_OFFSETS = {
    "1. Project": 100_000,
    "2. Towers": 200_000,
    "3. Wings": 300_000,
    "4. Unit types": 400_000,
    "5. Units and plots": 500_000,
    "6. Amenities": 600_000,
    "7. Parking zones": 700_000,
    "8. Gates and lifts": 800_000,
}


class ProjectsImportService:
    """Create a projects import job and apply the workbook through existing services."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.jobs_repo = ImportJobsRepository(db_connection=db_connection)
        self.rows_repo = ImportJobRowsRepository(db_connection=db_connection)
        self.projects_service = ProjectsService(
            db_connection=db_connection, user_context=user_context
        )
        self.towers_service = TowersService(db_connection=db_connection, user_context=user_context)
        self.configs_service = UnitConfigsService(
            db_connection=db_connection, user_context=user_context
        )
        self.units_service = UnitsService(db_connection=db_connection, user_context=user_context)
        self.facilities_service = FacilitiesService(
            db_connection=db_connection, user_context=user_context
        )
        self.inventory_service = InventoryService(
            db_connection=db_connection, user_context=user_context
        )
        self.site_map_service = SiteMapService(
            db_connection=db_connection, user_context=user_context
        )
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    async def create_job(
        self,
        *,
        organization_id: str,
        file_url: str,
        file_type: str,
        schema_version: int,
        community_admin_user_id: str,
    ) -> dict[str, Any]:
        """Persist a queued import job. Workbook processing happens in the background."""
        self._validate_file_url(file_url)
        if file_type != ContactsImportFileType.XLSX.value:
            raise ValidationException(
                message_key="projects_imports.errors.xlsx_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        return await self.jobs_repo.create_job(
            job_id=self._generate_job_id(),
            organization_id=organization_id,
            status=ContactsImportJobStatus.QUEUED.value,
            file_url=file_url,
            file_type=file_type,
            schema_version=schema_version,
            mapping=None,
            options={"community_admin_user_id": community_admin_user_id},
            import_type=ContactsImportType.PROJECTS.value,
        )

    async def process_job(self, *, job_id: str, organization_id: str) -> dict[str, Any]:
        """Download the workbook and create project inventory for a queued job."""
        job = await self.get_job(job_id=job_id, organization_id=organization_id)
        if job is None:
            logger.warning("projects_import_job_missing job_id=%s", job_id)
            return {}
        if str(job.get("status") or "") in _TERMINAL_JOB_STATUSES:
            return job

        job_key = str(job["job_id"])
        file_url = str(job.get("file_url") or "")
        options = job.get("options") or {}
        community_admin_user_id = str(options.get("community_admin_user_id") or "")
        if not community_admin_user_id:
            await self.jobs_repo.set_status_and_timestamps(
                job_id=job_key,
                organization_id=organization_id,
                status=ContactsImportJobStatus.FAILED.value,
                finished_at=datetime.now(UTC),
            )
            refreshed = await self.get_job(job_id=job_key, organization_id=organization_id)
            return refreshed or job

        await self.jobs_repo.set_status_and_timestamps(
            job_id=job_key,
            organization_id=organization_id,
            status=ContactsImportJobStatus.RUNNING.value,
            started_at=datetime.now(UTC),
        )

        try:
            payload = await self._download_workbook(file_url=file_url)
            parsed = parse_project_workbook(payload)
            if not parsed.projects:
                await self._finish_job(
                    job=job,
                    status=ContactsImportJobStatus.FAILED,
                    parsed=parsed,
                    extra_errors=parsed.errors,
                )
                refreshed = await self.get_job(job_id=job_key, organization_id=organization_id)
                return refreshed or job

            async with self.db_connection.transaction():
                apply_errors = await self._apply_workbook(
                    parsed=parsed,
                    community_admin_user_id=community_admin_user_id,
                )
            await self._finish_job(
                job=job,
                status=ContactsImportJobStatus.COMPLETED,
                parsed=parsed,
                extra_errors=apply_errors,
            )
        except (ValidationException, httpx.HTTPError):
            logger.exception("Projects import job %s failed during download/parse", job_key)
            await self.jobs_repo.set_status_and_timestamps(
                job_id=job_key,
                organization_id=organization_id,
                status=ContactsImportJobStatus.FAILED.value,
                finished_at=datetime.now(UTC),
            )
        except Exception:
            logger.exception("Projects import job %s failed", job_key)
            await self.jobs_repo.set_status_and_timestamps(
                job_id=job_key,
                organization_id=organization_id,
                status=ContactsImportJobStatus.FAILED.value,
                finished_at=datetime.now(UTC),
            )
            raise

        refreshed = await self.get_job(job_id=job_key, organization_id=organization_id)
        return refreshed or job

    async def get_job(self, *, job_id: str, organization_id: str) -> dict[str, Any] | None:
        """Fetch a projects import job scoped to the organization."""
        job = await self.jobs_repo.get_job(job_id=job_id, organization_id=organization_id)
        if job is None:
            return None
        if str(job.get("import_type") or "") != ContactsImportType.PROJECTS.value:
            return None
        return job

    async def list_job_error_rows(
        self,
        *,
        job_id: str,
        organization_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List row-ledger errors for a projects import job."""
        job = await self.get_job(job_id=job_id, organization_id=organization_id)
        if job is None:
            return ([], 0)
        return await self.rows_repo.list_error_rows(
            organization_id=organization_id,
            job_id=str(job.get("id") or ""),
            page=page,
            page_size=page_size,
        )

    async def _apply_workbook(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        community_admin_user_id: str,
    ) -> list[WorkbookRowError]:
        """Create or reuse projects, then apply inventory and complete setup."""
        errors: list[WorkbookRowError] = []
        projects_by_key: dict[str, str] = {}
        for item in parsed.projects:
            project_id, create_error = await self._get_or_create_project(
                item=item,
                community_admin_user_id=community_admin_user_id,
            )
            if project_id:
                projects_by_key[item["name_key"]] = project_id
            if create_error:
                errors.append(create_error)

        for project_key, project_id in projects_by_key.items():
            errors.extend(
                await self._apply_project_inventory(
                    parsed=parsed,
                    project_key=project_key,
                    project_id=project_id,
                )
            )
            await self._finalize_imported_setup(project_id=project_id)
        return errors

    async def _get_or_create_project(
        self,
        *,
        item: dict[str, Any],
        community_admin_user_id: str,
    ) -> tuple[str | None, WorkbookRowError | None]:
        """Reuse a project by name, or create it through create_project."""
        org_id = self.user_context.organization_id
        existing = await self.projects_service.projects_repo.get_project_by_name(
            organization_id=org_id,
            name=item["name"],
        )
        if existing:
            return str(existing["id"]), None
        try:
            created = await self.projects_service.create_project(
                CreateProjectRequest(
                    name=item["name"],
                    developer_name=item["developer_name"],
                    community_admin_user_id=community_admin_user_id,
                    gstin=item.get("gstin"),
                    possession_date=item.get("possession_date"),
                    address_line_1=item["address_line_1"],
                    address_line_2=item.get("address_line_2"),
                    pin_code=str(item["pin_code"]),
                    city=item["city"],
                    state=item["state"],
                    country=item.get("country") or "India",
                    latitude=item.get("latitude"),
                    longitude=item.get("longitude"),
                    property_types=item["property_types"],
                    primary_measurement_unit=item["primary_measurement_unit"],
                    units_count=item.get("units_count") or 0,
                )
            )
            return str(created["project_id"]), None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            recovered = await self.projects_service.projects_repo.get_project_by_name(
                organization_id=org_id,
                name=item["name"],
            )
            if recovered:
                logger.warning(
                    "projects_import_reused_project_after_create_error name=%s error=%s",
                    item["name"],
                    exc,
                )
                return str(recovered["id"]), None
            return None, WorkbookRowError(
                sheet="1. Project",
                row_number=int(item.get("sheet_row") or 0),
                code="project_create_failed",
                message=str(exc),
                raw=item,
            )

    async def _apply_project_inventory(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        project_key: str,
        project_id: str,
    ) -> list[WorkbookRowError]:
        """Apply project inventory to the project."""
        # pylint: disable=too-complex, too-many-branches
        errors: list[WorkbookRowError] = []
        towers_by_name: dict[str, dict[str, Any]] = {}
        known_wings = {
            (item["tower_name"].casefold(), item["name"].casefold())
            for item in parsed.wings
            if item["project_key"] == project_key
        }

        existing_towers = {
            str(row.get("name") or "").casefold(): row
            for row in await self.towers_service.list_towers(project_id=project_id)
        }
        for tower in [item for item in parsed.towers if item["project_key"] == project_key]:
            try:
                existing = existing_towers.get(tower["name"].casefold())
                if existing:
                    created = await self.towers_service.get_tower_detail(
                        project_id=project_id,
                        tower_id=str(existing["id"]),
                    )
                    created = await self._sync_existing_tower(
                        project_id=project_id,
                        tower_row=created,
                        parsed=parsed,
                        tower=tower,
                        known_wings=known_wings,
                    )
                else:
                    created = await self.towers_service.create_tower(
                        project_id=project_id,
                        body=self._tower_body(parsed, tower, known_wings),
                    )
                towers_by_name[tower["name"].casefold()] = created
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    WorkbookRowError(
                        sheet="2. Towers",
                        row_number=int(tower.get("sheet_row") or 0),
                        code="tower_create_failed",
                        message=str(exc),
                        raw=tower,
                    )
                )

        configs_by_name: dict[str, str] = {
            str(row.get("name") or "").casefold(): str(row["id"])
            for row in await self.configs_service.list_configs(
                project_id=project_id, config_kind=None
            )
        }
        for config in [item for item in parsed.unit_types if item["project_key"] == project_key]:
            if config["name"].casefold() in configs_by_name:
                continue
            try:
                created = await self.configs_service.create_config(
                    project_id=project_id,
                    body=CreateUnitConfigRequest(
                        config_kind=config["config_kind"],
                        name=config["name"],
                        code=config["code"],
                        bedrooms=config.get("bedrooms"),
                        bathrooms=config.get("bathrooms"),
                        area_sqft=config.get("area_sqft"),
                        carpet_area_sqft=config.get("carpet_area_sqft"),
                        balconies=config.get("balconies") or 0,
                        default_facing=config.get("default_facing"),
                        two_wheeler_parking_entitlement=config.get(
                            "two_wheeler_parking_entitlement"
                        )
                        or 0,
                        four_wheeler_parking_entitlement=config.get(
                            "four_wheeler_parking_entitlement"
                        )
                        or 0,
                        commercial_unit_type=config.get("commercial_unit_type"),
                        plot_type=config.get("plot_type"),
                    ),
                )
                configs_by_name[config["name"].casefold()] = str(created["id"])
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    WorkbookRowError(
                        sheet="4. Unit types",
                        row_number=int(config.get("sheet_row") or 0),
                        code="unit_type_create_failed",
                        message=str(exc),
                        raw=config,
                    )
                )

        existing_unit_codes = await self._existing_unit_codes(project_id=project_id)
        queued_codes = set(existing_unit_codes)
        unit_bodies: list[CreateUnitRequest] = []
        for unit in [item for item in parsed.units if item["project_key"] == project_key]:
            if unit["code"].casefold() in queued_codes:
                continue
            tower = towers_by_name.get((unit.get("tower_name") or "").casefold())
            config_id = configs_by_name.get(unit["type_name"].casefold())
            if not config_id:
                errors.append(
                    WorkbookRowError(
                        sheet="5. Units and plots",
                        row_number=int(unit.get("sheet_row") or 0),
                        code="unit_config_missing",
                        message="Unit type was not created for this row.",
                        raw=unit,
                    )
                )
                continue
            wing_id, floor_id = self._resolve_tower_refs(
                tower=tower,
                wing_name=unit.get("wing_name"),
                floor_level=unit.get("floor_level"),
                known_wings=known_wings,
            )
            queued_codes.add(unit["code"].casefold())
            unit_bodies.append(
                CreateUnitRequest(
                    tower_id=str(tower["id"]) if tower else None,
                    wing_id=wing_id,
                    floor_id=floor_id,
                    config_id=config_id,
                    code=unit["code"],
                    unit_label=unit.get("unit_label") or unit["code"],
                    status=unit.get("status"),
                    is_parking=False,
                )
            )
        for tower in [item for item in parsed.towers if item["project_key"] == project_key]:
            created = towers_by_name.get(tower["name"].casefold())
            if not created:
                continue
            for body in self._generated_unit_bodies(
                parsed=parsed,
                tower=tower,
                created_tower=created,
                configs_by_name=configs_by_name,
            ):
                if body.code.casefold() in queued_codes:
                    continue
                queued_codes.add(body.code.casefold())
                unit_bodies.append(body)
        for chunk in (
            unit_bodies[index : index + UNIT_BULK_CHUNK]
            for index in range(0, len(unit_bodies), UNIT_BULK_CHUNK)
        ):
            try:
                await self.units_service.create_units_bulk(
                    project_id=project_id,
                    body=BulkCreateUnitsRequest(units=chunk),
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    WorkbookRowError(
                        sheet="5. Units and plots",
                        row_number=0,
                        code="units_bulk_failed",
                        message=str(exc),
                    )
                )

        errors.extend(
            await self._apply_amenities(
                parsed=parsed,
                project_key=project_key,
                project_id=project_id,
                towers_by_name=towers_by_name,
            )
        )
        errors.extend(
            self._skip_unresolved_parking_zones(
                parsed=parsed,
                project_key=project_key,
                towers_by_name=towers_by_name,
                known_wings=known_wings,
            )
        )
        existing_zone_names = {
            str(row.get("name") or "").casefold()
            for row in await self.units_service.list_parking_zones(project_id=project_id)
        }
        for zone in [item for item in parsed.parking_zones if item["project_key"] == project_key]:
            if zone["name"].casefold() in existing_zone_names:
                continue
            tower = towers_by_name.get((zone.get("tower_name") or "").casefold())
            _, floor_id = self._resolve_tower_refs(
                tower=tower,
                wing_name=None,
                floor_level=zone.get("floor_level"),
                known_wings=known_wings,
            )
            if not tower or not floor_id:
                continue
            try:
                await self.units_service.create_parking_zone(
                    project_id=project_id,
                    body=CreateParkingZoneRequest(
                        tower_id=str(tower["id"]),
                        floor_id=floor_id,
                        name=zone["name"],
                        slot_from=zone["slot_from"],
                        slot_to=zone["slot_to"],
                    ),
                )
                existing_zone_names.add(zone["name"].casefold())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    WorkbookRowError(
                        sheet="7. Parking zones",
                        row_number=int(zone.get("sheet_row") or 0),
                        code="parking_zone_create_failed",
                        message=str(exc),
                        raw=zone,
                    )
                )
        return errors

    async def _finalize_imported_setup(self, *, project_id: str) -> None:
        """Mark wizard steps done and activate the project, same as the setup APIs."""
        project = await self.projects_service.projects_repo.get_project(
            organization_id=self.user_context.organization_id,
            project_id=project_id,
        )
        if not project:
            return
        completions: dict[str, Any] = {
            ProjectSetupStep.PROJECT_BASICS.value: lambda: self.setup_service.complete_step(
                project_id=project_id,
                step_key=ProjectSetupStep.PROJECT_BASICS.value,
            ),
            ProjectSetupStep.TOWER_BUILDER.value: lambda: (
                self.towers_service.complete_tower_builder(project_id=project_id)
            ),
            ProjectSetupStep.APARTMENT_CONFIG.value: lambda: (
                self.configs_service.complete_config_step(
                    project_id=project_id,
                    config_kind=UnitConfigKind.APARTMENT.value,
                )
            ),
            ProjectSetupStep.COMMERCIAL_CONFIG.value: lambda: (
                self.configs_service.complete_config_step(
                    project_id=project_id,
                    config_kind=UnitConfigKind.COMMERCIAL.value,
                )
            ),
            ProjectSetupStep.PLOT_CONFIG.value: lambda: self.configs_service.complete_config_step(
                project_id=project_id,
                config_kind=UnitConfigKind.PLOT.value,
            ),
            ProjectSetupStep.INVENTORIES.value: lambda: (
                self.inventory_service.complete_inventories(project_id=project_id)
            ),
            ProjectSetupStep.FACILITIES.value: lambda: (
                self.facilities_service.complete_facilities(project_id=project_id)
            ),
            ProjectSetupStep.FLOOR_PLANS.value: lambda: (
                self.units_service.complete_floor_plans(project_id=project_id)
            ),
            ProjectSetupStep.SITE_MAP.value: lambda: (
                self.site_map_service.complete_site_map(project_id=project_id)
            ),
        }
        for step_key in compute_visible_steps(list(project.get("property_types") or [])):
            complete = completions.get(step_key)
            if complete is None:
                continue
            try:
                await complete()
            except ValidationException as exc:
                logger.warning(
                    "projects_import_setup_step_failed project_id=%s step=%s error=%s",
                    project_id,
                    step_key,
                    exc,
                )
        try:
            await self.setup_service.complete_wizard(project_id=project_id)
        except ValidationException as exc:
            logger.warning(
                "projects_import_setup_complete_failed project_id=%s error=%s",
                project_id,
                exc,
            )

    def _tower_body(
        self,
        parsed: ParsedProjectWorkbook,
        tower: dict[str, Any],
        known_wings: set[tuple[str, str]],
    ) -> CreateTowerRequest:
        """Build the same nested create_tower payload the tower create API uses."""
        project_key = tower["project_key"]
        tower_name = tower["name"]
        wings = [
            CreateTowerWingRequest(name=item["name"], code=item.get("code"))
            for item in parsed.wings
            if item["project_key"] == project_key
            and item["tower_name"].casefold() == tower_name.casefold()
        ]
        floors = self._floor_items_for_tower(parsed=parsed, tower=tower, known_wings=known_wings)
        gates = [
            CreateTowerGateBulkItem(
                name=item["name"],
                gate_type=item["gate_type"],
                status=item["status"],
                is_open_24x7=bool(item.get("is_open_24x7")),
                wing_client_key=(
                    item["wing_name"]
                    if item.get("wing_name")
                    and (tower_name.casefold(), item["wing_name"].casefold()) in known_wings
                    else None
                ),
            )
            for item in parsed.gates
            if item["project_key"] == project_key
            and (item.get("tower_name") or "").casefold() == tower_name.casefold()
        ]
        lifts = [
            CreateTowerLiftRequest(
                name=item["name"],
                lift_type=item["lift_type"],
                capacity_persons=item.get("capacity_persons"),
            )
            for item in parsed.lifts
            if item["project_key"] == project_key
            and (item.get("tower_name") or "").casefold() == tower_name.casefold()
        ]
        return CreateTowerRequest(
            name=tower["name"],
            code=tower.get("code"),
            tower_type=tower["tower_type"],
            basement_count=tower.get("basement_count") or 0,
            upper_floor_count=tower.get("upper_floor_count") or 0,
            units_per_floor_default=tower.get("units_per_floor_default"),
            numbering_pattern=tower["numbering_pattern"],
            starting_unit_number=tower.get("starting_unit_number") or 1,
            custom_prefix=tower.get("custom_prefix"),
            has_wings=bool(tower.get("has_wings")),
            latitude=tower.get("latitude"),
            longitude=tower.get("longitude"),
            wings=wings or None,
            gates=gates or None,
            lifts=lifts or None,
            floors=floors or None,
        )

    def _floor_items_for_tower(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        tower: dict[str, Any],
        known_wings: set[tuple[str, str]],
    ) -> list[CreateFloorBulkItem]:
        """Build nested floor payloads for create_tower / create_floor."""
        parking_levels = {
            int(item["level_number"])
            for item in parsed.floors
            if item["project_key"] == tower["project_key"]
            and item["tower_name"].casefold() == tower["name"].casefold()
            and item.get("is_parking")
            and item.get("level_number") is not None
        }
        specs = build_tower_floor_specs(
            basement_count=int(tower.get("basement_count") or 0),
            upper_floor_count=int(tower.get("upper_floor_count") or 0),
            parking_levels=parking_levels,
        )
        floors = [
            CreateFloorBulkItem(
                level_number=int(spec["level_number"]),
                display_name=str(spec["display_name"]),
                is_parking=bool(spec["is_parking"]),
            )
            for spec in specs
        ]
        seen_levels = {int(spec["level_number"]) for spec in specs}
        tower_name = tower["name"]
        for item in parsed.floors:
            if item["project_key"] != tower["project_key"]:
                continue
            if item["tower_name"].casefold() != tower_name.casefold():
                continue
            wing_key = (
                item["wing_name"]
                if item.get("wing_name")
                and (tower_name.casefold(), item["wing_name"].casefold()) in known_wings
                else None
            )
            if wing_key is None and int(item["level_number"]) in seen_levels:
                continue
            floors.append(
                CreateFloorBulkItem(
                    level_number=int(item["level_number"]),
                    display_name=item["display_name"],
                    is_parking=bool(item.get("is_parking")),
                    wing_client_key=wing_key,
                )
            )
            if wing_key is None:
                seen_levels.add(int(item["level_number"]))
        return floors

    def _default_config_id(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        tower: dict[str, Any],
        configs_by_name: dict[str, str],
    ) -> str | None:
        """Pick a unit-type config for generated homes on this tower."""
        tower_types = [
            item["type_name"]
            for item in parsed.units
            if item["project_key"] == tower["project_key"]
            and (item.get("tower_name") or "").casefold() == tower["name"].casefold()
            and item.get("type_name")
        ]
        if tower_types:
            type_name = Counter(tower_types).most_common(1)[0][0]
            config_id = configs_by_name.get(type_name.casefold())
            if config_id:
                return config_id
        for item in parsed.unit_types:
            if item["project_key"] != tower["project_key"]:
                continue
            config_id = configs_by_name.get(str(item["name"]).casefold())
            if config_id:
                return config_id
        return next(iter(configs_by_name.values()), None)

    def _generated_unit_bodies(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        tower: dict[str, Any],
        created_tower: dict[str, Any],
        configs_by_name: dict[str, str],
    ) -> list[CreateUnitRequest]:
        """Homes per floor x floors above ground, using create_units_bulk payloads."""
        units_per_floor = int(tower.get("units_per_floor_default") or 0)
        upper_floor_count = int(tower.get("upper_floor_count") or 0)
        numbering = tower.get("numbering_pattern") or UnitNumberingPattern.FLOOR_UNIT
        if units_per_floor <= 0 or upper_floor_count <= 0:
            return []
        if numbering == UnitNumberingPattern.CUSTOM:
            return []
        config_id = self._default_config_id(
            parsed=parsed, tower=tower, configs_by_name=configs_by_name
        )
        if not config_id:
            return []
        floors_by_level: dict[int, dict[str, Any]] = {}
        for floor in created_tower.get("floors") or []:
            try:
                floors_by_level[int(floor.get("level_number"))] = floor
            except (TypeError, ValueError):
                continue
        starting = int(tower.get("starting_unit_number") or 1)
        tower_id = str(created_tower["id"])
        bodies: list[CreateUnitRequest] = []
        for floor_level in range(1, upper_floor_count + 1):
            floor = floors_by_level.get(floor_level)
            for code in unit_codes_for_floor(
                starting_unit_number=starting,
                floor_level=floor_level,
                units_per_floor=units_per_floor,
                numbering_pattern=numbering,
            ):
                bodies.append(
                    CreateUnitRequest(
                        tower_id=tower_id,
                        floor_id=str(floor["id"]) if floor and floor.get("id") else None,
                        config_id=config_id,
                        code=code,
                        unit_label=code,
                        status=UnitStatus.VACANT,
                        is_parking=False,
                    )
                )
        return bodies

    def _tower_matches(self, item: dict[str, Any], tower: dict[str, Any]) -> bool:
        """True when a workbook row belongs to the given tower."""
        return (
            item.get("project_key") == tower["project_key"]
            and (item.get("tower_name") or "").casefold() == tower["name"].casefold()
        )

    def _wing_id_by_name(self, tower_row: dict[str, Any], wing_name: str | None) -> str | None:
        """Return the created wing id matching a workbook wing name."""
        if not wing_name:
            return None
        for wing in tower_row.get("wings") or []:
            if str(wing.get("name") or "").casefold() == wing_name.casefold():
                return str(wing["id"])
        return None

    async def _sync_existing_tower(
        self,
        *,
        project_id: str,
        tower_row: dict[str, Any],
        parsed: ParsedProjectWorkbook,
        tower: dict[str, Any],
        known_wings: set[tuple[str, str]],
    ) -> dict[str, Any]:
        """Fill missing tower children through the same create_* methods as create_tower."""
        # pylint: disable=too-complex, too-many-branches
        tower_id = str(tower_row["id"])
        wings = list(tower_row.get("wings") or [])
        existing_wing_names = {str(item.get("name") or "").casefold() for item in wings}
        for item in parsed.wings:
            if not self._tower_matches(item, tower):
                continue
            if item["name"].casefold() in existing_wing_names:
                continue
            created = await self.towers_service.create_wing(
                project_id=project_id,
                tower_id=tower_id,
                body=CreateTowerWingRequest(name=item["name"], code=item.get("code")),
            )
            wings.append(created)
            existing_wing_names.add(item["name"].casefold())
        tower_row["wings"] = wings

        gates = list(tower_row.get("gates") or [])
        existing_gate_names = {str(item.get("name") or "").casefold() for item in gates}
        for item in parsed.gates:
            if not self._tower_matches(item, tower):
                continue
            if item["name"].casefold() in existing_gate_names:
                continue
            created = await self.towers_service.create_gate(
                project_id=project_id,
                tower_id=tower_id,
                body=CreateTowerGateRequest(
                    wing_id=self._wing_id_by_name(tower_row, item.get("wing_name")),
                    name=item["name"],
                    gate_type=item["gate_type"],
                    status=item["status"],
                    is_open_24x7=bool(item.get("is_open_24x7")),
                ),
            )
            gates.append(created)
            existing_gate_names.add(item["name"].casefold())
        tower_row["gates"] = gates

        lifts = list(tower_row.get("lifts") or [])
        existing_lift_names = {str(item.get("name") or "").casefold() for item in lifts}
        for item in parsed.lifts:
            if not self._tower_matches(item, tower):
                continue
            if item["name"].casefold() in existing_lift_names:
                continue
            created = await self.towers_service.create_lift(
                project_id=project_id,
                tower_id=tower_id,
                body=CreateTowerLiftRequest(
                    name=item["name"],
                    lift_type=item["lift_type"],
                    capacity_persons=item.get("capacity_persons"),
                ),
            )
            lifts.append(created)
            existing_lift_names.add(item["name"].casefold())
        tower_row["lifts"] = lifts

        existing_by_level: dict[int, dict[str, Any]] = {}
        floors = list(tower_row.get("floors") or [])
        for floor in floors:
            try:
                existing_by_level[int(floor.get("level_number"))] = floor
            except (TypeError, ValueError):
                continue
        for item in self._floor_items_for_tower(
            parsed=parsed, tower=tower, known_wings=known_wings
        ):
            level = int(item.level_number)
            current = existing_by_level.get(level)
            wing_id = self._wing_id_by_name(tower_row, item.wing_client_key)
            if current is None:
                created = await self.towers_service.create_floor(
                    project_id=project_id,
                    tower_id=tower_id,
                    body=CreateFloorRequest(
                        wing_id=wing_id,
                        level_number=level,
                        display_name=item.display_name,
                        is_parking=item.is_parking,
                    ),
                )
                floors.append(created)
                existing_by_level[level] = created
                continue
            if str(current.get("display_name") or "") == item.display_name:
                continue
            updated = await self.towers_service.update_floor(
                project_id=project_id,
                tower_id=tower_id,
                floor_id=str(current["id"]),
                patch={"display_name": item.display_name},
            )
            for index, floor in enumerate(floors):
                if str(floor.get("id")) == str(current["id"]):
                    floors[index] = updated
                    break
            existing_by_level[level] = updated
        tower_row["floors"] = floors
        return tower_row

    async def _existing_unit_codes(self, *, project_id: str) -> set[str]:
        """Collect unit codes already stored for the project."""
        codes: set[str] = set()
        page = 1
        page_size = 100
        while True:
            listed = await self.units_service.list_units(
                project_id=project_id, page=page, page_size=page_size
            )
            items = listed.get("items") or []
            codes.update(str(row.get("code") or "").casefold() for row in items)
            total = int(listed.get("total") or 0)
            if len(items) < page_size or len(codes) >= total:
                return codes
            page += 1

    async def _existing_facility_names(self, *, project_id: str) -> set[str]:
        """Collect amenity names already stored for the project."""
        listed = await self.facilities_service.list_facilities(
            project_id=project_id, page=1, page_size=100
        )
        return {
            str(row.get("name") or "").casefold()
            for row in listed.get("items") or listed.get("data") or []
        }

    async def _apply_amenities(
        self,
        *,
        parsed: ParsedProjectWorkbook,
        project_key: str,
        project_id: str,
        towers_by_name: dict[str, dict[str, Any]],
    ) -> list[WorkbookRowError]:
        """Create amenities through create_facility, skipping names that already exist."""
        errors: list[WorkbookRowError] = []
        existing_names = await self._existing_facility_names(project_id=project_id)
        for amenity in [item for item in parsed.amenities if item["project_key"] == project_key]:
            if amenity["name"].casefold() in existing_names:
                continue
            if amenity["facility_type"] == FacilityType.PARKING and (
                not amenity.get("parking_slots")
                or not amenity.get("parking_user_type")
                or not amenity.get("parking_vehicle_category")
            ):
                continue
            tower = towers_by_name.get((amenity.get("tower_name") or "").casefold())
            try:
                await self.facilities_service.create_facility(
                    project_id=project_id,
                    body=CreateFacilityRequest(
                        name=amenity["name"],
                        facility_type=amenity["facility_type"],
                        facility_subtype=(
                            ParkingFacilitySubtype.COVERED.value
                            if amenity["facility_type"] == FacilityType.PARKING
                            else None
                        ),
                        location_type=amenity["location_type"],
                        tower_id=str(tower["id"]) if tower else None,
                        floor_level=(
                            str(amenity["floor_level"])
                            if amenity.get("floor_level") is not None
                            else None
                        ),
                        wing=amenity.get("wing"),
                        capacity_persons=amenity.get("capacity_persons"),
                        parking_slots=amenity.get("parking_slots"),
                        parking_user_type=amenity.get("parking_user_type"),
                        parking_vehicle_category=amenity.get("parking_vehicle_category"),
                        latitude=amenity.get("latitude"),
                        longitude=amenity.get("longitude"),
                    ),
                )
                existing_names.add(amenity["name"].casefold())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(
                    WorkbookRowError(
                        sheet="6. Amenities",
                        row_number=int(amenity.get("sheet_row") or 0),
                        code="amenity_create_failed",
                        message=str(exc),
                        raw=amenity,
                    )
                )
        return errors

    @staticmethod
    def _skip_unresolved_parking_zones(
        *,
        parsed: ParsedProjectWorkbook,
        project_key: str,
        towers_by_name: dict[str, dict[str, Any]],
        known_wings: set[tuple[str, str]],
    ) -> list[WorkbookRowError]:
        """Record parking zones whose tower could not be resolved, without failing the job."""
        del known_wings
        skipped: list[WorkbookRowError] = []
        for zone in [item for item in parsed.parking_zones if item["project_key"] == project_key]:
            tower = towers_by_name.get((zone.get("tower_name") or "").casefold())
            if tower:
                continue
            skipped.append(
                WorkbookRowError(
                    sheet="7. Parking zones",
                    row_number=int(zone.get("sheet_row") or 0),
                    code="parking_zone_skipped",
                    message="Parking zone skipped; tower or floor could not be resolved.",
                    raw=zone,
                )
            )
        parsed.skipped.extend(skipped)
        return []

    @staticmethod
    def _resolve_tower_refs(
        *,
        tower: dict[str, Any] | None,
        wing_name: str | None,
        floor_level: int | None,
        known_wings: set[tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        """Resolve wing and floor ids from a created tower for a workbook row."""
        if not tower:
            return None, None
        tower_name = str(tower.get("name") or "").casefold()
        wing_id = None
        if wing_name and (tower_name, wing_name.casefold()) in known_wings:
            for wing in tower.get("wings") or []:
                if str(wing.get("name") or "").casefold() == wing_name.casefold():
                    wing_id = str(wing["id"])
                    break
        floor_id = None
        if floor_level is not None:
            for floor in tower.get("floors") or []:
                if int(floor.get("level_number")) != int(floor_level):
                    continue
                floor_wing = floor.get("wing_id")
                if wing_id and floor_wing and str(floor_wing) != wing_id:
                    continue
                floor_id = str(floor["id"])
                break
        return wing_id, floor_id

    async def _finish_job(
        self,
        *,
        job: dict[str, Any],
        status: ContactsImportJobStatus,
        parsed: ParsedProjectWorkbook,
        extra_errors: list[WorkbookRowError],
    ) -> None:
        """Persist row-ledger errors and mark the import job finished."""
        errors = list(parsed.errors) + list(extra_errors)
        success_rows = (
            len(parsed.projects)
            + len(parsed.towers)
            + len(parsed.wings)
            + len(parsed.unit_types)
            + len(parsed.floors)
            + len(parsed.units)
            + len(parsed.amenities)
            + len(parsed.parking_zones)
            + len(parsed.gates)
            + len(parsed.lifts)
        )
        error_rows = len(errors)
        total_rows = success_rows + error_rows + len(parsed.skipped)
        await self.jobs_repo.increment_counters(
            job_id=str(job["job_id"]),
            organization_id=str(job["organization_id"]),
            total_rows_delta=total_rows,
            processed_rows_delta=total_rows,
            success_rows_delta=max(success_rows - error_rows, 0),
            error_rows_delta=error_rows,
        )
        internal_id = str(job.get("id") or "")
        if internal_id and errors:
            claim_rows = [
                (_ledger_row(item.sheet, item.row_number), _json_safe(item.raw)) for item in errors
            ]
            await self.rows_repo.claim_rows_processing(
                organization_id=str(job["organization_id"]),
                job_id=internal_id,
                rows=claim_rows,
            )
            await self.rows_repo.mark_errors_bulk(
                organization_id=str(job["organization_id"]),
                job_id=internal_id,
                errors=[
                    (
                        _ledger_row(item.sheet, item.row_number),
                        item.code,
                        item.message,
                        _json_safe(item.raw),
                    )
                    for item in errors
                ],
            )
        await self.jobs_repo.set_status_and_timestamps(
            job_id=str(job["job_id"]),
            organization_id=str(job["organization_id"]),
            status=status.value,
            finished_at=datetime.now(UTC),
        )

    async def _download_workbook(self, *, file_url: str) -> bytes:
        """Download the workbook bytes from the presigned file URL."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(file_url)
            response.raise_for_status()
            payload = response.content
        if not payload:
            raise ValidationException(
                message_key="projects_imports.errors.empty_file",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if len(payload) > MAX_WORKBOOK_BYTES:
            raise ValidationException(
                message_key="projects_imports.errors.file_too_large",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return payload

    @staticmethod
    def _generate_job_id() -> str:
        """Return a unique import job identifier."""
        return f"imp_{uuid.uuid4().hex}"

    @staticmethod
    def _validate_file_url(file_url: str) -> None:
        """Reject private, local, or otherwise unsafe workbook URLs."""
        if not file_url or len(file_url) > 4096:
            raise ValidationException(
                message_key="projects_imports.errors.invalid_file_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        parsed = urlparse(file_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValidationException(
                message_key="projects_imports.errors.invalid_file_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        host = parsed.hostname or ""
        if host in {"localhost"} or host.endswith(".localhost"):
            raise ValidationException(
                message_key="projects_imports.errors.invalid_file_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        try:
            ip_addr = ipaddress.ip_address(host)
        except ValueError:
            return
        if any(
            (
                ip_addr.is_private,
                ip_addr.is_loopback,
                ip_addr.is_link_local,
                ip_addr.is_multicast,
                ip_addr.is_reserved,
                ip_addr.is_unspecified,
            )
        ):
            raise ValidationException(
                message_key="projects_imports.errors.invalid_file_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )


def _ledger_row(sheet: str, row_number: int) -> int:
    """Map a sheet row onto a unique import-job ledger row number."""
    return SHEET_ROW_OFFSETS.get(sheet, 900_000) + int(row_number or 0)


def _json_safe(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a workbook row into JSON-serializable error payload data."""
    if not raw:
        return None
    try:
        return json.loads(json.dumps(raw, default=_json_default))
    except TypeError:
        return {str(key): str(value) for key, value in raw.items()}


def _json_default(value: Any) -> Any:
    """JSON fallback for dates, enums, and other non-primitive cell values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def schedule_projects_import_job(
    *,
    job_id: str,
    organization_id: str,
    user_context: UserContext,
) -> None:
    """Run workbook processing after the HTTP response has already been sent."""
    task = asyncio.create_task(
        _run_projects_import_job(
            job_id=job_id,
            organization_id=organization_id,
            user_context=user_context,
        )
    )
    _BACKGROUND_IMPORTS.add(task)
    task.add_done_callback(_BACKGROUND_IMPORTS.discard)


async def _run_projects_import_job(
    *,
    job_id: str,
    organization_id: str,
    user_context: UserContext,
) -> None:
    """Acquire a fresh DB connection and process a queued projects import job."""
    pool = await get_pool()
    async with AcquireConnection(pool) as conn:
        service = ProjectsImportService(db_connection=conn, user_context=user_context)
        try:
            await service.process_job(job_id=job_id, organization_id=organization_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Projects import background job %s failed", job_id)
            await service.jobs_repo.set_status_and_timestamps(
                job_id=job_id,
                organization_id=organization_id,
                status=ContactsImportJobStatus.FAILED.value,
                finished_at=datetime.now(UTC),
            )
