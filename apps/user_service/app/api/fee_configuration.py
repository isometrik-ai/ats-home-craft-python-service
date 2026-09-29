"""Staff fee configuration API (ADR 0018)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.fee_configuration import (
    CreateFeeHeadRequest,
    CreateFinanceSettingsRequest,
    FeeHeadDetailApiResponse,
    FeeHeadListApiResponse,
    FeeHeadListItemApiResponse,
    FinanceSettingsApiResponse,
    UpdateFeeHeadRequest,
    UpdateFeeHeadStatusRequest,
    UpdateFinanceSettingsRequest,
)
from apps.user_service.app.services.fee_configuration_service import (
    FeeConfigurationService,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    FINANCE_MANAGEMENT_EDIT,
    FINANCE_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects/{project_id}/fee-configuration", tags=["Fee Configuration"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict (stale version or duplicate fee head)."},
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


def _created_response(model: type, description: str) -> dict[int | str, dict]:
    """Build OpenAPI responses for HTTP 201 success."""
    return _ok_response(model, description, status_code=http_status.HTTP_201_CREATED)


FEE_HEAD_LIST_RESPONSES = _ok_response(
    FeeHeadListApiResponse,
    "Fee heads for the project, in kind order.",
)
FEE_HEAD_DETAIL_RESPONSES = _ok_response(
    FeeHeadDetailApiResponse,
    "Editor document for one fee head.",
)
FEE_HEAD_CREATED_RESPONSES = _created_response(
    FeeHeadDetailApiResponse,
    "Newly created fee head.",
)
FEE_HEAD_STATUS_RESPONSES = _ok_response(
    FeeHeadListItemApiResponse,
    "Fee head list row after a status change.",
)
FINANCE_SETTINGS_RESPONSES = _ok_response(
    FinanceSettingsApiResponse,
    "Project payment retries and pre-due reminders.",
)
FINANCE_SETTINGS_CREATED_RESPONSES = _created_response(
    FinanceSettingsApiResponse,
    "Newly created finance settings.",
)


@handle_api_exceptions("list fee heads")
@router.get(
    "/fee-heads",
    status_code=http_status.HTTP_200_OK,
    summary="List fee heads",
    response_model=None,
    responses=FEE_HEAD_LIST_RESPONSES,
)
@limiter.limit("100/minute")
async def list_fee_heads(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the project's fee heads with charge, frequency, and tax labels."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_fee_heads(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=total,
        page=1,
        page_size=max(total, 1),
        message_key="fee_configuration.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create fee head")
@router.post(
    "/fee-heads",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a fee head",
    response_model=None,
    responses=FEE_HEAD_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="fee_heads",
    category="FEE_CONFIGURATION",
)
async def create_fee_head(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateFeeHeadRequest = Body(...),
):
    """Create one fee head. Kind selects the charge shape. Category is derived."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.create_fee_head(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="fee_heads",
        description=f"Created fee head {data.get('name', body.kind.value)}",
        requested_id=str(data.get("id", "")),
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_configuration.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("get fee head")
@router.get(
    "/fee-heads/{fee_head_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get a fee head",
    response_model=None,
    responses=FEE_HEAD_DETAIL_RESPONSES,
)
@limiter.limit("100/minute")
async def get_fee_head(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    fee_head_id: str = Path(..., description="Fee head identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the editor document for one fee head."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.get_fee_head(project_id=project_id, fee_head_id=fee_head_id)
    return success_response(
        request=request,
        message_key="fee_configuration.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("update fee head")
@router.patch(
    "/fee-heads/{fee_head_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Save a fee head",
    response_model=None,
    responses=FEE_HEAD_DETAIL_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="fee_heads",
    category="FEE_CONFIGURATION",
)
async def update_fee_head(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    fee_head_id: str = Path(..., description="Fee head identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateFeeHeadRequest = Body(...),
):
    """Replace the editable fee-head document. Kind and category are not accepted."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.update_fee_head(
        project_id=project_id,
        fee_head_id=fee_head_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="fee_heads",
        description=f"Updated fee head {data.get('name', fee_head_id)}",
        requested_id=fee_head_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_configuration.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("update fee head status")
@router.patch(
    "/fee-heads/{fee_head_id}/status",
    status_code=http_status.HTTP_200_OK,
    summary="Set fee head status",
    response_model=None,
    responses=FEE_HEAD_STATUS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="fee_heads",
    category="FEE_CONFIGURATION",
)
async def update_fee_head_status(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    fee_head_id: str = Path(..., description="Fee head identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateFeeHeadStatusRequest = Body(...),
):
    """Toggle Active or Inactive from the fee-head list."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.update_status(
        project_id=project_id,
        fee_head_id=fee_head_id,
        version=body.version,
        status=body.status,
    )
    set_audit_context(
        request,
        user_context,
        table="fee_heads",
        description=f"Set fee head status to {body.status.value}",
        requested_id=fee_head_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_configuration.success.status_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get finance settings")
@router.get(
    "/finance-settings",
    status_code=http_status.HTTP_200_OK,
    summary="Get finance settings",
    response_model=None,
    responses=FINANCE_SETTINGS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_finance_settings(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return project-wide payment retries and pre-due reminders."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.get_settings(project_id=project_id)
    return success_response(
        request=request,
        message_key="fee_configuration.success.settings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("create finance settings")
@router.post(
    "/finance-settings",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create finance settings",
    response_model=None,
    responses=FINANCE_SETTINGS_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="finance_settings",
    category="FEE_CONFIGURATION",
)
async def create_finance_settings(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateFinanceSettingsRequest = Body(...),
):
    """Create the project's payment-retry and reminder settings."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.create_settings(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="finance_settings",
        description="Created finance settings",
        requested_id=project_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_configuration.success.settings_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("update finance settings")
@router.patch(
    "/finance-settings",
    status_code=http_status.HTTP_200_OK,
    summary="Save finance settings",
    response_model=None,
    responses=FINANCE_SETTINGS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="finance_settings",
    category="FEE_CONFIGURATION",
)
async def update_finance_settings(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateFinanceSettingsRequest = Body(...),
):
    """Replace the four dunning numbers."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    service = FeeConfigurationService(db_connection=db_connection, user_context=user_context)
    data = await service.update_settings(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="finance_settings",
        description="Updated finance settings",
        requested_id=project_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_configuration.success.settings_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
