"""Project workbook import API."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.projects_imports import (
    CreateProjectsImportJobRequest,
    CreateProjectsImportJobResponse,
    GetProjectsImportJobResponse,
)
from apps.user_service.app.services.projects_imports_service import (
    ProjectsImportService,
    schedule_projects_import_job,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_CREATE,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects/imports", tags=["Project Imports"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"description": "Bad request."},
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}

JOB_NOT_FOUND = "projects_imports.errors.job_not_found"


@handle_api_exceptions("create projects import job")
@router.post(
    "",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Import projects from a Project_data_bulk workbook",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("20/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="import_jobs",
    category="PROJECT_SETUP",
)
async def create_projects_import_job(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateProjectsImportJobRequest = Body(...),
):
    """Queue a workbook import and return immediately; poll GET /{job_id} for status."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_CREATE,
    )
    request.state.audit_table = "import_jobs"
    request.state.audit_description = "Created projects import job"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    service = ProjectsImportService(db_connection=db_connection, user_context=user_context)
    job = await service.create_job(
        organization_id=user_context.organization_id,
        file_url=str(body.file_url),
        file_type=body.file_type.value,
        schema_version=body.schema_version,
        community_admin_user_id=body.community_admin_user_id,
    )
    schedule_projects_import_job(
        job_id=str(job["job_id"]),
        organization_id=user_context.organization_id,
        user_context=user_context,
    )
    request.state.audit_requested_id = str(job["job_id"])
    request.state.raw_audit_new_data = job
    return success_response(
        request=request,
        message_key="projects_imports.success.job_created",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=CreateProjectsImportJobResponse(
            job_id=str(job["job_id"]),
            status=job["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )


@handle_api_exceptions("get projects import job")
@router.get(
    "/{job_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get a projects import job",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def get_projects_import_job(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return job details and progress for a projects import."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectsImportService(db_connection=db_connection, user_context=user_context)
    job = await service.get_job(job_id=job_id, organization_id=user_context.organization_id)
    if job is None:
        raise NotFoundException(message_key=JOB_NOT_FOUND)
    return success_response(
        request=request,
        message_key="projects_imports.success.job_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=GetProjectsImportJobResponse.from_job_row(job).model_dump(
            exclude_none=True, mode="json"
        ),
    )


@handle_api_exceptions("get projects import errors")
@router.get(
    "/{job_id}/errors",
    status_code=http_status.HTTP_200_OK,
    summary="List projects import row errors",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def get_projects_import_errors(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Page size"),
):
    """List row-level errors for a projects import job."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
    )
    service = ProjectsImportService(db_connection=db_connection, user_context=user_context)
    job = await service.get_job(job_id=job_id, organization_id=user_context.organization_id)
    if job is None:
        raise NotFoundException(message_key=JOB_NOT_FOUND)
    items, total = await service.list_job_error_rows(
        job_id=job_id,
        organization_id=user_context.organization_id,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=_serialize_error_rows(items),
        total=total,
        page=page,
        page_size=page_size,
        message_key="projects_imports.success.errors_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


def _serialize_error_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the import-error fields returned to the client."""
    serialized: list[dict[str, Any]] = []
    for item in items:
        serialized.append(
            {
                "row_number": item.get("row_number"),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
                "raw_row": item.get("raw_row"),
            }
        )
    return serialized
