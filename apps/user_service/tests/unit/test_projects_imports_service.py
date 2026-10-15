"""Unit tests for ProjectsImportService."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    ContactsImportJobStatus,
    ContactsImportType,
    GateStatus,
    GateType,
    LiftType,
    MeasurementUnit,
    PropertyType,
    TowerType,
    UnitNumberingPattern,
    UnitStatus,
)
from apps.user_service.app.services.project_workbook_parser import ParsedProjectWorkbook
from apps.user_service.app.services.projects_imports_service import (
    ProjectsImportService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
ADMIN_ID = "770e8400-e29b-41d4-a716-446655440002"
FILE_URL = "https://cdn.example.com/project.xlsx"


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


class _FakeConn:
    def transaction(self):
        return _FakeTransaction()


class _FakeJobsRepo:
    last_instance: "_FakeJobsRepo | None" = None

    def __init__(self, *, db_connection) -> None:
        del db_connection
        self.create_job_calls: list[dict[str, Any]] = []
        self._job = {
            "id": "11111111-1111-1111-1111-111111111111",
            "job_key": "imp_projects",
            "job_id": "imp_projects",
            "organization_id": ORG_ID,
            "import_type": ContactsImportType.PROJECTS.value,
            "status": ContactsImportJobStatus.QUEUED.value,
            "created_at": "2026-10-13T00:00:00+00:00",
            "total_rows": 0,
            "processed_rows": 0,
            "success_rows": 0,
            "error_rows": 0,
        }
        _FakeJobsRepo.last_instance = self

    async def create_job(self, **kwargs) -> dict[str, Any]:
        self.create_job_calls.append(kwargs)
        self._job = {
            **self._job,
            **kwargs,
            "job_id": kwargs["job_id"],
            "job_key": kwargs["job_id"],
        }
        return dict(self._job)

    async def get_job(self, *, job_id: str, organization_id: str) -> dict[str, Any]:
        del organization_id
        return {**self._job, "job_id": job_id}

    async def set_status_and_timestamps(self, **kwargs) -> None:
        if "status" in kwargs:
            self._job["status"] = kwargs["status"]

    async def increment_counters(self, **kwargs) -> None:
        del kwargs


class _FakeRowsRepo:
    def __init__(self, *, db_connection) -> None:
        del db_connection

    async def claim_rows_processing(self, **kwargs) -> dict[int, str]:
        del kwargs
        return {}

    async def mark_errors_bulk(self, **kwargs) -> None:
        del kwargs

    async def list_error_rows(self, **kwargs):
        del kwargs
        return ([], 0)


def _service() -> ProjectsImportService:
    return ProjectsImportService(
        db_connection=_FakeConn(),
        user_context=UserContext(
            user_id=ADMIN_ID,
            email="admin@example.com",
            organization_id=ORG_ID,
            user_type="admin",
        ),
    )


def _patch_repos(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_imports_service.ImportJobsRepository",
        _FakeJobsRepo,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_imports_service.ImportJobRowsRepository",
        _FakeRowsRepo,
    )
    for name in (
        "ProjectsService",
        "TowersService",
        "UnitConfigsService",
        "UnitsService",
        "FacilitiesService",
        "InventoryService",
        "SiteMapService",
        "ProjectSetupService",
    ):
        monkeypatch.setattr(
            f"apps.user_service.app.services.projects_imports_service.{name}",
            lambda *args, **kwargs: MagicMock(),
        )


@pytest.mark.asyncio
async def test_create_job_stores_community_admin_and_projects_type(monkeypatch):
    """Import job body community admin is stored; import_type is projects."""
    _patch_repos(monkeypatch)
    service = _service()
    service._apply_workbook = AsyncMock(return_value=[])

    job = await service.create_job(
        organization_id=ORG_ID,
        file_url=FILE_URL,
        file_type="xlsx",
        schema_version=1,
        community_admin_user_id=ADMIN_ID,
    )

    created = _FakeJobsRepo.last_instance.create_job_calls[0]
    assert created["import_type"] == ContactsImportType.PROJECTS.value
    assert created["options"]["community_admin_user_id"] == ADMIN_ID
    service._apply_workbook.assert_not_awaited()
    assert job["status"] == ContactsImportJobStatus.QUEUED.value


@pytest.mark.asyncio
async def test_process_job_applies_workbook(monkeypatch):
    """Background processing downloads the workbook and creates inventory."""
    _patch_repos(monkeypatch)
    parsed = ParsedProjectWorkbook(
        projects=[{"name_key": "happy trails", "sheet_row": 5, "name": "Happy Trails"}]
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.projects_imports_service.parse_project_workbook",
        lambda _payload: parsed,
    )
    service = _service()
    service._download_workbook = AsyncMock(return_value=b"xlsx")
    service._apply_workbook = AsyncMock(return_value=[])
    created = await service.create_job(
        organization_id=ORG_ID,
        file_url=FILE_URL,
        file_type="xlsx",
        schema_version=1,
        community_admin_user_id=ADMIN_ID,
    )

    job = await service.process_job(job_id=str(created["job_id"]), organization_id=ORG_ID)

    service._apply_workbook.assert_awaited_once()
    assert job["status"] == ContactsImportJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_create_job_rejects_private_file_url():
    """SSRF-ish file URLs are rejected before a job is created."""
    service = _service()
    with pytest.raises(ValidationException):
        await service.create_job(
            organization_id=ORG_ID,
            file_url="http://localhost/secret.xlsx",
            file_type="xlsx",
            schema_version=1,
            community_admin_user_id=ADMIN_ID,
        )


def _project_item() -> dict[str, Any]:
    return {
        "sheet_row": 5,
        "name": "ATS Home Kraft Happy Trails",
        "name_key": "ats home kraft happy trails",
        "developer_name": "ATS Homefraft",
        "gstin": None,
        "address_line_1": "Plot GH-002A",
        "pin_code": "203207",
        "city": "Noida",
        "state": "UP",
        "country": "India",
        "property_types": [PropertyType.RESIDENTIAL],
        "primary_measurement_unit": MeasurementUnit.SQ_FT,
        "units_count": 0,
    }


@pytest.mark.asyncio
async def test_get_or_create_project_reuses_existing_name():
    """A second import with the same project name does not insert another row."""
    service = _service()
    service.projects_service.projects_repo.get_project_by_name = AsyncMock(
        return_value={"id": "existing-id", "name": "ATS Home Kraft Happy Trails"}
    )
    service.projects_service.create_project = AsyncMock()

    project_id, error = await service._get_or_create_project(
        item=_project_item(),
        community_admin_user_id=ADMIN_ID,
    )

    assert project_id == "existing-id"
    assert error is None
    service.projects_service.create_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_project_recovers_after_create_error():
    """If create inserts then raises (workflow key), reuse the saved project."""
    service = _service()
    service.projects_service.projects_repo.get_project_by_name = AsyncMock(
        side_effect=[None, {"id": "inserted-id", "name": "ATS Home Kraft Happy Trails"}]
    )
    service.projects_service.create_project = AsyncMock(
        side_effect=RuntimeError("All connection attempts failed")
    )

    project_id, error = await service._get_or_create_project(
        item=_project_item(),
        community_admin_user_id=ADMIN_ID,
    )

    assert project_id == "inserted-id"
    assert error is None


@pytest.mark.asyncio
async def test_create_job_requires_xlsx():
    """CSV contacts files are not accepted as project workbooks."""
    service = _service()
    with pytest.raises(ValidationException):
        await service.create_job(
            organization_id=ORG_ID,
            file_url=FILE_URL,
            file_type="csv",
            schema_version=1,
            community_admin_user_id=ADMIN_ID,
        )


def test_tower_body_passes_nested_floors_into_create_tower():
    """Import builds the same nested floors payload create_tower already uses."""
    service = _service()
    parsed = ParsedProjectWorkbook(
        floors=[
            {
                "project_key": "happytrails",
                "tower_name": "Tower 1",
                "level_number": -1,
                "display_name": "Basement 1",
                "is_parking": True,
                "wing_name": None,
            }
        ]
    )
    body = service._tower_body(
        parsed,
        {
            "project_key": "happytrails",
            "name": "Tower 1",
            "code": "tower-1",
            "tower_type": TowerType.RESIDENTIAL,
            "basement_count": 1,
            "upper_floor_count": 27,
            "units_per_floor_default": 2,
            "numbering_pattern": UnitNumberingPattern.FLOOR_UNIT,
            "starting_unit_number": 1011,
            "has_wings": False,
        },
        set(),
    )
    by_level = {item.level_number: item for item in body.floors or []}
    assert by_level[-1].is_parking is True
    assert by_level[1].display_name == "Floor 1"
    assert by_level[22].display_name == "Floor 22"
    assert by_level[27].display_name == "Floor 27"
    assert body.units_per_floor_default == 2


def test_generated_units_use_homes_per_floor_for_create_units_bulk():
    """Usual homes per floor becomes create_units_bulk payloads after tower create."""
    service = _service()
    parsed = ParsedProjectWorkbook(
        units=[
            {
                "project_key": "happytrails",
                "tower_name": "Tower 1",
                "type_name": "3+Study",
                "code": "1221",
            }
        ]
    )
    bodies = service._generated_unit_bodies(
        parsed=parsed,
        tower={
            "project_key": "happytrails",
            "name": "Tower 1",
            "units_per_floor_default": 2,
            "upper_floor_count": 27,
            "numbering_pattern": UnitNumberingPattern.FLOOR_UNIT,
            "starting_unit_number": 1011,
        },
        created_tower={
            "id": "tower-id",
            "floors": [
                {"id": "f1", "level_number": 1},
                {"id": "f22", "level_number": 22},
            ],
        },
        configs_by_name={"3+study": "config-id"},
    )
    assert len(bodies) == 54
    assert [item.code for item in bodies if item.floor_id == "f1"] == ["1011", "1012"]
    assert [item.code for item in bodies if item.floor_id == "f22"] == ["1221", "1222"]
    assert bodies[0].status == UnitStatus.VACANT
    assert bodies[0].tower_id == "tower-id"
    assert bodies[0].config_id == "config-id"


@pytest.mark.asyncio
async def test_sync_existing_tower_calls_create_methods_for_all_children():
    """Re-import uses create_wing/gate/lift/floor, the same methods as create_tower."""
    service = _service()
    service.towers_service.create_wing = AsyncMock(return_value={"id": "wing-1", "name": "East"})
    service.towers_service.create_gate = AsyncMock(
        return_value={"id": "gate-1", "name": "Main Gate"}
    )
    service.towers_service.create_lift = AsyncMock(return_value={"id": "lift-1", "name": "Lift 1"})
    service.towers_service.create_floor = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": f"floor-{kwargs['body'].level_number}",
            "level_number": kwargs["body"].level_number,
            "display_name": kwargs["body"].display_name,
        }
    )
    service.towers_service.update_floor = AsyncMock()

    parsed = ParsedProjectWorkbook(
        wings=[
            {
                "project_key": "happytrails",
                "tower_name": "Tower 1",
                "name": "East",
                "code": "east",
            }
        ],
        gates=[
            {
                "project_key": "happytrails",
                "tower_name": "Tower 1",
                "name": "Main Gate",
                "gate_type": GateType.ENTRY,
                "status": GateStatus.ACTIVE,
                "is_open_24x7": True,
                "wing_name": "East",
            }
        ],
        lifts=[
            {
                "project_key": "happytrails",
                "tower_name": "Tower 1",
                "name": "Lift 1",
                "lift_type": LiftType.PASSENGER,
                "capacity_persons": 8,
            }
        ],
    )
    result = await service._sync_existing_tower(
        project_id="proj-1",
        tower_row={
            "id": "tower-id",
            "name": "Tower 1",
            "wings": [],
            "gates": [],
            "lifts": [],
            "floors": [],
        },
        parsed=parsed,
        tower={
            "project_key": "happytrails",
            "name": "Tower 1",
            "basement_count": 1,
            "upper_floor_count": 2,
        },
        known_wings={("tower 1", "east")},
    )

    service.towers_service.create_wing.assert_awaited_once()
    service.towers_service.create_gate.assert_awaited_once()
    service.towers_service.create_lift.assert_awaited_once()
    assert service.towers_service.create_floor.await_count == 3
    assert [item["name"] for item in result["wings"]] == ["East"]
    assert [item["name"] for item in result["gates"]] == ["Main Gate"]
    assert [item["name"] for item in result["lifts"]] == ["Lift 1"]
    assert {item["level_number"] for item in result["floors"]} == {-1, 1, 2}


@pytest.mark.asyncio
async def test_finalize_imported_setup_completes_wizard_like_setup_apis():
    """Import marks applicable wizard steps done and sets the project active."""
    service = _service()
    service.projects_service.projects_repo.get_project = AsyncMock(
        return_value={
            "id": "proj-1",
            "property_types": [
                PropertyType.RESIDENTIAL.value,
                PropertyType.COMMERCIAL.value,
            ],
        }
    )
    service.setup_service.complete_step = AsyncMock()
    service.setup_service.complete_wizard = AsyncMock()
    service.towers_service.complete_tower_builder = AsyncMock()
    service.configs_service.complete_config_step = AsyncMock()
    service.inventory_service.complete_inventories = AsyncMock()
    service.facilities_service.complete_facilities = AsyncMock()
    service.units_service.complete_floor_plans = AsyncMock()
    service.site_map_service.complete_site_map = AsyncMock()

    await service._finalize_imported_setup(project_id="proj-1")

    service.setup_service.complete_step.assert_awaited_once_with(
        project_id="proj-1",
        step_key="project_basics",
    )
    service.towers_service.complete_tower_builder.assert_awaited_once_with(project_id="proj-1")
    assert service.configs_service.complete_config_step.await_count == 2
    service.inventory_service.complete_inventories.assert_awaited_once_with(project_id="proj-1")
    service.facilities_service.complete_facilities.assert_awaited_once_with(project_id="proj-1")
    service.units_service.complete_floor_plans.assert_awaited_once_with(project_id="proj-1")
    service.site_map_service.complete_site_map.assert_awaited_once_with(project_id="proj-1")
    service.setup_service.complete_wizard.assert_awaited_once_with(project_id="proj-1")
    service.configs_service.complete_config_step.assert_any_await(
        project_id="proj-1",
        config_kind="apartment",
    )
    service.configs_service.complete_config_step.assert_any_await(
        project_id="proj-1",
        config_kind="commercial",
    )
