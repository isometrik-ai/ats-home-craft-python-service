"""API keys management."""

import hashlib
import secrets

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
from apps.work_order_service.app.schemas.integrations import CreateApiKeyRequest
from apps.work_order_service.app.schemas.openapi import (
    ApiKeyCreatedApiResponse,
    ApiKeyListApiResponse,
    DeleteIdApiResponse,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — API Keys"])


def _generate_api_key() -> tuple[str, str, str]:
    """Generate api key."""
    raw = f"ats_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    return raw, key_hash, prefix


@handle_api_exceptions("list api keys")
@router.get(
    "/{project_id}/api-keys",
    response_model=None,
    responses=ok_response(ApiKeyListApiResponse, "Project API keys."),
)
@limiter.limit("60/minute")
async def list_api_keys(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List api keys."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items = await IntegrationRepository(db_connection).list_api_keys(
        organization_id=ctx.organization_id,
        project_id=project_id,
    )
    return success_response(
        request=request,
        message_key="success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data={"items": items},
    )


@handle_api_exceptions("create api key")
@router.post(
    "/{project_id}/api-keys",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(ApiKeyCreatedApiResponse, "Created API key."),
)
@limiter.limit("20/minute")
async def create_api_key(
    request: Request,
    project_id: str = Path(...),
    body: CreateApiKeyRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create api key."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    raw, key_hash, prefix = _generate_api_key()
    record = await IntegrationRepository(db_connection).create_api_key(
        {
            "organization_id": ctx.organization_id,
            "project_id": project_id,
            "name": body.name,
            "key_prefix": prefix,
            "key_hash": key_hash,
        }
    )
    record["key"] = raw
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("revoke api key")
@router.delete(
    "/{project_id}/api-keys/{key_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Revoked API key."),
)
@limiter.limit("20/minute")
async def revoke_api_key(
    request: Request,
    project_id: str = Path(...),
    key_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Revoke api key."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    ok = await IntegrationRepository(db_connection).soft_delete_api_key(
        entity_id=key_id,
        organization_id=ctx.organization_id,
        project_id=project_id,
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": key_id},
    )
