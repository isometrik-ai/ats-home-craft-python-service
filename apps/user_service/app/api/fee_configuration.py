"""Fee configuration API (Finance → Settings)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.enums import MeasurementUnit, UnitConfigKind
from apps.user_service.app.schemas.fee_configuration import (
    UpsertFeeConfigurationRequest,
)
from apps.user_service.app.services.fee_configuration_service import (
    FeeConfigurationService,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    FINANCE_MANAGEMENT_EDIT,
    FINANCE_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Fee Configuration"])

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
    request.state.audit_user_id = user_context.user_id
    request.state.audit_organization_id = user_context.organization_id


@handle_api_exceptions("get fee configuration")
@router.get(
    "/{project_id}/fee-configuration",
    status_code=http_status.HTTP_200_OK,
    summary="Get project fee configuration",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_fee_configuration(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return fee settings and per-category rates for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.get_configuration(project_id=project_id)
    return success_response(
        request=request,
        data=data,
        message_key="fee_configuration.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("update fee configuration")
@router.put(
    "/{project_id}/fee-configuration",
    status_code=http_status.HTTP_200_OK,
    summary="Upsert project fee configuration",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="project_fee_settings",
    category="FINANCE",
)
async def upsert_fee_configuration(
    request: Request,
    project_id: str = Path(...),
    body: UpsertFeeConfigurationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create or update fee configuration for a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.upsert_configuration(project_id=project_id, body=body)
    _set_audit(
        request,
        user_context,
        table="project_fee_settings",
        requested_id=project_id,
        description="Updated project fee configuration",
    )
    request.state.raw_audit_new_data = data
    return success_response(
        request=request,
        data=data,
        message_key="fee_configuration.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("preview fee configuration")
@router.get(
    "/{project_id}/fee-configuration/preview",
    status_code=http_status.HTTP_200_OK,
    summary="Preview maintenance fee calculation",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def preview_fee_configuration(
    request: Request,
    project_id: str = Path(...),
    unit_config_kind: UnitConfigKind = Query(...),
    unit_id: str | None = Query(default=None),
    area: float | None = Query(default=None, gt=0),
    measurement_unit: MeasurementUnit = Query(default=MeasurementUnit.SQ_FT),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Compute a maintenance fee preview for a unit or sample area."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.preview(
        project_id=project_id,
        unit_config_kind=unit_config_kind,
        unit_id=unit_id,
        area=area,
        measurement_unit=measurement_unit.value if measurement_unit else None,
    )
    return success_response(
        request=request,
        data=data,
        message_key="fee_configuration.success.preview",
        custom_code=CustomStatusCode.SUCCESS,
    )
