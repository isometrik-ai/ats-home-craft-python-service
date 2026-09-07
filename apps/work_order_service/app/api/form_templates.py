"""Form templates API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import created_response, ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.dependencies.db import db_conn, db_uow
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.form_templates import CreateFormTemplateRequest
from apps.work_order_service.app.schemas.openapi import (
    FormTemplateApiResponse,
    FormTemplateListApiResponse,
)
from apps.work_order_service.app.services.form_templates_service import (
    FormTemplatesService,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Form Templates"])


@handle_api_exceptions("list form templates")
@router.get(
    "/{project_id}/form-templates",
    response_model=None,
    responses=ok_response(FormTemplateListApiResponse, "Paginated form templates."),
)
@limiter.limit("100/minute")
async def list_form_templates(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List form templates."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await FormTemplatesService(db_connection, ctx).list(
        project_id=project_id, page=page, page_size=page_size
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="work_orders.success.list",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create form template")
@router.post(
    "/{project_id}/form-templates",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(FormTemplateApiResponse, "Created form template."),
)
@limiter.limit("100/minute")
async def create_form_template(
    request: Request,
    project_id: str = Path(...),
    body: CreateFormTemplateRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create form template."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await FormTemplatesService(db_connection, ctx).create(
        project_id=project_id, data=dump_request(body)
    )
    return success_response(
        request=request,
        message_key="work_orders.success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
    )


@handle_api_exceptions("get form template")
@router.get(
    "/{project_id}/form-templates/{template_id}",
    response_model=None,
    responses=ok_response(FormTemplateApiResponse, "Form template detail."),
)
@limiter.limit("100/minute")
async def get_form_template(
    request: Request,
    project_id: str = Path(...),
    template_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get form template."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    record = await FormTemplatesService(db_connection, ctx).get(
        project_id=project_id, entity_id=template_id
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="work_orders.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )
