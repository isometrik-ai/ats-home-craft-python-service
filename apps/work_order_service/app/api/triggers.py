"""Webhook trigger configuration API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import created_response, ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.db.repositories.integration_repository import (
    IntegrationRepository,
)
from apps.work_order_service.app.dependencies.db import db_conn, db_uow
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.integrations import (
    CreateTriggerRequest,
    UpdateTriggerRequest,
)
from apps.work_order_service.app.schemas.openapi import (
    DeleteIdApiResponse,
    TriggerApiResponse,
    TriggerListApiResponse,
    TriggerTestApiResponse,
)
from apps.work_order_service.app.services.events_service import EventsService
from apps.work_order_service.app.utils.webhook_url import validate_outbound_webhook_url
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Triggers"])


@handle_api_exceptions("list triggers")
@router.get(
    "/{project_id}/triggers",
    response_model=None,
    responses=ok_response(TriggerListApiResponse, "Webhook trigger configurations."),
)
@limiter.limit("100/minute")
async def list_triggers(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List triggers."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items = await IntegrationRepository(db_connection).list_triggers(
        organization_id=ctx.organization_id,
        project_id=project_id,
    )
    return success_response(
        request=request,
        message_key="success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data={"items": items},
    )


@handle_api_exceptions("create trigger")
@router.post(
    "/{project_id}/triggers",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(TriggerApiResponse, "Created trigger."),
)
@limiter.limit("30/minute")
async def create_trigger(
    request: Request,
    project_id: str = Path(...),
    body: CreateTriggerRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create trigger."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    payload = dump_request(body)
    validate_outbound_webhook_url(payload["webhook_url"])
    record = await IntegrationRepository(db_connection).create_trigger(
        {
            "organization_id": ctx.organization_id,
            "project_id": project_id,
            **payload,
        }
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update trigger")
@router.patch(
    "/{project_id}/triggers/{trigger_id}",
    response_model=None,
    responses=ok_response(TriggerApiResponse, "Updated trigger."),
)
@limiter.limit("30/minute")
async def update_trigger(
    request: Request,
    project_id: str = Path(...),
    trigger_id: str = Path(...),
    body: UpdateTriggerRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update trigger."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    patch = dump_request(body, partial=True)
    if "webhook_url" in patch:
        validate_outbound_webhook_url(patch["webhook_url"])
    repo = IntegrationRepository(db_connection)
    record = await repo.update_trigger(
        trigger_id,
        {
            "organization_id": ctx.organization_id,
            "project_id": project_id,
            **patch,
        },
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("delete trigger")
@router.delete(
    "/{project_id}/triggers/{trigger_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Deleted trigger."),
)
@limiter.limit("30/minute")
async def delete_trigger(
    request: Request,
    project_id: str = Path(...),
    trigger_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete trigger."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    ok = await IntegrationRepository(db_connection).soft_delete_trigger(
        entity_id=trigger_id,
        organization_id=ctx.organization_id,
        project_id=project_id,
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": trigger_id},
    )


@handle_api_exceptions("test trigger")
@router.post(
    "/{project_id}/triggers/{trigger_id}/test",
    response_model=None,
    responses=ok_response(TriggerTestApiResponse, "Trigger test delivery result."),
)
@limiter.limit("10/minute")
async def test_trigger(
    request: Request,
    project_id: str = Path(...),
    trigger_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Verify trigger."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    repo = IntegrationRepository(db_connection)
    trigger = await repo.get_trigger(
        entity_id=trigger_id,
        organization_id=ctx.organization_id,
        project_id=project_id,
    )
    if not trigger:
        raise NotFoundException(message_key="errors.not_found")
    result = await EventsService(db_connection).test_trigger(trigger=trigger, actor_name=ctx.email)
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
