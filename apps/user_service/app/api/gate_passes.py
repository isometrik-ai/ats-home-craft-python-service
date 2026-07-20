"""Gate pass verification API (permission-gated)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.gate_passes import (
    CheckInRequest,
    CheckOutRequest,
    VerifyPassRequest,
)
from apps.user_service.app.services.pass_verification_service import (
    PassVerificationService,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import VISITOR_MANAGEMENT_VERIFY
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/passes", tags=["Gate Passes"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("verify visitor pass")
@router.post(
    "/verify",
    status_code=http_status.HTTP_200_OK,
    summary="Verify a visitor pass by code",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def verify_pass(
    request: Request,
    body: VerifyPassRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Look up a pass by 4-digit code before admitting a guest."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = PassVerificationService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.verify(code=body.code, gate_id=body.gate_id)
    return success_response(
        request=request,
        message_key="passes.success.verified",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )


@handle_api_exceptions("check in visitor pass")
@router.post(
    "/{pass_id}/check-in",
    status_code=http_status.HTTP_200_OK,
    summary="Check in a visitor pass",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="pass_events",
    category="VISITOR_PASSES",
)
async def check_in_pass(
    request: Request,
    pass_id: str = Path(..., description="Pass UUID"),
    body: CheckInRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record guest entry at the gate."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    request.state.audit_table = "pass_events"
    request.state.audit_description = f"Checked in visitor pass: {pass_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    request.state.audit_requested_id = pass_id

    service = PassVerificationService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.check_in(pass_id=pass_id, body=body)
    request.state.raw_audit_new_data = result
    return success_response(
        request=request,
        message_key="passes.success.checked_in",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )


@handle_api_exceptions("check out visitor pass")
@router.post(
    "/{pass_id}/check-out",
    status_code=http_status.HTTP_200_OK,
    summary="Check out a visitor pass",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["soc2_audit", "audit_required"],
    table_name="pass_events",
    category="VISITOR_PASSES",
)
async def check_out_pass(
    request: Request,
    pass_id: str = Path(..., description="Pass UUID"),
    body: CheckOutRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record guest exit at the gate."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    request.state.audit_table = "pass_events"
    request.state.audit_description = f"Checked out visitor pass: {pass_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    request.state.audit_requested_id = pass_id

    service = PassVerificationService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.check_out(pass_id=pass_id, body=body)
    request.state.raw_audit_new_data = result
    return success_response(
        request=request,
        message_key="passes.success.checked_out",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
