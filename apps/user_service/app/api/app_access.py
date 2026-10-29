"""Staff and resident app access control API (ADR 0019)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.app_access import (
    AppAccessOverrideRequest,
    AppAccessRuleApiResponse,
    AppAccessUnitItemApiResponse,
    AppAccessUnitListApiResponse,
    ResidentAppAccessApiResponse,
    UpsertAppAccessRuleRequest,
)
from apps.user_service.app.schemas.enums.app_access import AppAccessStatus
from apps.user_service.app.services.app_access_service import AppAccessService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    UserContext,
    ensure_staff_project_access,
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    APP_ACCESS_EDIT,
    APP_ACCESS_OVERRIDE,
    APP_ACCESS_VIEW,
)
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects/{project_id}/app-access", tags=["App Access"])
unit_router = APIRouter(prefix="/units/{unit_id}", tags=["App Access"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict (stale version)."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _ok_response(
    model: type,
    description: str,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> dict[int | str, dict]:
    """Build OpenAPI responses for a successful JSON envelope."""
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {"model": model, "description": description},
    }


RULE_RESPONSES = _ok_response(AppAccessRuleApiResponse, "Saved rule plus impact counts.")
UNITS_RESPONSES = _ok_response(AppAccessUnitListApiResponse, "Customers-tab rows.")
UNIT_ITEM_RESPONSES = _ok_response(AppAccessUnitItemApiResponse, "Unit row after override.")
RESIDENT_RESPONSES = _ok_response(ResidentAppAccessApiResponse, "Hold payload for one unit.")


async def _resident_for_unit(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    unit_id: str,
    request: Request,
) -> UserContext:
    """Confirm the caller has an active link to the unit."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user,
        db_connection,
        request=request,
    )
    repository = ContactUnitsRepository(db_connection)
    organization_id = str(user_context.organization_id)
    unit = await repository.get_unit_project(
        organization_id=organization_id,
        unit_id=unit_id,
    )
    if unit is None or not unit.get("project_id"):
        raise NotFoundException(
            message_key="app_access.errors.unit_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    linked = await repository.contact_has_active_unit(
        organization_id=organization_id,
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    if not linked:
        raise ForbiddenException(
            message_key="app_access.errors.unit_not_found",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    return user_context


@handle_api_exceptions("get app access rule")
@router.get(
    "/rule",
    status_code=http_status.HTTP_200_OK,
    summary="Get app access rule",
    response_model=None,
    responses=RULE_RESPONSES,
)
@limiter.limit("100/minute")
async def get_rule(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the project's rule and impact counts. Missing row is off."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=APP_ACCESS_VIEW,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    data = await service.get_rule(project_id=project_id)
    return success_response(
        request=request,
        message_key="app_access.success.rule_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("save app access rule")
@router.put(
    "/rule",
    status_code=http_status.HTTP_200_OK,
    summary="Save app access rule",
    response_model=None,
    responses=RULE_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="app_access_rules",
    category="APP_ACCESS",
)
async def put_rule(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpsertAppAccessRuleRequest = Body(...),
):
    """Create or replace the rule, then re-evaluate every candidate unit."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=APP_ACCESS_EDIT,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    data = await service.upsert_rule(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="app_access_rules",
        description="Saved app access rule",
        requested_id=project_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="app_access.success.rule_saved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list app access units")
@router.get(
    "/units",
    status_code=http_status.HTTP_200_OK,
    summary="List app access units",
    response_model=None,
    responses=UNITS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_units(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    q: str | None = Query(default=None, description="Unit code or occupant name."),
    status: AppAccessStatus | None = Query(
        default=None, description="Filter by evaluation status."
    ),
    page: int = Query(default=1, ge=1, description="Page number."),
    page_size: int = Query(default=25, ge=1, le=100, description="Rows per page."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Customers tab: outstanding, days past due, and hold state."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=APP_ACCESS_VIEW,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    items, total = await service.list_units(
        project_id=project_id,
        q=q,
        status=status.value if status is not None else None,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="app_access.success.units_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("set app access override")
@router.post(
    "/units/{unit_id}/override",
    status_code=http_status.HTTP_200_OK,
    summary="Hold or release one unit",
    response_model=None,
    responses=UNIT_ITEM_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="app_access_overrides",
    category="APP_ACCESS",
)
async def set_override(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: AppAccessOverrideRequest = Body(...),
):
    """Block now or release. Outstanding of zero is refused."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=APP_ACCESS_OVERRIDE,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    data = await service.set_override(project_id=project_id, unit_id=unit_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="app_access_overrides",
        description=f"Set app access override {body.kind.value}",
        requested_id=unit_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="app_access.success.override_saved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("clear app access override")
@router.delete(
    "/units/{unit_id}/override",
    status_code=http_status.HTTP_200_OK,
    summary="Back to rule",
    response_model=None,
    responses=UNIT_ITEM_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="app_access_overrides",
    category="APP_ACCESS",
)
async def clear_override(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete the override and re-evaluate the unit against the rule."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=APP_ACCESS_OVERRIDE,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    data = await service.clear_override(project_id=project_id, unit_id=unit_id)
    set_audit_context(
        request,
        user_context,
        table="app_access_overrides",
        description="Cleared app access override",
        requested_id=unit_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="app_access.success.override_cleared",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get resident app access")
@unit_router.get(
    "/app-access",
    status_code=http_status.HTTP_200_OK,
    summary="Get hold payload for a unit",
    response_model=None,
    responses=RESIDENT_RESPONSES,
)
@limiter.limit("100/minute")
async def get_resident_app_access(
    request: Request,
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Hold payload for the signed-in resident's unit. Missing state is not held."""
    user_context = await _resident_for_unit(
        current_user=current_user,
        db_connection=db_connection,
        unit_id=unit_id,
        request=request,
    )
    service = AppAccessService(db_connection, user_context=user_context)
    data = await service.resident_unit_access(
        organization_id=str(user_context.organization_id),
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="app_access.success.status_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
