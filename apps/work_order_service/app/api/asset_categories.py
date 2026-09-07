"""Asset categories API."""

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
from apps.work_order_service.app.schemas.assets import (
    CreateAssetCategoryRequest,
    UpdateAssetCategoryRequest,
)
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.openapi import (
    AssetCategoryApiResponse,
    AssetCategoryListApiResponse,
    DeleteIdApiResponse,
)
from apps.work_order_service.app.services.asset_categories_service import (
    AssetCategoriesService,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Asset Categories"])


@handle_api_exceptions("list asset categories")
@router.get(
    "/{project_id}/asset-categories",
    response_model=None,
    responses=ok_response(AssetCategoryListApiResponse, "Paginated asset categories."),
)
@limiter.limit("100/minute")
async def list_asset_categories(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List asset categories."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    service = AssetCategoriesService(db_connection, ctx)
    items, total = await service.list(
        project_id=project_id, page=page, page_size=page_size, search=search
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get asset category")
@router.get(
    "/{project_id}/asset-categories/{category_id}",
    response_model=None,
    responses=ok_response(AssetCategoryApiResponse, "Asset category detail."),
)
@limiter.limit("100/minute")
async def get_asset_category(
    request: Request,
    project_id: str = Path(...),
    category_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get asset category."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    record = await AssetCategoriesService(db_connection, ctx).get(
        project_id=project_id, entity_id=category_id
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("create asset category")
@router.post(
    "/{project_id}/asset-categories",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(AssetCategoryApiResponse, "Created asset category."),
)
@limiter.limit("60/minute")
async def create_asset_category(
    request: Request,
    project_id: str = Path(...),
    body: CreateAssetCategoryRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create asset category."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await AssetCategoriesService(db_connection, ctx).create(
        project_id=project_id, data=dump_request(body)
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update asset category")
@router.patch(
    "/{project_id}/asset-categories/{category_id}",
    response_model=None,
    responses=ok_response(AssetCategoryApiResponse, "Updated asset category."),
)
@limiter.limit("60/minute")
async def update_asset_category(
    request: Request,
    project_id: str = Path(...),
    category_id: str = Path(...),
    body: UpdateAssetCategoryRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update asset category."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await AssetCategoriesService(db_connection, ctx).update(
        project_id=project_id,
        entity_id=category_id,
        data=dump_request(body, partial=True),
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("delete asset category")
@router.delete(
    "/{project_id}/asset-categories/{category_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Deleted asset category."),
)
@limiter.limit("60/minute")
async def delete_asset_category(
    request: Request,
    project_id: str = Path(...),
    category_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete asset category."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    ok = await AssetCategoriesService(db_connection, ctx).delete(
        project_id=project_id, entity_id=category_id
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": category_id},
    )
