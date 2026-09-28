"""Gate pass verification API (permission-gated)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
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
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import VISITOR_MANAGEMENT_VERIFY
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException
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


async def _ensure_gate_access_for_pass(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    pass_row: dict,
    request: Request,
):
    """Resolve project from the pass and enforce visitor_management.verify."""
    project_id = pass_row.get("project_id")
    if not project_id:
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    return await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=str(project_id),
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )


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
    user_context = await extract_user_context(current_user, db_connection, request=request)
    org_id = user_context.organization_id
    if not org_id:
        raise ForbiddenException(
            message_key="auth.errors.session_not_found",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )
    pass_row = await PassesRepository(db_connection).get_by_code(
        organization_id=org_id,
        code=body.code,
    )
    if not pass_row:
        raise NotFoundException(
            message_key="passes.errors.pass_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    user_context = await _ensure_gate_access_for_pass(
        current_user=current_user,
        db_connection=db_connection,
        pass_row=pass_row,
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
    user_context = await extract_user_context(current_user, db_connection, request=request)
    org_id = user_context.organization_id
    if not org_id:
        raise ForbiddenException(
            message_key="auth.errors.session_not_found",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )
    pass_row = await PassesRepository(db_connection).get_by_id(
        organization_id=org_id,
        pass_id=pass_id,
    )
    if not pass_row:
        raise NotFoundException(
            message_key="passes.errors.pass_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    user_context = await _ensure_gate_access_for_pass(
        current_user=current_user,
        db_connection=db_connection,
        pass_row=pass_row,
        request=request,
    )
    service = PassVerificationService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.check_in(pass_id=pass_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="pass_events",
        requested_id=pass_id,
        description=f"Checked in visitor pass: {pass_id}",
        risk_level="medium",
        new_data=result,
    )
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
    user_context = await extract_user_context(current_user, db_connection, request=request)
    org_id = user_context.organization_id
    if not org_id:
        raise ForbiddenException(
            message_key="auth.errors.session_not_found",
            custom_code=CustomStatusCode.UNAUTHORIZED,
        )
    pass_row = await PassesRepository(db_connection).get_by_id(
        organization_id=org_id,
        pass_id=pass_id,
    )
    if not pass_row:
        raise NotFoundException(
            message_key="passes.errors.pass_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    user_context = await _ensure_gate_access_for_pass(
        current_user=current_user,
        db_connection=db_connection,
        pass_row=pass_row,
        request=request,
    )
    service = PassVerificationService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.check_out(pass_id=pass_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="pass_events",
        requested_id=pass_id,
        description=f"Checked out visitor pass: {pass_id}",
        risk_level="medium",
        new_data=result,
    )
    return success_response(
        request=request,
        message_key="passes.success.checked_out",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
