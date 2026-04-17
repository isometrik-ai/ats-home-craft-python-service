"""Contacts Import API (producer side).

Implements:
- POST /contacts/imports
- GET /contacts/imports/{job_id}
- GET /contacts/imports/{job_id}/errors
- POST /contacts/imports/{job_id}/retry
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Request
from fastapi import status as http_status
from fastapi.responses import RedirectResponse

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.contacts_imports import (
    CreateContactsImportJobRequest,
    CreateContactsImportJobResponse,
    GetContactsImportJobResponse,
    RetryContactsImportJobResponse,
)
from apps.user_service.app.services.contacts_imports_service import (
    CONTACTS_IMPORT_TOPIC,
    ContactsImportService,
)
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CLIENTS_MANAGEMENT_CREATE,
    CLIENTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/contacts/imports", tags=["Contacts Imports"])

CONTACTS_IMPORT_TOPICS: list[str] = [CONTACTS_IMPORT_TOPIC]

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"description": "Bad request."},
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}

JOB_NOT_FOUND = "contacts_imports.errors.job_not_found"
ERRORS_NOT_FOUND = "contacts_imports.errors.errors_not_found"


def _schedule_contacts_import_event_publish(
    *,
    background_tasks: BackgroundTasks,
    event_payload: dict[str, Any],
    job_id: str,
) -> None:
    """Schedule a contacts import event publish."""
    background_tasks.add_task(
        EventService.publish_event_background,
        event=event_payload,
        key=str(job_id),
        topics=CONTACTS_IMPORT_TOPICS,
    )


@handle_api_exceptions("create contacts import job")
@router.post(
    "",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Create a contacts import job",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="import_jobs",
    category="CLIENT",
)
async def create_contacts_import_job(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateContactsImportJobRequest = Body(...),
):
    """Create a contacts import job for the authenticated organization."""
    job_id: str | None = None
    event_payload: dict[str, Any] | None = None

    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_CREATE,
        )
        request.state.audit_table = "import_jobs"
        request.state.audit_description = "Created contacts import job"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        service = ContactsImportService(db_connection=db_connection)
        job, event_payload = await service.create_job_and_enqueue(
            organization_id=user_context.organization_id,
            requested_by=str(user_context.user_id) if user_context.user_id else None,
            file_url=str(body.file_url),
            file_type=body.file_type.value,
            schema_version=body.schema_version,
            mapping=body.mapping,
            options=body.options.model_dump(mode="json") if body.options else None,
        )

        job_id = str(job["job_id"])
        request.state.audit_requested_id = job_id
        request.state.raw_audit_new_data = job

    if event_payload is not None and job_id:
        _schedule_contacts_import_event_publish(
            background_tasks=background_tasks,
            event_payload=event_payload,
            job_id=job_id,
        )

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_created",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=CreateContactsImportJobResponse(
            job_id=str(job["job_id"]),
            status=job["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )


@handle_api_exceptions("get contacts import job")
@router.get(
    "/{job_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get contacts import job status",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def get_contacts_import_job(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return status and progress for a contacts import job."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsImportService(db_connection=db_connection)
    job = await service.get_job(job_id=job_id, organization_id=user_context.organization_id)
    if job is None:
        raise NotFoundException(message_key=JOB_NOT_FOUND)

    data = GetContactsImportJobResponse.from_job_row(job).model_dump(exclude_none=True, mode="json")

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("download contacts import job errors")
@router.get(
    "/{job_id}/errors",
    status_code=http_status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Download row-level errors (redirect)",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def get_contacts_import_job_errors(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Redirect to the presigned URL containing row-level import errors."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsImportService(db_connection=db_connection)
    job = await service.get_job(job_id=job_id, organization_id=user_context.organization_id)
    if job is None:
        raise NotFoundException(message_key=JOB_NOT_FOUND)

    url = job.get("errors_file_url")
    if not url:
        raise NotFoundException(message_key=ERRORS_NOT_FOUND)

    return RedirectResponse(url=str(url), status_code=http_status.HTTP_307_TEMPORARY_REDIRECT)


@handle_api_exceptions("retry contacts import job")
@router.post(
    "/{job_id}/retry",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Retry contacts import job",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="import_jobs",
    category="CLIENT",
)
async def retry_contacts_import_job(
    request: Request,
    background_tasks: BackgroundTasks,
    job_id: str = Path(..., min_length=5, max_length=128),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Retry a previously created contacts import job."""
    event_payload: dict[str, Any] | None = None

    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_CREATE,
        )
        request.state.audit_table = "import_jobs"
        request.state.audit_requested_id = job_id
        request.state.audit_description = f"Retried contacts import job: {job_id}"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        service = ContactsImportService(db_connection=db_connection)
        result = await service.retry_job_and_enqueue(
            job_id=job_id,
            organization_id=user_context.organization_id,
            requested_by=str(user_context.user_id) if user_context.user_id else None,
        )
        if result is None:
            raise NotFoundException(message_key=JOB_NOT_FOUND)
        job, event_payload = result
        request.state.raw_audit_new_data = job

    if event_payload is not None:
        _schedule_contacts_import_event_publish(
            background_tasks=background_tasks,
            event_payload=event_payload,
            job_id=str(job["job_id"]),
        )

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_queued",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=RetryContactsImportJobResponse(
            job_id=str(job["job_id"]),
            status=job["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )
