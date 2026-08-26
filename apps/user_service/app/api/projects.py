"""Project Setup API (project basics, media, wizard status/steps)."""
# pylint: disable=too-many-lines

from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.contact_onboarding import (
    DeleteProjectVehicleRequest,
    ReviewVehicleRequest,
)
from apps.user_service.app.schemas.enums import (
    FacilityStatus,
    ParkingSlotStatus,
    PropertyProjectStatus,
    PropertyType,
    UnitStatus,
    VehicleFuelType,
    VehicleStatus,
    VehicleType,
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
    InventorySummaryResponse,
    ListProjectUnitsFilterQuery,
    ListProjectUnitsQuery,
    ReassignUnitOwnerRequest,
    UnitDetailResponse,
    UnitDocumentResponse,
    UnitListItemResponse,
    UnitListSummary,
    UnitOwnerChangeResponse,
    UpdateFacilityRequest,
    UpdateProjectLocationRequest,
    UpdateUnitConfigRequest,
    UpdateUnitRequest,
    UpsertFloorInventoryRequest,
    build_facility_list_query,
)
from apps.user_service.app.schemas.project_members import (
    AssignProjectMemberRequest,
    ListProjectMembersQuery,
    UpdateProjectMemberRequest,
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
    TowerDetailResponse,
    UpdateProjectRequest,
    UpdateTowerRequest,
)
from apps.user_service.app.services.contact_unit_documents_service import (
    ContactUnitDocumentsService,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.services.facilities_service import FacilitiesService
from apps.user_service.app.services.inventory_service import InventoryService
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.services.project_members_service import ProjectMembersService
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.projects_service import ProjectsService
from apps.user_service.app.services.site_map_service import SiteMapService
from apps.user_service.app.services.towers_service import TowersService
from apps.user_service.app.services.unit_configs_service import UnitConfigsService
from apps.user_service.app.services.units_service import UnitsService
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    UserContext,
    check_any_permissions,
    check_permissions,
    ensure_staff_project_access,
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECT_MEMBERS_MANAGE,
    PROJECTS_MANAGEMENT_CREATE,
    PROJECTS_MANAGEMENT_DELETE,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
    VISITOR_MANAGEMENT_VIEW,
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
    risk_level: str = "low",
    old_data: Any | None = None,
    new_data: Any | None = None,
) -> None:
    """Populate request.state audit fields for the audit decorator."""
    set_audit_context(
        request,
        user_context,
        table=table,
        requested_id=requested_id,
        description=description,
        risk_level=risk_level,
        old_data=old_data,
        new_data=new_data,
    )


