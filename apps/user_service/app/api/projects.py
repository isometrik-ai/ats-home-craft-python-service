"""Project Setup API (project basics, media, wizard status/steps)."""
# pylint: disable=too-many-lines

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.contact_onboarding import ReviewVehicleRequest
from apps.user_service.app.schemas.enums import (
    ParkingSlotStatus,
    PropertyProjectStatus,
    PropertyType,
    UnitStatus,
    VehicleStatus,
)
from apps.user_service.app.schemas.project_inventory import (
    ConfigMediaRequest,
    CreateFacilityRequest,
    CreateParkingZoneRequest,
    CreatePlotConfigItemRequest,
    CreateSiteMapOverlayRequest,
    CreateUnitConfigRequest,
    CreateUnitRequest,
    InventorySummaryResponse,
    UnitDetailResponse,
    UpdateFacilityRequest,
    UpdateProjectLocationRequest,
    UpdateUnitConfigRequest,
    UpdateUnitRequest,
    UpsertFloorInventoryRequest,
)
from apps.user_service.app.schemas.project_setup import (
    CompleteStepRequest,
    CreateFloorRequest,
    CreateProjectRequest,
    CreateTowerGateRequest,
    CreateTowerLiftRequest,
    CreateTowerRequest,
    CreateTowerWingRequest,
    MyProjectSummaryResponse,
    ProjectDetailsResponse,
    ProjectMediaRequest,
    ProjectMediaResponse,
    ProjectStatusResponse,
    ProjectSummaryResponse,
    UpdateProjectRequest,
    UpdateTowerRequest,
)
from apps.user_service.app.services.facilities_service import FacilitiesService
from apps.user_service.app.services.inventory_service import InventoryService
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.projects_service import ProjectsService
from apps.user_service.app.services.site_map_service import SiteMapService
from apps.user_service.app.services.towers_service import TowersService
from apps.user_service.app.services.unit_configs_service import UnitConfigsService
from apps.user_service.app.services.units_service import UnitsService
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    check_permissions,
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_CREATE,
    PROJECTS_MANAGEMENT_DELETE,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Project Setup"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _set_audit(
    request: Request,
    user_context: UserContext,
    *,
    table: str,
    requested_id: str,
    description: str,
) -> None:
    """Populate request.state audit fields for the audit decorator."""
    request.state.audit_table = table
    request.state.audit_requested_id = requested_id
    request.state.audit_description = description
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }


@handle_api_exceptions("create project")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a project",
    description="Creates a project (step 1) and seeds the setup wizard steps.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="projects",
    category="PROJECT_SETUP",
)
async def create_project(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateProjectRequest = Body(...),
):
    """Create a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_CREATE,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.create_project(body)
    project_id = result["project_id"]
    request.state.audit_table = "projects"
    request.state.audit_requested_id = str(project_id)
    request.state.audit_description = f"Created project: {project_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")
    return success_response(
        request=request,
        message_key="project_setup.success.project_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=result.get("new_data"),
    )


@handle_api_exceptions("list projects")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List projects",
    description="Returns paginated projects from PostgreSQL, filtered via query params.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_projects(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        default=None, min_length=2, description="Name/code/developer search."
    ),
    status: PropertyProjectStatus | None = Query(default=None, description="Filter by status."),
    property_type: PropertyType | None = Query(
        default=None, description="Filter by property type."
    ),
    page: int = Query(default=1, ge=1, description="Page number."),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page."),
):
    """List projects with pagination."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.list_projects(
        search=search,
        status=status.value if status else None,
        property_type=property_type.value if property_type else None,
        page=page,
        page_size=page_size,
    )
    items = [
        ProjectSummaryResponse.model_validate(row).model_dump(exclude_none=True)
        for row in result["items"]
    ]
    total = int(result["total"])
    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="project_setup.success.projects_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("list my projects")
@router.get(
    "/mine",
    status_code=http_status.HTTP_200_OK,
    summary="List projects assigned to me",
    description="Returns paginated projects where the current user is an active project member.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_my_projects(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        default=None, min_length=2, description="Name/code/developer search."
    ),
    status: PropertyProjectStatus | None = Query(default=None, description="Filter by status."),
    property_type: PropertyType | None = Query(
        default=None, description="Filter by property type."
    ),
    page: int = Query(default=1, ge=1, description="Page number."),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page."),
):
    """List projects assigned to the logged-in user via project_members."""
    user_context = await extract_user_context(current_user, db_connection, request=request)
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.list_my_projects(
        search=search,
        status=status.value if status else None,
        property_type=property_type.value if property_type else None,
        page=page,
        page_size=page_size,
    )
    items = [
        MyProjectSummaryResponse.model_validate(row).model_dump(exclude_none=True)
        for row in result["items"]
    ]
    total = int(result["total"])
    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="project_setup.success.my_projects_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get project status")
@router.get(
    "/{project_id}/status",
    status_code=http_status.HTTP_200_OK,
    summary="Get project setup status",
    description="Returns the wizard step statuses and current step pointer.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_status(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get the setup wizard status for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectSetupService(db_connection=db_connection, user_context=user_context)
    status_data = await service.get_status(project_id=project_id)
    data = ProjectStatusResponse.model_validate(status_data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="project_setup.success.status_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("get project details")
@router.get(
    "/{project_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get project details",
    description="Returns a single project.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_details(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get a single project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    details = await service.get_project_details(project_id=project_id)
    details = ProjectDetailsResponse.model_validate(details).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="project_setup.success.project_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=details,
    )


@handle_api_exceptions("update project")
@router.patch(
    "/{project_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a project",
    description="Updates project fields; property_types changes re-seed setup steps.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="projects",
    category="PROJECT_SETUP",
)
async def update_project(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateProjectRequest = Body(...),
):
    """Update a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    request.state.audit_table = "projects"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Updated project: {project_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    result = await service.update_project(project_id=project_id, body=body)
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")
    return success_response(
        request=request,
        message_key="project_setup.success.project_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=result.get("new_data"),
    )


@handle_api_exceptions("delete project")
@router.delete(
    "/{project_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a project",
    description="Hard-deletes a project and its child records.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="projects",
    category="PROJECT_SETUP",
)
async def delete_project(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    request.state.audit_table = "projects"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Deleted project: {project_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    result = await service.delete_project(project_id=project_id)
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")
    return success_response(
        request=request,
        message_key="project_setup.success.project_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("complete project setup step")
@router.post(
    "/{project_id}/steps/{step_key}/complete",
    status_code=http_status.HTTP_200_OK,
    summary="Complete a setup step",
    description="Marks a wizard step completed and advances the current step pointer.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def complete_setup_step(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    step_key: str = Path(..., description="Setup step key."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CompleteStepRequest = Body(default=CompleteStepRequest()),
):
    """Complete a wizard step."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectSetupService(db_connection=db_connection, user_context=user_context)
    data = await service.complete_step(project_id=project_id, step_key=step_key, data=body.data)
    return success_response(
        request=request,
        message_key="project_setup.success.step_completed",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("complete project setup")
@router.post(
    "/{project_id}/complete",
    status_code=http_status.HTTP_200_OK,
    summary="Finalize project setup",
    description="Requires all steps done; sets the project status to active.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="projects",
    category="PROJECT_SETUP",
)
async def complete_project_setup(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Finalize the setup wizard."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectSetupService(db_connection=db_connection, user_context=user_context)
    request.state.audit_table = "projects"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Completed project setup: {project_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    data = await service.complete_wizard(project_id=project_id)
    return success_response(
        request=request,
        message_key="project_setup.success.setup_completed",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("add project media")
@router.post(
    "/{project_id}/media",
    status_code=http_status.HTTP_201_CREATED,
    summary="Attach media to a project",
    description="Stores media metadata (path/mime/size) as provided.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_media",
    category="PROJECT_SETUP",
)
async def add_project_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ProjectMediaRequest = Body(...),
):
    """Attach media to a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    request.state.audit_table = "project_media"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Added media to project: {project_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    data = await service.add_media(project_id=project_id, body=body)
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.media_added",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list project media")
@router.get(
    "/{project_id}/media",
    status_code=http_status.HTTP_200_OK,
    summary="List project media",
    description="Returns media rows for a project.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List media for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    rows = await service.list_media(project_id=project_id)
    items = [ProjectMediaResponse.model_validate(row).model_dump(exclude_none=True) for row in rows]
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.media_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete project media")
@router.delete(
    "/{project_id}/media/{media_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete project media",
    description="Removes a media row from a project.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_media",
    category="PROJECT_SETUP",
)
async def delete_project_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    media_id: str = Path(..., description="Media identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a project media row."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    request.state.audit_table = "project_media"
    request.state.audit_requested_id = media_id
    request.state.audit_description = f"Deleted project media: {media_id}"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    result = await service.remove_media(project_id=project_id, media_id=media_id)
    request.state.raw_audit_old_data = result.get("old_data")
    return success_response(
        request=request,
        message_key="project_setup.success.media_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Tower builder (towers, wings, gates, lifts, floors)
# ---------------------------------------------------------------------------


@handle_api_exceptions("create tower")
@router.post(
    "/{project_id}/towers",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a tower",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="towers",
    category="PROJECT_SETUP",
)
async def create_tower(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerRequest = Body(...),
):
    """Create a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_tower(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="towers",
        requested_id=str(data.get("id")),
        description=f"Created tower in project: {project_id}",
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.tower_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list towers")
@router.get(
    "/{project_id}/towers",
    status_code=http_status.HTTP_200_OK,
    summary="List towers",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_towers(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List towers for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    items = await service.list_towers(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.towers_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("update tower")
@router.patch(
    "/{project_id}/towers/{tower_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a tower",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="towers",
    category="PROJECT_SETUP",
)
async def update_tower(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateTowerRequest = Body(...),
):
    """Update a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="towers",
        requested_id=tower_id,
        description=f"Updated tower: {tower_id}",
    )
    data = await service.update_tower(project_id=project_id, tower_id=tower_id, body=body)
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.tower_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("delete tower")
@router.delete(
    "/{project_id}/towers/{tower_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a tower",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="towers",
    category="PROJECT_SETUP",
)
async def delete_tower(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="towers",
        requested_id=tower_id,
        description=f"Deleted tower: {tower_id}",
    )
    result = await service.delete_tower(project_id=project_id, tower_id=tower_id)
    request.state.raw_audit_old_data = result.get("old_data")
    return success_response(
        request=request,
        message_key="project_setup.success.tower_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create tower wing")
@router.post(
    "/{project_id}/towers/{tower_id}/wings",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a tower wing",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_tower_wing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerWingRequest = Body(...),
):
    """Create a wing under a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_wing(project_id=project_id, tower_id=tower_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.wing_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list tower wings")
@router.get(
    "/{project_id}/towers/{tower_id}/wings",
    status_code=http_status.HTTP_200_OK,
    summary="List tower wings",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_tower_wings(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List wings for a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    items = await service.list_wings(project_id=project_id, tower_id=tower_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.wings_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete tower wing")
@router.delete(
    "/{project_id}/towers/{tower_id}/wings/{wing_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a tower wing",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_tower_wing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    wing_id: str = Path(..., description="Wing identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a wing."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    await service.delete_wing(project_id=project_id, tower_id=tower_id, wing_id=wing_id)
    return success_response(
        request=request,
        message_key="project_setup.success.wing_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create tower gate")
@router.post(
    "/{project_id}/towers/{tower_id}/gates",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a tower gate",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_tower_gate(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerGateRequest = Body(...),
):
    """Create a gate under a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_gate(project_id=project_id, tower_id=tower_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.gate_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list tower gates")
@router.get(
    "/{project_id}/towers/{tower_id}/gates",
    status_code=http_status.HTTP_200_OK,
    summary="List tower gates",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_tower_gates(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List gates for a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    items = await service.list_gates(project_id=project_id, tower_id=tower_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.gates_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete tower gate")
@router.delete(
    "/{project_id}/towers/{tower_id}/gates/{gate_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a tower gate",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_tower_gate(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    gate_id: str = Path(..., description="Gate identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a gate."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    await service.delete_gate(project_id=project_id, tower_id=tower_id, gate_id=gate_id)
    return success_response(
        request=request,
        message_key="project_setup.success.gate_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create tower lift")
@router.post(
    "/{project_id}/towers/{tower_id}/lifts",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a tower lift",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_tower_lift(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerLiftRequest = Body(...),
):
    """Create a lift under a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_lift(project_id=project_id, tower_id=tower_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.lift_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list tower lifts")
@router.get(
    "/{project_id}/towers/{tower_id}/lifts",
    status_code=http_status.HTTP_200_OK,
    summary="List tower lifts",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_tower_lifts(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List lifts for a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    items = await service.list_lifts(project_id=project_id, tower_id=tower_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.lifts_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete tower lift")
@router.delete(
    "/{project_id}/towers/{tower_id}/lifts/{lift_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a tower lift",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_tower_lift(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    lift_id: str = Path(..., description="Lift identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a lift."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    await service.delete_lift(project_id=project_id, tower_id=tower_id, lift_id=lift_id)
    return success_response(
        request=request,
        message_key="project_setup.success.lift_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create floor")
@router.post(
    "/{project_id}/towers/{tower_id}/floors",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a floor",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_floor(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateFloorRequest = Body(...),
):
    """Create a floor under a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_floor(project_id=project_id, tower_id=tower_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.floor_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list floors")
@router.get(
    "/{project_id}/towers/{tower_id}/floors",
    status_code=http_status.HTTP_200_OK,
    summary="List floors",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_floors(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List floors for a tower."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    items = await service.list_floors(project_id=project_id, tower_id=tower_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.floors_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete floor")
@router.delete(
    "/{project_id}/towers/{tower_id}/floors/{floor_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a floor",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_floor(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    floor_id: str = Path(..., description="Floor identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a floor."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    await service.delete_floor(project_id=project_id, tower_id=tower_id, floor_id=floor_id)
    return success_response(
        request=request,
        message_key="project_setup.success.floor_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Unit configs (apartment / commercial / plot), plot items, config media
# ---------------------------------------------------------------------------


@handle_api_exceptions("create unit config")
@router.post(
    "/{project_id}/configs",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a unit configuration",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="unit_configs",
    category="PROJECT_SETUP",
)
async def create_unit_config(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateUnitConfigRequest = Body(...),
):
    """Create a unit configuration."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_config(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="unit_configs",
        requested_id=str(data.get("id")),
        description=f"Created config in project: {project_id}",
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.config_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list unit configs")
@router.get(
    "/{project_id}/configs",
    status_code=http_status.HTTP_200_OK,
    summary="List unit configurations",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_unit_configs(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_kind: str | None = Query(default=None, description="Filter by config kind."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List unit configurations."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    items = await service.list_configs(project_id=project_id, config_kind=config_kind)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.configs_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("update unit config")
@router.patch(
    "/{project_id}/configs/{config_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a unit configuration",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="unit_configs",
    category="PROJECT_SETUP",
)
async def update_unit_config(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUnitConfigRequest = Body(...),
):
    """Update a unit configuration."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="unit_configs",
        requested_id=config_id,
        description=f"Updated config: {config_id}",
    )
    data = await service.update_config(project_id=project_id, config_id=config_id, body=body)
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.config_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("delete unit config")
@router.delete(
    "/{project_id}/configs/{config_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a unit configuration",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="unit_configs",
    category="PROJECT_SETUP",
)
async def delete_unit_config(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a unit configuration."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="unit_configs",
        requested_id=config_id,
        description=f"Deleted config: {config_id}",
    )
    result = await service.delete_config(project_id=project_id, config_id=config_id)
    request.state.raw_audit_old_data = result.get("old_data")
    return success_response(
        request=request,
        message_key="project_setup.success.config_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create plot config item")
@router.post(
    "/{project_id}/configs/{config_id}/plot-items",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a plot item",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_plot_item(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreatePlotConfigItemRequest = Body(...),
):
    """Create a plot item under a plot config."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_plot_item(project_id=project_id, config_id=config_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.plot_item_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list plot config items")
@router.get(
    "/{project_id}/configs/{config_id}/plot-items",
    status_code=http_status.HTTP_200_OK,
    summary="List plot items",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_plot_items(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List plot items for a plot config."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    items = await service.list_plot_items(project_id=project_id, config_id=config_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.plot_items_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete plot config item")
@router.delete(
    "/{project_id}/configs/{config_id}/plot-items/{item_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a plot item",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_plot_item(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    item_id: str = Path(..., description="Plot item identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a plot item."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    await service.delete_plot_item(project_id=project_id, config_id=config_id, item_id=item_id)
    return success_response(
        request=request,
        message_key="project_setup.success.plot_item_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("add config media")
@router.post(
    "/{project_id}/configs/{config_id}/media",
    status_code=http_status.HTTP_201_CREATED,
    summary="Attach media to a config",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def add_config_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ConfigMediaRequest = Body(...),
):
    """Attach media to a config."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.add_media(project_id=project_id, config_id=config_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.config_media_added",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list config media")
@router.get(
    "/{project_id}/configs/{config_id}/media",
    status_code=http_status.HTTP_200_OK,
    summary="List config media",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_config_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List media for a config."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    items = await service.list_media(project_id=project_id, config_id=config_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.config_media_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete config media")
@router.delete(
    "/{project_id}/configs/{config_id}/media/{media_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete config media",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_config_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    media_id: str = Path(..., description="Media identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete config media."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    await service.delete_media(project_id=project_id, config_id=config_id, media_id=media_id)
    return success_response(
        request=request,
        message_key="project_setup.success.config_media_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Floor inventory
# ---------------------------------------------------------------------------


@handle_api_exceptions("upsert floor inventory")
@router.put(
    "/{project_id}/inventory",
    status_code=http_status.HTTP_200_OK,
    summary="Upsert the floor inventory matrix",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="floor_inventory",
    category="PROJECT_SETUP",
)
async def upsert_floor_inventory(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpsertFloorInventoryRequest = Body(...),
):
    """Upsert the floor inventory matrix for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = InventoryService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="floor_inventory",
        requested_id=project_id,
        description=f"Updated inventory for project: {project_id}",
    )
    items = await service.upsert_inventory(project_id=project_id, body=body)
    request.state.raw_audit_new_data = {"items": items}
    return success_response(
        request=request,
        message_key="project_setup.success.inventory_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data={"items": items},
    )


@handle_api_exceptions("list floor inventory")
@router.get(
    "/{project_id}/inventory/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Get inventory menu summary",
    description=(
        "Returns aggregated inventory data for the post-setup inventory screen: "
        "header stats, buildings, units, floors, and plot configs."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_inventory_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str | None = Query(
        default=None,
        description="Optional tower filter for units and floors.",
    ),
    status: UnitStatus | None = Query(
        default=None,
        description="Optional unit status filter.",
    ),
    include_plot_items: bool = Query(
        default=True,
        description="Include plot configs and plot items in the response.",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get the inventory menu summary for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = InventoryService(db_connection=db_connection, user_context=user_context)
    data = await service.get_inventory_summary(
        project_id=project_id,
        tower_id=tower_id,
        status=status,
        include_plot_items=include_plot_items,
    )
    payload = InventorySummaryResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="project_setup.success.inventory_summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=payload,
    )


@handle_api_exceptions("list floor inventory")
@router.get(
    "/{project_id}/inventory",
    status_code=http_status.HTTP_200_OK,
    summary="List the floor inventory matrix",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_floor_inventory(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List the floor inventory matrix for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = InventoryService(db_connection=db_connection, user_context=user_context)
    items = await service.list_inventory(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.inventory_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------


@handle_api_exceptions("create facility")
@router.post(
    "/{project_id}/facilities",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a facility",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="facilities",
    category="PROJECT_SETUP",
)
async def create_facility(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateFacilityRequest = Body(...),
):
    """Create a facility."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    data = await service.create_facility(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="facilities",
        requested_id=str(data.get("id")),
        description=f"Created facility in project: {project_id}",
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.facility_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list facilities")
@router.get(
    "/{project_id}/facilities",
    status_code=http_status.HTTP_200_OK,
    summary="List facilities",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_facilities(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List facilities for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    items = await service.list_facilities(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.facilities_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("list facility parking slots")
@router.get(
    "/{project_id}/facilities/{facility_id}/parking-slots",
    status_code=http_status.HTTP_200_OK,
    summary="List parking slots for a facility",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_facility_parking_slots(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    facility_id: str = Path(..., description="Facility identifier (UUID string)."),
    status: ParkingSlotStatus | None = Query(
        default=None,
        description="Filter by slot status (available, assigned, blocked).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List parking slots provisioned for a parking facility."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    items = await service.list_parking_slots(
        project_id=project_id,
        facility_id=facility_id,
        status=status.value if status else None,
    )
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.parking_slots_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("update facility")
@router.patch(
    "/{project_id}/facilities/{facility_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a facility",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="facilities",
    category="PROJECT_SETUP",
)
async def update_facility(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    facility_id: str = Path(..., description="Facility identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateFacilityRequest = Body(...),
):
    """Update a facility."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="facilities",
        requested_id=facility_id,
        description=f"Updated facility: {facility_id}",
    )
    data = await service.update_facility(project_id=project_id, facility_id=facility_id, body=body)
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.facility_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("delete facility")
@router.delete(
    "/{project_id}/facilities/{facility_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a facility",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="facilities",
    category="PROJECT_SETUP",
)
async def delete_facility(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    facility_id: str = Path(..., description="Facility identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a facility."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="facilities",
        requested_id=facility_id,
        description=f"Deleted facility: {facility_id}",
    )
    result = await service.delete_facility(project_id=project_id, facility_id=facility_id)
    request.state.raw_audit_old_data = result.get("old_data")
    return success_response(
        request=request,
        message_key="project_setup.success.facility_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Units + parking zones
# ---------------------------------------------------------------------------


@handle_api_exceptions("create unit")
@router.post(
    "/{project_id}/units",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a unit",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="units",
    category="PROJECT_SETUP",
)
async def create_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateUnitRequest = Body(...),
):
    """Create a unit."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_unit(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=str(data.get("id")),
        description=f"Created unit in project: {project_id}",
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.unit_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list units")
@router.get(
    "/{project_id}/units",
    status_code=http_status.HTTP_200_OK,
    summary="List units",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_units(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List units for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    items = await service.list_units(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.units_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get unit detail")
@router.get(
    "/{project_id}/units/{unit_id}/detail",
    status_code=http_status.HTTP_200_OK,
    summary="Get unit detail",
    description=(
        "Returns full unit detail for the inventory slide-out and unit registry: "
        "tower/floor, config, owner, residents, vehicles, and financial placeholders."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_unit_detail(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get full detail for one unit in a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_unit_detail(project_id=project_id, unit_id=unit_id)
    payload = UnitDetailResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="project_setup.success.unit_detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("update unit")
@router.patch(
    "/{project_id}/units/{unit_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a unit",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="units",
    category="PROJECT_SETUP",
)
async def update_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateUnitRequest = Body(...),
):
    """Update a unit."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=unit_id,
        description=f"Updated unit: {unit_id}",
    )
    data = await service.update_unit(project_id=project_id, unit_id=unit_id, body=body)
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.unit_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("delete unit")
@router.delete(
    "/{project_id}/units/{unit_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a unit",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="units",
    category="PROJECT_SETUP",
)
async def delete_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a unit."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=unit_id,
        description=f"Deleted unit: {unit_id}",
    )
    result = await service.delete_unit(project_id=project_id, unit_id=unit_id)
    request.state.raw_audit_old_data = result.get("old_data")
    return success_response(
        request=request,
        message_key="project_setup.success.unit_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("create parking zone")
@router.post(
    "/{project_id}/parking-zones",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a parking zone",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_parking_zone(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateParkingZoneRequest = Body(...),
):
    """Create a parking zone."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_parking_zone(project_id=project_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.parking_zone_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list parking zones")
@router.get(
    "/{project_id}/parking-zones",
    status_code=http_status.HTTP_200_OK,
    summary="List parking zones",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_parking_zones(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List parking zones for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    items = await service.list_parking_zones(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.parking_zones_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete parking zone")
@router.delete(
    "/{project_id}/parking-zones/{zone_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a parking zone",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_parking_zone(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    zone_id: str = Path(..., description="Parking zone identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a parking zone."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    await service.delete_parking_zone(project_id=project_id, zone_id=zone_id)
    return success_response(
        request=request,
        message_key="project_setup.success.parking_zone_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Site map
# ---------------------------------------------------------------------------


@handle_api_exceptions("update project location")
@router.patch(
    "/{project_id}/site-map/location",
    status_code=http_status.HTTP_200_OK,
    summary="Update project map location",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def update_project_location(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateProjectLocationRequest = Body(...),
):
    """Patch the project's map latitude/longitude."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    data = await service.update_location(project_id=project_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.project_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("create site map overlay")
@router.post(
    "/{project_id}/site-map/overlays",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a site map overlay",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_site_map_overlay(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateSiteMapOverlayRequest = Body(...),
):
    """Create a site map overlay marker."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    data = await service.create_overlay(project_id=project_id, body=body)
    return success_response(
        request=request,
        message_key="project_setup.success.overlay_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list site map overlays")
@router.get(
    "/{project_id}/site-map/overlays",
    status_code=http_status.HTTP_200_OK,
    summary="List site map overlays",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_site_map_overlays(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List site map overlays for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    items = await service.list_overlays(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.overlays_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete site map overlay")
@router.delete(
    "/{project_id}/site-map/overlays/{overlay_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a site map overlay",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_site_map_overlay(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    overlay_id: str = Path(..., description="Overlay identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a site map overlay."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    await service.delete_overlay(project_id=project_id, overlay_id=overlay_id)
    return success_response(
        request=request,
        message_key="project_setup.success.overlay_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Vehicle registration requests (admin review + parking slot assignment)
# ---------------------------------------------------------------------------


@handle_api_exceptions("list project vehicle requests")
@router.get(
    "/{project_id}/vehicle-requests",
    status_code=http_status.HTTP_200_OK,
    summary="List resident vehicle registration requests",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_vehicle_requests(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    status: VehicleStatus | None = Query(
        default=None,
        description="Filter by vehicle status (pending, approved, rejected).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List vehicle requests for admin review."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = VehiclesService(db_connection=db_connection, user_context=user_context)
    items = await service.list_project_vehicles(project_id=project_id, status=status)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="project_setup.success.vehicle_requests_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("review project vehicle request")
@router.patch(
    "/{project_id}/vehicle-requests/{vehicle_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Approve or reject a vehicle request",
    description=(
        "On approval, assigns an available parking slot from a parking facility. "
        "On rejection, stores rejection_reason."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="PROJECT_SETUP",
)
async def review_project_vehicle_request(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    vehicle_id: str = Path(..., description="Vehicle identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ReviewVehicleRequest = Body(...),
):
    """Approve or reject a resident vehicle registration request."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = VehiclesService(db_connection=db_connection, user_context=user_context)
    _set_audit(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Reviewed vehicle request: {vehicle_id}",
    )
    data = await service.review_vehicle(
        project_id=project_id,
        vehicle_id=vehicle_id,
        body=body,
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        message_key="project_setup.success.vehicle_request_reviewed",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )
