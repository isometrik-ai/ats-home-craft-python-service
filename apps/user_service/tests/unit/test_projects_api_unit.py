"""Unit tests for project setup API route handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.requests import Request

from apps.user_service.app.api import projects as projects_api
from apps.user_service.app.api.projects import (
    add_config_media,
    add_project_media,
    add_unit_document,
    assign_project_member,
    bulk_create_units,
    complete_project_setup,
    complete_setup_step,
    create_facility,
    create_floor,
    create_parking_zone,
    create_plot_item,
    create_project,
    create_project_role,
    create_site_map_overlays,
    create_tower,
    create_tower_gate,
    create_tower_lift,
    create_tower_wing,
    create_unit,
    create_unit_config,
    delete_config_media,
    delete_facility,
    delete_floor,
    delete_parking_zone,
    delete_plot_item,
    delete_project,
    delete_project_media,
    delete_project_role,
    delete_project_vehicle,
    delete_site_map_overlay,
    delete_tower,
    delete_tower_gate,
    delete_tower_lift,
    delete_tower_wing,
    delete_unit,
    delete_unit_config,
    delete_unit_document,
    export_project_vehicle_requests,
    get_facility_list_query,
    get_inventory_summary,
    get_my_project_permissions,
    get_project_details,
    get_project_role_detail,
    get_project_status,
    get_tower_detail,
    get_unit_detail,
    get_units_registry_summary,
    list_assignable_project_role_permissions,
    list_config_media,
    list_facilities,
    list_facility_parking_slots,
    list_floor_inventory,
    list_floors,
    list_my_projects,
    list_plot_items,
    list_project_media,
    list_project_members,
    list_project_roles,
    list_project_vehicle_requests,
    list_parking_zones,
    list_site_map_overlays,
    list_tower_gates,
    list_tower_lifts,
    list_tower_wings,
    list_towers,
    list_unit_configs,
    list_unit_documents,
    list_unit_passes,
    list_units,
    reassign_unit_owner,
    remove_project_member,
    review_project_vehicle_request,
    unassign_unit_owner,
    update_facility,
    update_project,
    update_project_location,
    update_project_member,
    update_project_role,
    update_tower,
    update_unit,
    update_unit_config,
    upsert_floor_inventory,
    list_projects,
)
from apps.user_service.app.schemas.contact_onboarding import (
    DeleteProjectVehicleRequest,
    ReviewVehicleRequest,
    VehicleRequestsExportQuery,
)
from apps.user_service.app.schemas.enums import (
    ConfigMediaKind,
    ContactUnitDocumentType,
    FacilityLocationType,
    FacilityType,
    MeasurementUnit,
    ProjectMediaKind,
    PropertyProjectStatus,
    PropertyType,
    TowerType,
    UnitConfigKind,
    UnitStatus,
    VehicleStatus,
)
from apps.user_service.app.schemas.passes import AdminUnitPassListQuery
from apps.user_service.app.schemas.project_inventory import (
    BulkCreateUnitsRequest,
    ConfigMediaRequest,
    CreateFacilityRequest,
    CreateParkingZoneRequest,
    CreatePlotConfigItemRequest,
    CreateSiteMapOverlaysRequest,
    CreateUnitConfigRequest,
    CreateUnitDocumentRequest,
    CreateUnitRequest,
    FacilityListQuery,
    ListProjectUnitsFilterQuery,
    ListProjectUnitsQuery,
    ReassignUnitOwnerRequest,
    UpdateFacilityRequest,
    UpdateProjectLocationRequest,
    UpdateUnitConfigRequest,
    UpdateUnitRequest,
    UpsertFloorInventoryRequest,
    FloorInventoryItem,
)
from apps.user_service.app.schemas.project_members import (
    AssignProjectMemberRequest,
    ListProjectMembersQuery,
    UpdateProjectMemberRequest,
)
from apps.user_service.app.schemas.project_roles import (
    CreateProjectRoleRequest,
    ProjectMyPermissionsResponse,
    ProjectRoleDetailItem,
    ProjectRoleItem,
    UpdateProjectRoleRequest,
)
from apps.user_service.app.schemas.project_setup import (
    CompleteStepRequest,
    CreateFloorRequest,
    CreateProjectRequest,
    CreateTowerGateRequest,
    CreateTowerLiftRequest,
    CreateTowerRequest,
    CreateTowerWingRequest,
    ProjectMediaRequest,
    UpdateProjectRequest,
    UpdateTowerRequest,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
TOWER_ID = "22222222-2222-2222-2222-222222222222"
UNIT_ID = "33333333-3333-3333-3333-333333333333"
ROLE_ID = "44444444-4444-4444-4444-444444444444"
MEMBER_ID = "55555555-5555-5555-5555-555555555555"
FACILITY_ID = "66666666-6666-6666-6666-666666666666"
VEHICLE_ID = "77777777-7777-7777-7777-777777777777"
ADMIN_USER_ID = "88888888-8888-8888-8888-888888888888"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/projects",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


def _project_summary(**overrides) -> dict:
    base = {
        "id": PROJECT_ID,
        "organization_id": "org-1",
        "code": "sunrise",
        "name": "Sunrise Towers",
        "developer_name": "Dev Co",
        "city": "Mumbai",
        "state": "MH",
        "status": "draft",
        "property_types": ["residential"],
        "primary_measurement_unit": "sq_ft",
        "units_count": 0,
        "setup_current_step": "basics",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _project_details(**overrides) -> dict:
    base = {
        "id": PROJECT_ID,
        "organization_id": "org-1",
        "code": "sunrise",
        "name": "Sunrise Towers",
        "developer_name": "Dev Co",
        "community_admin_user_id": ADMIN_USER_ID,
        "gstin": "22AAAAA0000A1Z5",
        "address_line_1": "Line 1",
        "pin_code": "400001",
        "city": "Mumbai",
        "state": "MH",
        "country": "IN",
        "property_types": ["residential"],
        "primary_measurement_unit": "sq_ft",
        "status": "draft",
        "setup_current_step": "basics",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _tower_detail(**overrides) -> dict:
    base = {
        "id": TOWER_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "name": "Tower A",
        "code": "A",
        "tower_type": "residential",
        "numbering_pattern": "floor_unit",
    }
    base.update(overrides)
    return base


def _unit_list_item(**overrides) -> dict:
    base = {
        "id": UNIT_ID,
        "code": "A-101",
        "status": UnitStatus.VACANT.value,
        "is_sold": False,
    }
    base.update(overrides)
    return base


def _unit_detail(**overrides) -> dict:
    base = {
        "id": UNIT_ID,
        "project_id": PROJECT_ID,
        "code": "A-101",
        "status": UnitStatus.VACANT.value,
        "occupancy_label": "Vacant",
        "is_sold": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _inventory_summary_payload() -> dict:
    header = {
        "buildings": 1,
        "apartments": 10,
        "commercial": 0,
        "plots": 0,
        "sold_count": 2,
        "unsold_count": 8,
        "sold_percent": 20,
    }
    return {
        "project_id": PROJECT_ID,
        "header": header,
        "buildings": [],
        "units": [],
        "floors": {},
        "plot_configs": [],
    }


def _role_item(**overrides) -> ProjectRoleItem:
    data = {
        "id": ROLE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "slug": "security",
        "name": "Security",
    }
    data.update(overrides)
    return ProjectRoleItem(**data)


def _role_detail(**overrides) -> ProjectRoleDetailItem:
    data = {
        "id": ROLE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "slug": "custom_role",
        "name": "Custom",
        "permission_ids": [],
        "permissions": [],
    }
    data.update(overrides)
    return ProjectRoleDetailItem(**data)


def _create_project_body() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="Sunrise Towers",
        developer_name="Dev Co",
        community_admin_user_id=ADMIN_USER_ID,
        gstin="22AAAAA0000A1Z5",
        address_line_1="Line 1",
        pin_code="400001",
        city="Mumbai",
        state="MH",
        country="IN",
        property_types=[PropertyType.RESIDENTIAL],
        primary_measurement_unit=MeasurementUnit.SQ_FT,
    )


def test_get_facility_list_query_parses_filters():
    query = get_facility_list_query(
        facility_types=["sports", "recreation"],
        status=None,
        search="pool",
        page=2,
        page_size=10,
        is_bookable=True,
    )
    assert query.page == 2
    assert query.search == "pool"
    assert query.is_bookable is True


def test_project_rbac_openapi_helpers():
    ok = projects_api._project_rbac_ok_response(dict, "ok")
    created = projects_api._project_rbac_created_response(dict, "created")
    assert 200 in ok
    assert 201 in created


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects.ensure_staff_project_access", new_callable=AsyncMock)
async def test_staff_project_access_wrapper(mock_ensure):
    mock_ensure.return_value = _user_context()
    ctx = await projects_api._staff_project_access(
        request=_request(),
        current_user={"sub": "staff-1"},
        db_connection=MagicMock(),
        project_id=PROJECT_ID,
        permission_codes="view",
    )
    assert ctx.organization_id == "org-1"
    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.projects.ensure_project_staff_management_access_for_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.projects.extract_user_context", new_callable=AsyncMock)
async def test_project_staff_management_access_wrapper(mock_extract, mock_mgmt):
    mock_extract.return_value = _user_context()
    mock_mgmt.return_value = _user_context()
    ctx = await projects_api._project_staff_management_access(
        request=_request(),
        current_user={"sub": "staff-1"},
        db_connection=MagicMock(),
        project_id=PROJECT_ID,
    )
    assert ctx.user_id == "staff-1"
    mock_mgmt.assert_awaited_once()


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects.check_permissions", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectsService")
async def test_create_project(mock_service_cls, mock_perms):
    mock_perms.return_value = _user_context()
    mock_service_cls.return_value.create_project = AsyncMock(
        return_value={
            "project_id": PROJECT_ID,
            "new_data": _project_details(),
            "old_data": None,
        }
    )
    response = await create_project(
        request=_request(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
        body=_create_project_body(),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects.check_any_permissions", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectsService")
async def test_list_projects_empty_and_populated(mock_service_cls, mock_perms):
    mock_perms.return_value = _user_context()
    service = mock_service_cls.return_value
    service.list_projects = AsyncMock(return_value={"items": [], "total": 0})
    empty = await list_projects(
        request=_request(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )
    assert empty.status_code == 200

    service.list_projects = AsyncMock(
        return_value={"items": [_project_summary()], "total": 1}
    )
    populated = await list_projects(
        request=_request(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
        search="sun",
        status=PropertyProjectStatus.ONBOARDING,
        property_type=PropertyType.RESIDENTIAL,
        page=1,
        page_size=20,
    )
    assert populated.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects.extract_user_context", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectsService")
async def test_list_my_projects(mock_service_cls, mock_extract):
    mock_extract.return_value = _user_context()
    service = mock_service_cls.return_value
    service.list_my_projects = AsyncMock(return_value={"items": [], "total": 0})
    empty = await list_my_projects(
        request=_request(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )
    assert empty.status_code == 200

    my_row = {**_project_summary(), "project_role_id": ROLE_ID, "role_slug": "admin"}
    service.list_my_projects = AsyncMock(return_value={"items": [my_row], "total": 1})
    populated = await list_my_projects(
        request=_request(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
        search=None,
        status=None,
        property_type=None,
        page=1,
        page_size=20,
    )
    assert populated.status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectSetupService")
@patch("apps.user_service.app.api.projects.ProjectsService")
async def test_project_crud_status_and_media(
    mock_projects_cls, mock_setup_cls, mock_access
):
    mock_access.return_value = _user_context()
    setup = mock_setup_cls.return_value
    setup.get_status = AsyncMock(
        return_value={
            "project_id": PROJECT_ID,
            "status": "draft",
            "setup_current_step": "basics",
            "is_completed": False,
            "steps": [],
        }
    )
    setup.complete_step = AsyncMock(return_value={"step_key": "basics"})
    setup.complete_wizard = AsyncMock(return_value={"status": "active"})

    projects = mock_projects_cls.return_value
    projects.get_project_details = AsyncMock(return_value=_project_details())
    projects.update_project = AsyncMock(
        return_value={"new_data": _project_details(name="Renamed"), "old_data": {}}
    )
    projects.delete_project = AsyncMock(return_value={"old_data": {}, "new_data": None})
    projects.add_media = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "project_id": PROJECT_ID,
            "kind": "photo",
            "path": "/p.jpg",
            "mime": "image/jpeg",
            "size_bytes": 1,
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    projects.list_media = AsyncMock(return_value=[])
    projects.remove_media = AsyncMock(return_value={"old_data": {}})

    assert (
        await get_project_status(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_project_details(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await update_project(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
            body=UpdateProjectRequest(name="Renamed"),
        )
    ).status_code == 200

    assert (
        await delete_project(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await complete_setup_step(
            request=_request(),
            project_id=PROJECT_ID,
            step_key="basics",
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
            body=CompleteStepRequest(),
        )
    ).status_code == 200

    assert (
        await complete_project_setup(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    media_body = ProjectMediaRequest(
        kind=ProjectMediaKind.COVER_IMAGE,
        path="/p.jpg",
        mime="image/jpeg",
        size_bytes=1,
    )
    assert (
        await add_project_media(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
            body=media_body,
        )
    ).status_code == 201

    assert (
        await list_project_media(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    projects.list_media = AsyncMock(
        return_value=[
            {
                "id": str(uuid4()),
                "project_id": PROJECT_ID,
                "kind": "photo",
                "path": "/p.jpg",
                "mime": "image/jpeg",
                "size_bytes": 1,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    assert (
        await list_project_media(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await delete_project_media(
            request=_request(),
            project_id=PROJECT_ID,
            media_id=str(uuid4()),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.TowersService")
async def test_tower_group_handlers(mock_towers_cls, mock_access):
    mock_access.return_value = _user_context()
    svc = mock_towers_cls.return_value
    svc.create_tower = AsyncMock(return_value=_tower_detail())
    svc.list_towers = AsyncMock(return_value=[_tower_detail()])
    svc.get_tower_detail = AsyncMock(return_value=_tower_detail())
    svc.update_tower = AsyncMock(return_value=_tower_detail())
    svc.delete_tower = AsyncMock(return_value={"old_data": {}})
    svc.create_wing = AsyncMock(return_value={"id": str(uuid4())})
    svc.list_wings = AsyncMock(return_value=[{"id": str(uuid4())}])
    svc.delete_wing = AsyncMock(return_value={"old_data": {}})
    svc.create_gate = AsyncMock(return_value={"id": str(uuid4())})
    svc.list_gates = AsyncMock(return_value=[{"id": str(uuid4())}])
    svc.delete_gate = AsyncMock(return_value={"old_data": {}})
    svc.create_lift = AsyncMock(return_value={"id": str(uuid4())})
    svc.list_lifts = AsyncMock(return_value=[{"id": str(uuid4())}])
    svc.delete_lift = AsyncMock(return_value={"old_data": {}})
    svc.create_floor = AsyncMock(return_value={"id": str(uuid4())})
    svc.list_floors = AsyncMock(return_value=[{"id": str(uuid4())}])
    svc.delete_floor = AsyncMock(return_value={"old_data": {}})

    tower_body = CreateTowerRequest(name="Tower A", tower_type=TowerType.RESIDENTIAL)
    wing_body = CreateTowerWingRequest(name="East")
    gate_body = CreateTowerGateRequest(name="Main Gate")
    lift_body = CreateTowerLiftRequest(name="Lift 1")
    floor_body = CreateFloorRequest(level_number=1, display_name="First")

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()

    assert (
        await create_tower(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=tower_body,
        )
    ).status_code == 201
    assert (
        await list_towers(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await get_tower_detail(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await update_tower(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
            body=UpdateTowerRequest(name="Tower A2"),
        )
    ).status_code == 200
    assert (
        await delete_tower(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    wing_id = str(uuid4())
    assert (
        await create_tower_wing(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
            body=wing_body,
        )
    ).status_code == 201
    assert (
        await list_tower_wings(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_tower_wing(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            wing_id=wing_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    gate_id = str(uuid4())
    assert (
        await create_tower_gate(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
            body=gate_body,
        )
    ).status_code == 201
    assert (
        await list_tower_gates(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_tower_gate(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            gate_id=gate_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    lift_id = str(uuid4())
    assert (
        await create_tower_lift(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
            body=lift_body,
        )
    ).status_code == 201
    assert (
        await list_tower_lifts(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_tower_lift(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            lift_id=lift_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    floor_id = str(uuid4())
    assert (
        await create_floor(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
            body=floor_body,
        )
    ).status_code == 201
    assert (
        await list_floors(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_floor(
            request=req,
            project_id=PROJECT_ID,
            tower_id=TOWER_ID,
            floor_id=floor_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.UnitConfigsService")
@patch("apps.user_service.app.api.projects.InventoryService")
async def test_configs_plots_and_inventory(
    mock_inventory_cls, mock_configs_cls, mock_access
):
    mock_access.return_value = _user_context()
    configs = mock_configs_cls.return_value
    configs.create_config = AsyncMock(return_value={"id": str(uuid4())})
    configs.list_configs = AsyncMock(return_value=[{"id": str(uuid4())}])
    configs.update_config = AsyncMock(return_value={"id": str(uuid4())})
    configs.delete_config = AsyncMock(return_value={"old_data": {}})
    configs.create_plot_item = AsyncMock(return_value={"id": str(uuid4())})
    configs.list_plot_items = AsyncMock(return_value=[{"id": str(uuid4())}])
    configs.delete_plot_item = AsyncMock(return_value={"old_data": {}})
    configs.add_media = AsyncMock(return_value={"id": str(uuid4())})
    configs.list_media = AsyncMock(return_value=[{"id": str(uuid4())}])
    configs.delete_media = AsyncMock(return_value={"old_data": {}})

    inventory = mock_inventory_cls.return_value
    inventory.upsert_inventory = AsyncMock(return_value=[{"quantity": 1}])
    inventory.get_inventory_summary = AsyncMock(return_value=_inventory_summary_payload())
    inventory.list_inventory = AsyncMock(return_value=[])

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()
    config_id = str(uuid4())

    config_body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.APARTMENT,
        name="2BHK",
        code="2bhk",
    )
    assert (
        await create_unit_config(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=config_body,
        )
    ).status_code == 201
    assert (
        await list_unit_configs(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await update_unit_config(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
            body=UpdateUnitConfigRequest(name="2BHK Plus"),
        )
    ).status_code == 200
    assert (
        await delete_unit_config(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    plot_body = CreatePlotConfigItemRequest(plot_no="P-1", size_sqft=1000)
    plot_id = str(uuid4())
    assert (
        await create_plot_item(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
            body=plot_body,
        )
    ).status_code == 201
    assert (
        await list_plot_items(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_plot_item(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            item_id=plot_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    media_body = ConfigMediaRequest(
        kind=ConfigMediaKind.FLOOR_PLAN,
        path="/m.jpg",
        mime="image/jpeg",
        size_bytes=1,
    )
    media_id = str(uuid4())
    assert (
        await add_config_media(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
            body=media_body,
        )
    ).status_code == 201
    assert (
        await list_config_media(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_config_media(
            request=req,
            project_id=PROJECT_ID,
            config_id=config_id,
            media_id=media_id,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    assert (
        await upsert_floor_inventory(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=UpsertFloorInventoryRequest(
                items=[
                    FloorInventoryItem(
                        tower_id=TOWER_ID,
                        floor_id=str(uuid4()),
                        config_id=config_id,
                        quantity=1,
                    )
                ]
            ),
        )
    ).status_code == 200
    assert (
        await get_inventory_summary(
            request=req,
            project_id=PROJECT_ID,
            tower_id=None,
            status=None,
            include_plot_items=True,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_floor_inventory(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.FacilitiesService")
@patch("apps.user_service.app.api.projects.UnitsService")
@patch("apps.user_service.app.api.projects.ContactUnitsService")
@patch("apps.user_service.app.api.projects.ContactUnitDocumentsService")
@patch("apps.user_service.app.api.projects.check_permissions", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.PassesService")
async def test_facilities_units_and_passes(
    mock_passes_cls,
    mock_check_perms,
    mock_docs_cls,
    mock_contact_units_cls,
    mock_units_cls,
    mock_facilities_cls,
    mock_access,
):
    mock_access.return_value = _user_context()
    mock_check_perms.return_value = _user_context()

    facilities = mock_facilities_cls.return_value
    facilities.create_facility = AsyncMock(return_value={"id": FACILITY_ID})
    facilities.list_facilities = AsyncMock(return_value={"items": [{"id": FACILITY_ID}], "total": 1})
    facilities.list_parking_slots = AsyncMock(return_value=[{"id": "slot-1"}])
    facilities.update_facility = AsyncMock(return_value={"id": FACILITY_ID})
    facilities.delete_facility = AsyncMock(return_value={"old_data": {}})

    units = mock_units_cls.return_value
    units.create_unit = AsyncMock(return_value={"id": UNIT_ID})
    units.create_units_bulk = AsyncMock(
        return_value={"created_count": 1, "items": [{"id": UNIT_ID}]}
    )
    units.list_units = AsyncMock(return_value={"items": [_unit_list_item()], "total": 1})
    units.get_units_registry_summary = AsyncMock(
        return_value={"total": 1, "sold_count": 0, "unsold_count": 1}
    )
    units.get_unit_detail = AsyncMock(
        return_value=_unit_detail(
            owner={
                "contact_id": str(uuid4()),
                "contact_unit_id": str(uuid4()),
                "display_name": "Owner One",
                "contact_type": "owner",
                "relationship": "self",
            }
        )
    )
    units.update_unit = AsyncMock(return_value={"id": UNIT_ID})
    units.delete_unit = AsyncMock(return_value={"old_data": {}})

    contact_units = mock_contact_units_cls.return_value
    owner_change = {"unit_status": UnitStatus.VACANT.value}
    contact_units.unassign_unit_owner = AsyncMock(return_value=owner_change)
    contact_units.reassign_unit_owner = AsyncMock(return_value=owner_change)

    docs = mock_docs_cls.return_value
    doc_row = {
        "id": "doc-1",
        "contact_unit_id": str(uuid4()),
        "document_type": ContactUnitDocumentType.OWNERSHIP_CERTIFICATE.value,
        "file_path": "/f.pdf",
        "file_name": "f.pdf",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    docs.list_unit_documents = AsyncMock(return_value=[doc_row])
    docs.add_unit_document = AsyncMock(return_value=doc_row)
    docs.delete_unit_document = AsyncMock(return_value=None)

    passes = mock_passes_cls.return_value
    passes.list_unit_passes_for_admin = AsyncMock(return_value=([], 0))

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()

    facility_body = CreateFacilityRequest(
        name="Clubhouse",
        facility_type=FacilityType.RECREATION,
        location_type=FacilityLocationType.INDOOR_CLUBHOUSE,
    )
    assert (
        await create_facility(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=facility_body,
        )
    ).status_code == 201

    facilities.list_facilities = AsyncMock(return_value={"items": [], "total": 0})
    assert (
        await list_facilities(
            request=req,
            project_id=PROJECT_ID,
            query=FacilityListQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    facilities.list_facilities = AsyncMock(
        return_value={"items": [{"id": FACILITY_ID, "name": "Club"}], "total": 1}
    )
    assert (
        await list_facilities(
            request=req,
            project_id=PROJECT_ID,
            query=FacilityListQuery(page=1, page_size=20),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    assert (
        await list_facility_parking_slots(
            request=req,
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            status=None,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await update_facility(
            request=req,
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=db,
            current_user=user,
            body=UpdateFacilityRequest(name="Club"),
        )
    ).status_code == 200
    assert (
        await delete_facility(
            request=req,
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    unit_body = CreateUnitRequest(code="A-101", config_id=str(uuid4()))
    assert (
        await create_unit(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=unit_body,
        )
    ).status_code == 201
    assert (
        await bulk_create_units(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=BulkCreateUnitsRequest(units=[unit_body]),
        )
    ).status_code == 201

    units.list_units = AsyncMock(return_value={"items": [], "total": 0})
    assert (
        await list_units(
            request=req,
            project_id=PROJECT_ID,
            query=ListProjectUnitsQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    units.list_units = AsyncMock(return_value={"items": [_unit_list_item()], "total": 1})
    assert (
        await list_units(
            request=req,
            project_id=PROJECT_ID,
            query=ListProjectUnitsQuery(page=1, page_size=10),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    assert (
        await get_units_registry_summary(
            request=req,
            project_id=PROJECT_ID,
            query=ListProjectUnitsFilterQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_unit_detail(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_unit_passes(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            query=AdminUnitPassListQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await unassign_unit_owner(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await reassign_unit_owner(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
            body=ReassignUnitOwnerRequest(
                contact_id=str(uuid4()),
                assign_date=date(2026, 1, 15),
            ),
        )
    ).status_code == 200
    assert (
        await list_unit_documents(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    doc_body = CreateUnitDocumentRequest(
        file_path="/f.pdf",
        file_name="f.pdf",
        document_type=ContactUnitDocumentType.OWNERSHIP_CERTIFICATE,
    )
    assert (
        await add_unit_document(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
            body=doc_body,
        )
    ).status_code == 201
    assert (
        await delete_unit_document(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            document_id=str(uuid4()),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await update_unit(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
            body=UpdateUnitRequest(status=UnitStatus.VACANT),
        )
    ).status_code == 200
    assert (
        await delete_unit(
            request=req,
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.UnitsService")
@patch("apps.user_service.app.api.projects.SiteMapService")
async def test_parking_zones_site_map_and_location(
    mock_site_cls, mock_units_cls, mock_access
):
    mock_access.return_value = _user_context()
    units = mock_units_cls.return_value
    units.list_parking_zones = AsyncMock(return_value=[{"id": str(uuid4())}])
    units.create_parking_zone = AsyncMock(return_value={"id": str(uuid4())})
    units.delete_parking_zone = AsyncMock(return_value={"old_data": {}})

    site = mock_site_cls.return_value
    site.update_location = AsyncMock(return_value={"latitude": 1.0, "longitude": 2.0})
    site.create_overlays = AsyncMock(return_value=[{"id": str(uuid4())}])
    site.list_overlays = AsyncMock(return_value=[{"id": str(uuid4())}])
    site.delete_overlay = AsyncMock(return_value={"old_data": {}})

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()
    zone_body = CreateParkingZoneRequest(
        tower_id=TOWER_ID,
        floor_id=str(uuid4()),
        name="Basement P1",
    )
    assert (
        await create_parking_zone(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=zone_body,
        )
    ).status_code == 201
    assert (
        await list_parking_zones(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await delete_parking_zone(
            request=req,
            project_id=PROJECT_ID,
            zone_id=str(uuid4()),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await update_project_location(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=UpdateProjectLocationRequest(latitude=19.0, longitude=72.0),
        )
    ).status_code == 200
    from apps.user_service.app.schemas.project_inventory import CreateSiteMapOverlayRequest

    overlay_body = CreateSiteMapOverlaysRequest(
        items=[
            CreateSiteMapOverlayRequest(
                entity_type="tower",
                entity_id=TOWER_ID,
                latitude=19.0,
                longitude=72.0,
            )
        ]
    )
    assert (
        await create_site_map_overlays(
            request=req,
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
            body=overlay_body,
        )
    ).status_code == 201
    assert (
        await list_site_map_overlays(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await delete_site_map_overlay(
            request=req,
            project_id=PROJECT_ID,
            overlay_id=str(uuid4()),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._project_staff_management_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.extract_user_context", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectMembersService")
@patch("apps.user_service.app.api.projects.ProjectRolesService")
async def test_project_rbac_and_members(
    mock_roles_cls,
    mock_members_cls,
    mock_extract,
    mock_staff_access,
    mock_mgmt_access,
):
    mock_staff_access.return_value = _user_context()
    mock_mgmt_access.return_value = _user_context()
    mock_extract.return_value = _user_context()

    roles = mock_roles_cls.return_value
    roles.list_roles = AsyncMock(return_value=[_role_item()])
    roles.create_role = AsyncMock(return_value=_role_detail())
    roles.list_assignable_permissions = AsyncMock(return_value=[])
    roles.get_role_detail = AsyncMock(return_value=_role_detail())
    roles.update_role = AsyncMock(return_value=_role_detail())
    roles.get_my_permissions = AsyncMock(
        return_value=ProjectMyPermissionsResponse(
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
            role_slug="security",
            effective_permissions=["view"],
        )
    )
    roles.delete_role = AsyncMock(return_value=None)

    member = MagicMock()
    member.model_dump.return_value = {
        "id": MEMBER_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "user_id": ADMIN_USER_ID,
        "project_role_id": ROLE_ID,
        "role_slug": "security",
        "status": "active",
    }
    members = mock_members_cls.return_value
    members.list_members = AsyncMock(return_value=[member])
    members.assign_member = AsyncMock(return_value=member)
    members.update_member = AsyncMock(return_value=member)
    members.remove_member = AsyncMock(return_value={"id": MEMBER_ID})

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()

    assert (
        await list_project_roles(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await create_project_role(
            request=req,
            project_id=PROJECT_ID,
            body=CreateProjectRoleRequest(name="Custom"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await list_assignable_project_role_permissions(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await get_project_role_detail(
            request=req,
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await update_project_role(
            request=req,
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
            body=UpdateProjectRoleRequest(name="Renamed"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_project_role(
            request=req,
            project_id=PROJECT_ID,
            project_role_id=ROLE_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_my_project_permissions(
            request=req, project_id=PROJECT_ID, db_connection=db, current_user=user
        )
    ).status_code == 200
    assert (
        await list_project_members(
            request=req,
            project_id=PROJECT_ID,
            query=ListProjectMembersQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await assign_project_member(
            request=req,
            project_id=PROJECT_ID,
            body=AssignProjectMemberRequest(
                user_id=ADMIN_USER_ID,
                project_role_id=ROLE_ID,
            ),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await update_project_member(
            request=req,
            project_id=PROJECT_ID,
            user_id=ADMIN_USER_ID,
            body=UpdateProjectMemberRequest(project_role_id=ROLE_ID),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await remove_project_member(
            request=req,
            project_id=PROJECT_ID,
            user_id=ADMIN_USER_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.VehiclesService")
async def test_vehicle_request_handlers(mock_vehicles_cls, mock_access):
    mock_access.return_value = _user_context()
    vehicles = mock_vehicles_cls.return_value
    vehicles.list_project_vehicles = AsyncMock(return_value=[{"id": VEHICLE_ID}])
    vehicles.export_project_vehicles_csv = AsyncMock(return_value="id\n1\n")
    vehicles.review_vehicle = AsyncMock(return_value={"id": VEHICLE_ID, "status": "approved"})
    vehicles.admin_delete_project_vehicle = AsyncMock(return_value={"id": VEHICLE_ID})

    db = MagicMock()
    user = {"sub": "staff-1"}
    req = _request()

    assert (
        await list_project_vehicle_requests(
            request=req,
            project_id=PROJECT_ID,
            status=VehicleStatus.PENDING,
            vehicle_type=None,
            fuel_type=None,
            search=None,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    vehicles.list_project_vehicles = AsyncMock(return_value=[])
    assert (
        await list_project_vehicle_requests(
            request=req,
            project_id=PROJECT_ID,
            status=None,
            vehicle_type=None,
            fuel_type=None,
            search="MH",
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200

    export_resp = await export_project_vehicle_requests(
        request=req,
        project_id=PROJECT_ID,
        query=VehicleRequestsExportQuery(),
        db_connection=db,
        current_user=user,
    )
    assert export_resp.status_code == 200

    review_body = ReviewVehicleRequest(status=VehicleStatus.APPROVED)
    assert (
        await review_project_vehicle_request(
            request=req,
            project_id=PROJECT_ID,
            vehicle_id=VEHICLE_ID,
            db_connection=db,
            current_user=user,
            body=review_body,
        )
    ).status_code == 200

    assert (
        await delete_project_vehicle(
            request=req,
            project_id=PROJECT_ID,
            vehicle_id=VEHICLE_ID,
            db_connection=db,
            current_user=user,
            body=DeleteProjectVehicleRequest(rejection_reason="Duplicate"),
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.projects._staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.projects.ProjectsService")
async def test_get_project_details_not_found_propagates(mock_projects_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_projects_cls.return_value.get_project_details = AsyncMock(
        side_effect=NotFoundException(
            message_key="project_setup.errors.project_not_found",
            custom_code=404,
        )
    )
    with pytest.raises(NotFoundException):
        await get_project_details(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