async def _staff_project_access(
    *,
    request: Request,
    current_user: dict,
    db_connection: asyncpg.Connection,
    project_id: str,
    permission_codes: list[str] | str,
) -> UserContext:
    """Thin wrapper for project-scoped staff route permission checks."""
    return await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=permission_codes,
        request=request,
    )


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
    _set_audit(
        request,
        user_context,
        table="projects",
        requested_id=str(project_id),
        description=f"Created project: {project_id}",
        old_data=result.get("old_data"),
        new_data=result.get("new_data"),
    )
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
    user_context = await check_any_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=[
            PROJECTS_MANAGEMENT_VIEW,
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        ],
        request=request,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.update_project(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="projects",
        requested_id=project_id,
        description=f"Updated project: {project_id}",
        old_data=result.get("old_data"),
        new_data=result.get("new_data"),
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_project(project_id=project_id)
    _set_audit(
        request,
        user_context,
        table="projects",
        requested_id=project_id,
        description=f"Deleted project: {project_id}",
        risk_level="high",
        old_data=result.get("old_data"),
        new_data=result.get("new_data"),
    )
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
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_setup_steps",
    category="PROJECT_SETUP",
)
async def complete_setup_step(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    step_key: str = Path(..., description="Setup step key."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CompleteStepRequest = Body(default=CompleteStepRequest()),
):
    """Complete a wizard step."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectSetupService(db_connection=db_connection, user_context=user_context)
    data = await service.complete_step(project_id=project_id, step_key=step_key, data=body.data)
    _set_audit(
        request,
        user_context,
        table="project_setup_steps",
        requested_id=step_key,
        description=f"Completed setup step {step_key} for project: {project_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectSetupService(db_connection=db_connection, user_context=user_context)
    data = await service.complete_wizard(project_id=project_id)
    _set_audit(
        request,
        user_context,
        table="projects",
        requested_id=project_id,
        description=f"Completed project setup: {project_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    data = await service.add_media(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="project_media",
        requested_id=project_id,
        description=f"Added media to project: {project_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ProjectsService(db_connection=db_connection, user_context=user_context)
    result = await service.remove_media(project_id=project_id, media_id=media_id)
    _set_audit(
        request,
        user_context,
        table="project_media",
        requested_id=media_id,
        description=f"Deleted project media: {media_id}",
        old_data=result.get("old_data"),
    )
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
    description=(
        "Create a tower. Optionally include nested `wings`, `gates`, `lifts`, and `floors` "
        "to create the full tower setup in one request. Nested gates/floors link to wings "
        "via optional `wing_client_key` (matching wing `code` or `name`). "
        "Individual nested resources can still be added later via their dedicated endpoints."
    ),
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
    """Create a tower, optionally with its wings, gates, lifts, and floors."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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


@handle_api_exceptions("get tower detail")
@router.get(
    "/{project_id}/towers/{tower_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get tower detail",
    description=(
        "Returns a tower with nested `wings`, `gates`, `lifts`, and `floors` for the builder edit page."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_tower_detail(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get a tower with its nested builder entities."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.get_tower_detail(project_id=project_id, tower_id=tower_id)
    return success_response(
        request=request,
        message_key="project_setup.success.tower_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=TowerDetailResponse.model_validate(data).model_dump(exclude_none=True),
    )


@handle_api_exceptions("update tower")
@router.patch(
    "/{project_id}/towers/{tower_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a tower",
    description=(
        "Patch a tower. Optionally include nested `wings`, `gates`, `lifts`, and `floors` "
        "to upsert child records in the same request. Items with an `id` are updated; items "
        "without an `id` are created. Nested gates/floors link to wings via optional "
        "`wing_client_key` (matching wing `code`, `name`, or `id`). When nested arrays are "
        "omitted, the response is the tower row only; when present, the response also includes "
        "the processed `wings`, `gates`, `lifts`, and `floors` arrays."
    ),
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.update_tower(project_id=project_id, tower_id=tower_id, body=body)
    _set_audit(
        request,
        user_context,
        table="towers",
        requested_id=tower_id,
        description=f"Updated tower: {tower_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_tower(project_id=project_id, tower_id=tower_id)
    _set_audit(
        request,
        user_context,
        table="towers",
        requested_id=tower_id,
        description=f"Deleted tower: {tower_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_wings",
    category="PROJECT_SETUP",
)
async def create_tower_wing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerWingRequest = Body(...),
):
    """Create a wing under a tower."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_wing(project_id=project_id, tower_id=tower_id, body=body)
    _set_audit(
        request,
        user_context,
        table="tower_wings",
        requested_id=str(data.get("id")),
        description=f"Created wing in tower: {tower_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_wings",
    category="PROJECT_SETUP",
)
async def delete_tower_wing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    wing_id: str = Path(..., description="Wing identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a wing."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_wing(project_id=project_id, tower_id=tower_id, wing_id=wing_id)
    _set_audit(
        request,
        user_context,
        table="tower_wings",
        requested_id=wing_id,
        description=f"Deleted wing: {wing_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_gates",
    category="PROJECT_SETUP",
)
async def create_tower_gate(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerGateRequest = Body(...),
):
    """Create a gate under a tower."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_gate(project_id=project_id, tower_id=tower_id, body=body)
    _set_audit(
        request,
        user_context,
        table="tower_gates",
        requested_id=str(data.get("id")),
        description=f"Created gate in tower: {tower_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_gates",
    category="PROJECT_SETUP",
)
async def delete_tower_gate(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    gate_id: str = Path(..., description="Gate identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a gate."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_gate(project_id=project_id, tower_id=tower_id, gate_id=gate_id)
    _set_audit(
        request,
        user_context,
        table="tower_gates",
        requested_id=gate_id,
        description=f"Deleted gate: {gate_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_lifts",
    category="PROJECT_SETUP",
)
async def create_tower_lift(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateTowerLiftRequest = Body(...),
):
    """Create a lift under a tower."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_lift(project_id=project_id, tower_id=tower_id, body=body)
    _set_audit(
        request,
        user_context,
        table="tower_lifts",
        requested_id=str(data.get("id")),
        description=f"Created lift in tower: {tower_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="tower_lifts",
    category="PROJECT_SETUP",
)
async def delete_tower_lift(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    lift_id: str = Path(..., description="Lift identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a lift."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_lift(project_id=project_id, tower_id=tower_id, lift_id=lift_id)
    _set_audit(
        request,
        user_context,
        table="tower_lifts",
        requested_id=lift_id,
        description=f"Deleted lift: {lift_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="floors",
    category="PROJECT_SETUP",
)
async def create_floor(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateFloorRequest = Body(...),
):
    """Create a floor under a tower."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    data = await service.create_floor(project_id=project_id, tower_id=tower_id, body=body)
    _set_audit(
        request,
        user_context,
        table="floors",
        requested_id=str(data.get("id")),
        description=f"Created floor in tower: {tower_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="floors",
    category="PROJECT_SETUP",
)
async def delete_floor(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    tower_id: str = Path(..., description="Tower identifier (UUID string)."),
    floor_id: str = Path(..., description="Floor identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a floor."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = TowersService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_floor(project_id=project_id, tower_id=tower_id, floor_id=floor_id)
    _set_audit(
        request,
        user_context,
        table="floors",
        requested_id=floor_id,
        description=f"Deleted floor: {floor_id}",
        old_data=result.get("old_data"),
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.update_config(project_id=project_id, config_id=config_id, body=body)
    _set_audit(
        request,
        user_context,
        table="unit_configs",
        requested_id=config_id,
        description=f"Updated config: {config_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_config(project_id=project_id, config_id=config_id)
    _set_audit(
        request,
        user_context,
        table="unit_configs",
        requested_id=config_id,
        description=f"Deleted config: {config_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="plot_config_items",
    category="PROJECT_SETUP",
)
async def create_plot_item(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreatePlotConfigItemRequest = Body(...),
):
    """Create a plot item under a plot config."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_plot_item(project_id=project_id, config_id=config_id, body=body)
    _set_audit(
        request,
        user_context,
        table="plot_config_items",
        requested_id=str(data.get("id")),
        description=f"Created plot item in config: {config_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="plot_config_items",
    category="PROJECT_SETUP",
)
async def delete_plot_item(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    item_id: str = Path(..., description="Plot item identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a plot item."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_plot_item(
        project_id=project_id, config_id=config_id, item_id=item_id
    )
    _set_audit(
        request,
        user_context,
        table="plot_config_items",
        requested_id=item_id,
        description=f"Deleted plot item: {item_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="config_media",
    category="PROJECT_SETUP",
)
async def add_config_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ConfigMediaRequest = Body(...),
):
    """Attach media to a config."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    data = await service.add_media(project_id=project_id, config_id=config_id, body=body)
    _set_audit(
        request,
        user_context,
        table="config_media",
        requested_id=str(data.get("id")),
        description=f"Added media to config: {config_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="config_media",
    category="PROJECT_SETUP",
)
async def delete_config_media(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    config_id: str = Path(..., description="Config identifier (UUID string)."),
    media_id: str = Path(..., description="Media identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete config media."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitConfigsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_media(
        project_id=project_id, config_id=config_id, media_id=media_id
    )
    _set_audit(
        request,
        user_context,
        table="config_media",
        requested_id=media_id,
        description=f"Deleted config media: {media_id}",
        old_data=result.get("old_data"),
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = InventoryService(db_connection=db_connection, user_context=user_context)
    items = await service.upsert_inventory(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="floor_inventory",
        requested_id=project_id,
        description=f"Updated inventory for project: {project_id}",
        new_data={"items": items},
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.facility_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


def get_facility_list_query(
    facility_types: Annotated[
        list[str] | None,
        Query(
            description=(
                "Filter by one or more facility types. "
                "Repeat facility_types or pass comma-separated values."
            ),
        ),
    ] = None,
    status: FacilityStatus | None = Query(default=None, description="Filter by status."),
) -> FacilityListQuery:
    """Parse facility list filters from query params."""
    return build_facility_list_query(facility_types=facility_types, status=status)


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
    query: FacilityListQuery = Depends(get_facility_list_query),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List facilities for a project."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    parsed_types = (
        [facility_type.value for facility_type in query.facility_types]
        if query.facility_types
        else None
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    items = await service.list_facilities(
        project_id=project_id,
        facility_types=parsed_types,
        status=query.status.value if query.status else None,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    data = await service.update_facility(project_id=project_id, facility_id=facility_id, body=body)
    _set_audit(
        request,
        user_context,
        table="facilities",
        requested_id=facility_id,
        description=f"Updated facility: {facility_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = FacilitiesService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_facility(project_id=project_id, facility_id=facility_id)
    _set_audit(
        request,
        user_context,
        table="facilities",
        requested_id=facility_id,
        description=f"Deleted facility: {facility_id}",
        old_data=result.get("old_data"),
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.unit_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("bulk create units")
@router.post(
    "/{project_id}/units/bulk",
    status_code=http_status.HTTP_201_CREATED,
    summary="Bulk create units",
    description=(
        "Create up to 200 units in a single request. All rows are inserted in one "
        "transaction; if any unit code conflicts with an existing project unit, the "
        "entire request fails."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="units",
    category="PROJECT_SETUP",
)
async def bulk_create_units(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: BulkCreateUnitsRequest = Body(...),
):
    """Create many units for a project in one call."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_units_bulk(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=project_id,
        description=f"Bulk created {data.get('created_count', 0)} units in project: {project_id}",
        new_data={
            "created_count": data.get("created_count"),
            "unit_ids": [item.get("id") for item in data.get("items", [])],
        },
    )
    return success_response(
        request=request,
        message_key="project_setup.success.units_created_bulk",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list units")
@router.get(
    "/{project_id}/units",
    status_code=http_status.HTTP_200_OK,
    summary="List units",
    description=(
        "Returns paginated non-parking units for the unit registry table. "
        "Each item includes `parking_entitlement` from the unit config. "
        "Supports search by unit code/label/owner name and filters for property type, "
        "tower, config, and unit status."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_units(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ListProjectUnitsQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List units for a project with filters and pagination."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    result = await service.list_units(
        project_id=project_id,
        search=query.search,
        property_type=query.property_type.value if query.property_type else None,
        tower_id=query.tower_id,
        config_id=query.config_id,
        status=query.status.value if query.status else None,
        page=query.page,
        page_size=query.page_size,
    )
    items = [
        UnitListItemResponse.model_validate(row).model_dump(exclude_none=True)
        for row in result["items"]
    ]
    total = int(result["total"])
    if not items:
        return list_response(
            request=request,
            items=[],
            total=total,
            page=query.page,
            page_size=query.page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="project_setup.success.units_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get units registry summary")
@router.get(
    "/{project_id}/units/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Get unit registry summary",
    description=(
        "Returns total, sold, and unsold counts for the unit registry header cards. "
        "Accepts the same filters as the unit list endpoint."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_units_registry_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ListProjectUnitsFilterQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return aggregate unit counts for the registry header."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    summary = await service.get_units_registry_summary(
        project_id=project_id,
        search=query.search,
        property_type=query.property_type.value if query.property_type else None,
        tower_id=query.tower_id,
        config_id=query.config_id,
        status=query.status.value if query.status else None,
    )
    data = UnitListSummary.model_validate(summary).model_dump()
    return success_response(
        request=request,
        message_key="project_setup.success.units_summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_unit_detail(project_id=project_id, unit_id=unit_id)
    validated = UnitDetailResponse.model_validate(data)
    payload = validated.model_dump(exclude_none=True)
    if validated.owner is not None:
        payload["owner"] = validated.owner.model_dump()
    return success_response(
        request=request,
        message_key="project_setup.success.unit_detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("list unit visitor passes")
@router.get(
    "/{project_id}/units/{unit_id}/passes",
    status_code=http_status.HTTP_200_OK,
    summary="List visitor passes for a unit",
    description=(
        "Admin list of QRs / visitor passes issued for a flat. "
        "Use for the unit detail 'QRs Generated' section."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_unit_passes(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    query: AdminUnitPassListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List visitor passes generated for one unit."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=VISITOR_MANAGEMENT_VIEW,
        request=request,
    )
    service = PassesService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_unit_passes_for_admin(
        project_id=project_id,
        unit_id=unit_id,
        query=query,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="passes.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("unassign unit owner")
@router.delete(
    "/{project_id}/units/{unit_id}/owner",
    status_code=http_status.HTTP_200_OK,
    summary="Unassign owner from a unit",
    description=(
        "Marks pending/active contact_units allotment links as moved_out and sets "
        "the unit inventory status to vacant."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_units",
    category="PROJECT_SETUP",
)
async def unassign_unit_owner(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove the current owner allotment from a unit."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ContactUnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.unassign_unit_owner(project_id=project_id, unit_id=unit_id)
    payload = UnitOwnerChangeResponse.model_validate(data).model_dump(exclude_none=True)
    _set_audit(
        request,
        user_context,
        table="contact_units",
        requested_id=unit_id,
        description=f"Unassigned owner from unit: {unit_id}",
        new_data=payload,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.unit_owner_unassigned",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("reassign unit owner")
@router.post(
    "/{project_id}/units/{unit_id}/reassign",
    status_code=http_status.HTTP_200_OK,
    summary="Reassign unit owner",
    description=(
        "Replaces the current unit assignee with another contact in one step. "
        "Works for pending or active allotments and sets the unit to occupied. "
        "Requires assign_date (YYYY-MM-DD)."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="contact_units",
    category="PROJECT_SETUP",
)
async def reassign_unit_owner(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ReassignUnitOwnerRequest = Body(...),
):
    """Replace the owner on a unit with a new contact."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ContactUnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.reassign_unit_owner(
        project_id=project_id,
        unit_id=unit_id,
        contact_id=body.contact_id,
        assign_date=body.assign_date,
        is_primary=body.is_primary,
        relationship=body.relationship.value,
    )
    payload = UnitOwnerChangeResponse.model_validate(data).model_dump(exclude_none=True)
    _set_audit(
        request,
        user_context,
        table="contact_units",
        requested_id=unit_id,
        description=f"Reassigned owner on unit: {unit_id}",
        new_data=payload,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.unit_owner_reassigned",
        custom_code=CustomStatusCode.SUCCESS,
        data=payload,
    )


@handle_api_exceptions("list unit documents")
@router.get(
    "/{project_id}/units/{unit_id}/documents",
    status_code=http_status.HTTP_200_OK,
    summary="List owner documents for a unit",
    description=(
        "Returns ownership documents (lease, tax receipt, etc.) for the "
        "current Owner contact_units allotment on this unit."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_unit_documents(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List documents for the current owner allotment on a unit."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ContactUnitDocumentsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await service.list_unit_documents(project_id=project_id, unit_id=unit_id)
    payload = [UnitDocumentResponse.model_validate(item).model_dump() for item in items]
    return list_response(
        request=request,
        items=payload,
        total=len(payload),
        page=1,
        page_size=max(len(payload), 1),
        message_key="project_setup.success.unit_documents_retrieved",
        custom_code=CustomStatusCode.SUCCESS if payload else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("add unit document")
@router.post(
    "/{project_id}/units/{unit_id}/documents",
    status_code=http_status.HTTP_201_CREATED,
    summary="Add an owner document to a unit",
    description=(
        "Registers a storage path for an ownership document on the current "
        "Owner allotment. Upload the file separately, then pass file_path here."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="contact_unit_documents",
    category="PROJECT_SETUP",
)
async def add_unit_document(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateUnitDocumentRequest = Body(...),
):
    """Add a document to the current owner allotment on a unit."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ContactUnitDocumentsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.add_unit_document(
        project_id=project_id,
        unit_id=unit_id,
        body=body,
    )
    payload = UnitDocumentResponse.model_validate(data).model_dump()
    _set_audit(
        request,
        user_context,
        table="contact_unit_documents",
        requested_id=payload["id"],
        description=f"Added unit document: {payload['id']}",
        new_data=payload,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.unit_document_added",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=payload,
    )


@handle_api_exceptions("delete unit document")
@router.delete(
    "/{project_id}/units/{unit_id}/documents/{document_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete an owner document from a unit",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="contact_unit_documents",
    category="PROJECT_SETUP",
)
async def delete_unit_document(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    document_id: str = Path(..., description="Document identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete one document from the current owner allotment on a unit."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = ContactUnitDocumentsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    await service.delete_unit_document(
        project_id=project_id,
        unit_id=unit_id,
        document_id=document_id,
    )
    _set_audit(
        request,
        user_context,
        table="contact_unit_documents",
        requested_id=document_id,
        description=f"Deleted unit document: {document_id}",
    )
    return success_response(
        request=request,
        message_key="project_setup.success.unit_document_deleted",
        custom_code=CustomStatusCode.SUCCESS,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.update_unit(project_id=project_id, unit_id=unit_id, body=body)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=unit_id,
        description=f"Updated unit: {unit_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_unit(project_id=project_id, unit_id=unit_id)
    _set_audit(
        request,
        user_context,
        table="units",
        requested_id=unit_id,
        description=f"Deleted unit: {unit_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="parking_zones",
    category="PROJECT_SETUP",
)
async def create_parking_zone(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateParkingZoneRequest = Body(...),
):
    """Create a parking zone."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_parking_zone(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="parking_zones",
        requested_id=str(data.get("id")),
        description=f"Created parking zone in project: {project_id}",
        new_data=data,
    )
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="parking_zones",
    category="PROJECT_SETUP",
)
async def delete_parking_zone(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    zone_id: str = Path(..., description="Parking zone identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a parking zone."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = UnitsService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_parking_zone(project_id=project_id, zone_id=zone_id)
    _set_audit(
        request,
        user_context,
        table="parking_zones",
        requested_id=zone_id,
        description=f"Deleted parking zone: {zone_id}",
        old_data=result.get("old_data"),
    )
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
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="projects",
    category="PROJECT_SETUP",
)
async def update_project_location(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateProjectLocationRequest = Body(...),
):
    """Patch the project's map latitude/longitude."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    data = await service.update_location(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="projects",
        requested_id=project_id,
        description=f"Updated project location: {project_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.project_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("create site map overlays")
@router.post(
    "/{project_id}/site-map/overlays",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create site map overlays",
    description="Create one or more geo overlay markers in a single request.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="site_map_overlays",
    category="PROJECT_SETUP",
)
async def create_site_map_overlays(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateSiteMapOverlaysRequest = Body(...),
):
    """Create site map overlay markers in bulk."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    items = await service.create_overlays(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="site_map_overlays",
        requested_id=project_id,
        description=f"Created site map overlays in project: {project_id}",
        new_data=items,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.overlay_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=items,
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
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
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="site_map_overlays",
    category="PROJECT_SETUP",
)
async def delete_site_map_overlay(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    overlay_id: str = Path(..., description="Overlay identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a site map overlay."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )
    service = SiteMapService(db_connection=db_connection, user_context=user_context)
    result = await service.delete_overlay(project_id=project_id, overlay_id=overlay_id)
    _set_audit(
        request,
        user_context,
        table="site_map_overlays",
        requested_id=overlay_id,
        description=f"Deleted site map overlay: {overlay_id}",
        old_data=result.get("old_data"),
    )
    return success_response(
        request=request,
        message_key="project_setup.success.overlay_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Project members (staff assignment)
# ---------------------------------------------------------------------------


@handle_api_exceptions("list project members")
@router.get(
    "/{project_id}/members",
    status_code=http_status.HTTP_200_OK,
    summary="List staff assigned to a project",
    description=(
        "Returns project member rows joined with organization member profiles. "
        "Filter by role, status, or search by name/email."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_members(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ListProjectMembersQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return project_members rows joined with organization member profiles."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=[
            PROJECTS_MANAGEMENT_VIEW,
            PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        ],
    )
    service = ProjectMembersService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await service.list_members(
        project_id=project_id,
        role=query.role,
        status=query.status,
        search=query.search,
    )
    payload = [item.model_dump() for item in items]
    return list_response(
        request=request,
        items=payload,
        total=len(payload),
        page=1,
        page_size=max(len(payload), 1),
        message_key="project_members.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS if payload else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("assign project member")
@router.post(
    "/{project_id}/members",
    status_code=http_status.HTTP_201_CREATED,
    summary="Assign an organization member to a project",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_members",
    category="PROJECT_MEMBERS",
)
async def assign_project_member(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    body: AssignProjectMemberRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Assign an active organization member to a project."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECT_MEMBERS_MANAGE,
    )
    service = ProjectMembersService(
        db_connection=db_connection,
        user_context=user_context,
    )
    member = await service.assign_member(project_id=project_id, body=body)
    data = member.model_dump()
    _set_audit(
        request,
        user_context,
        table="project_members",
        requested_id=data.get("id", project_id),
        description=f"Assigned project member {body.user_id} to project {project_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_members.success.assigned",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("update project member")
@router.patch(
    "/{project_id}/members/{user_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a project member role or status",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_members",
    category="PROJECT_MEMBERS",
)
async def update_project_member(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    user_id: str = Path(..., description="Auth user id of the project member."),
    body: UpdateProjectMemberRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update project member role or status."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECT_MEMBERS_MANAGE,
    )
    service = ProjectMembersService(
        db_connection=db_connection,
        user_context=user_context,
    )
    member = await service.update_member(
        project_id=project_id,
        user_id=user_id,
        body=body,
    )
    data = member.model_dump()
    _set_audit(
        request,
        user_context,
        table="project_members",
        requested_id=data.get("id", user_id),
        description=f"Updated project member {user_id} on project {project_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_members.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove project member")
@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove a project member assignment",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_members",
    category="PROJECT_MEMBERS",
)
async def remove_project_member(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    user_id: str = Path(..., description="Auth user id of the project member."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Suspend a user's assignment to a project."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECT_MEMBERS_MANAGE,
    )
    service = ProjectMembersService(
        db_connection=db_connection,
        user_context=user_context,
    )
    await service.remove_member(project_id=project_id, user_id=user_id)
    _set_audit(
        request,
        user_context,
        table="project_members",
        requested_id=user_id,
        description=f"Removed project member {user_id} from project {project_id}",
        old_data={"user_id": user_id, "project_id": project_id},
    )
    return success_response(
        request=request,
        message_key="project_members.success.removed",
        custom_code=CustomStatusCode.SUCCESS,
    )


# ---------------------------------------------------------------------------
# Vehicle registration requests (admin review + parking slot assignment)
# ---------------------------------------------------------------------------


@handle_api_exceptions("list project vehicle requests")
@router.get(
    "/{project_id}/vehicle-requests",
    status_code=http_status.HTTP_200_OK,
    summary="List resident vehicle registration requests",
    description=(
        "Each item includes nested `owner` (unit Owner contact: display name, phone, email, "
        "profile_photo_url), `unit` (code, location_label, property_type, config, floor, "
        "status), `parking_allotment` (slot number, status, facility) when assigned, and "
        "`approved_by` / `rejected_by` org-member summaries when reviewed. "
        "Optional `search` matches registration number or unit code/label. "
        "Filter by `status`, `vehicle_type`, and `fuel_type`."
    ),
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
    vehicle_type: VehicleType | None = Query(
        default=None,
        description="Filter by vehicle type (two_wheeler, four_wheeler).",
    ),
    fuel_type: VehicleFuelType | None = Query(
        default=None,
        description="Filter by fuel type (non_ev, ev).",
    ),
    search: str | None = Query(
        default=None,
        min_length=1,
        description="Search by vehicle registration number or unit code/label.",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List vehicle requests for admin review."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = VehiclesService(db_connection=db_connection, user_context=user_context)
    items = await service.list_project_vehicles(
        project_id=project_id,
        status=status,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        search=search,
    )
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
        "On approval, optionally links the vehicle to a parking slot already allotted "
        "to the unit via parking allotment (does not change slot status). "
        "On rejection, stores rejection_reason. Response includes nested "
        "`approved_by` / `rejected_by` org-member summaries when applicable."
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
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = VehiclesService(db_connection=db_connection, user_context=user_context)
    data = await service.review_vehicle(
        project_id=project_id,
        vehicle_id=vehicle_id,
        body=body,
    )
    _set_audit(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Reviewed vehicle request: {vehicle_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="project_setup.success.vehicle_request_reviewed",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("delete project vehicle")
@router.delete(
    "/{project_id}/vehicles/{vehicle_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Remove or delete a project vehicle",
    description=(
        "Admin removes a vehicle in the project. Pending and rejected requests are hard-deleted; "
        "approved vehicles are soft-removed and any assigned parking slot is released. "
        "Requires rejection_reason in the request body."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="vehicles",
    category="PROJECT_SETUP",
)
async def delete_project_vehicle(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    vehicle_id: str = Path(..., description="Vehicle identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: DeleteProjectVehicleRequest = Body(...),
):
    """Remove or delete a vehicle registered in a project (admin)."""
    user_context = await _staff_project_access(
        request=request,
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )
    service = VehiclesService(db_connection=db_connection, user_context=user_context)
    data = await service.admin_delete_project_vehicle(
        project_id=project_id,
        vehicle_id=vehicle_id,
        rejection_reason=body.rejection_reason,
    )
    _set_audit(
        request,
        user_context,
        table="vehicles",
        requested_id=vehicle_id,
        description=f"Removed vehicle: {vehicle_id}",
        old_data={"project_id": project_id, "vehicle_id": vehicle_id},
        new_data={**(data or {}), "rejection_reason": body.rejection_reason},
    )
    return success_response(
        request=request,
        message_key="project_setup.success.vehicle_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )
