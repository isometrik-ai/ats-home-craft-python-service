"""Projects Management API Module

This module provides CRUD operations for project management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.projects import (
    CreateProjectRequest,
    ProjectDetailData,
    ProjectListQueryParams,
    ProjectListResponse,
    UpdateProjectRequest,
)
from apps.user_service.app.services.project_service import ProjectService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_CREATE,
    PROJECTS_MANAGEMENT_DELETE,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Projects Management"])

logger = get_logger("projects-api")


@handle_api_exceptions("create project")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new project",
    summary="Create a new project",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Project created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client or team member not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Project title already exists"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",  # Project management is critical for SOC2 compliance
        "audit_required",  # Project creation requires audit trail
    ],
    table_name="projects",
    category="PROJECT",
)
async def create_project(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateProjectRequest = Body(...),
):
    """Create a new project with team, repositories, and integrations.

    This endpoint creates:
    1. A team for the project (if team_members provided)
    2. Team members with project-specific data in additional_data JSONB field
    3. A project record
    4. Repository records (if provided)
    5. Integration records (if provided)

    All operations are executed within a single transaction.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_CREATE,
    )

    request.state.audit_table = "projects"
    request.state.audit_description = f"Created project: {body.project_title}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    # Create service and delegate to service
    project_service = ProjectService(
        user_context=user_context,
        db_connection=db_connection,
    )
    created = await project_service.create_project(body) or {}
    request.state.audit_requested_id = str(created.get("id", ""))
    request.state.raw_audit_new_data = created.get("new_data")

    return success_response(
        request=request,
        message_key="projects.success.project_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list projects")
@router.get(
    "",
    response_model=ProjectListResponse,
    status_code=http_status.HTTP_200_OK,
    description="List all projects with filtering and pagination",
    summary="List all projects",
    responses={
        http_status.HTTP_200_OK: {
            "model": ProjectListResponse,
            "description": "Projects retrieved successfully",
        },
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def list_projects(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    filters: ProjectListQueryParams = Query(...),
):
    """Retrieve a paginated list of projects with optional filtering and sorting.

    Supports filtering by client, status, priority, and tags.
    Also supports full-text search on title, description, and tags.
    """
    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )

    # Create service and delegate to service
    project_service = ProjectService(
        user_context=user_context,
        db_connection=db_connection,
    )
    projects, total_count = await project_service.list_projects(filters)

    if not projects:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=filters.page,
            page_size=filters.page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=projects,
        total=total_count,
        message_key="projects.success.projects_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=filters.page,
        page_size=filters.page_size,
    )


@handle_api_exceptions("get project details")
@router.get(
    "/{project_id}",
    response_model=ProjectDetailData,
    status_code=http_status.HTTP_200_OK,
    description="Get project details",
    summary="Get project details",
    responses={
        http_status.HTTP_200_OK: {
            "model": ProjectDetailData,
            "description": "Project details retrieved successfully",
        },
        http_status.HTTP_404_NOT_FOUND: {"description": "Project not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
async def get_project_details(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    project_id: str = Path(..., description="Project UUID or human-readable ID"),
    current_user: dict = Depends(get_user_from_auth),
):
    """Retrieve complete project details including team, repositories, and integrations.

    Returns full project information with:
    - Project details
    - Client information
    - Team information with project lead, tech lead, and members
    - Repository information
    - Integration information
    """
    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )

    # Create service and delegate to service
    project_service = ProjectService(
        user_context=user_context,
        db_connection=db_connection,
    )
    project_detail = await project_service.get_project_details(project_id)

    return success_response(
        request=request,
        message_key="projects.success.project_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=project_detail.model_dump(mode="json", exclude_none=False),
    )


@handle_api_exceptions("update project")
@router.patch(
    "/{project_id}",
    status_code=http_status.HTTP_200_OK,
    description="Update a project",
    summary="Update project",
    responses={
        http_status.HTTP_200_OK: {"description": "Project updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request or validation error"},
        http_status.HTTP_404_NOT_FOUND: {
            "description": "Project, repository, or integration not found"
        },
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="projects",
    category="PROJECT",
)
async def update_project(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    project_id: str = Path(..., description="Project UUID or human-readable ID"),
    body: UpdateProjectRequest = Body(...),
):
    """Update a project. Only provided fields are updated.

    List-type fields (team_members, repositories, integrations) support single operations:
    exactly one of add, update, or remove per call. All operations run in a single transaction.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
    )

    request.state.audit_table = "projects"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Updated project: {project_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    project_service = ProjectService(
        user_context=user_context,
        db_connection=db_connection,
    )
    result = await project_service.update_project(project_id, body) or {}
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")

    return success_response(
        request=request,
        message_key="projects.success.project_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete project")
@router.delete(
    "/{project_id}",
    status_code=http_status.HTTP_200_OK,
    description="Delete a project",
    summary="Delete project",
    responses={
        http_status.HTTP_200_OK: {"description": "Project deleted successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Project not found or already deleted"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="projects",
    category="PROJECT",
)
async def delete_project(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    project_id: str = Path(..., description="Project UUID or human-readable ID"),
):
    """Delete a project.

    Hard deletes all related: team, team members, repositories, integrations.
    Soft deletes the project (sets status to archived) for audit retention.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_DELETE,
    )

    request.state.audit_table = "projects"
    request.state.audit_requested_id = project_id
    request.state.audit_description = f"Deleted project: {project_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    project_service = ProjectService(
        user_context=user_context,
        db_connection=db_connection,
    )
    deleted = await project_service.delete_project(project_id)
    request.state.raw_audit_old_data = deleted

    return success_response(
        request=request,
        message_key="projects.success.project_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
